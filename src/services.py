import json
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import select
from .duplicate_service import DUPLICATE_DAYS, is_incomplete, is_issued
from .models import (
    Client, Shipment, ImportSession, AssignmentStatus, DeliveryPoint
)
from .parser import ExcelParser

class ImportService:
    def __init__(self, db_session: Session):
        self.session = db_session
        self.parser = ExcelParser()
        # Выданных посылок, которые снова пришли после неполного прошлого отчёта
        # и не стали дублями (итог последнего process_import).
        self.skipped_duplicates = 0

    def find_duplicate_import(self, file_path: str) -> Optional[ImportSession]:
        """Сессия, в которой этот файл (по sha256) уже импортировали, либо None.
        Нужна, чтобы предупредить о повторной загрузке того же отчёта."""
        file_hash = self.parser.get_file_sha256(file_path)
        return self.session.execute(
            select(ImportSession).where(ImportSession.source_file_sha256 == file_hash)
        ).scalar_one_or_none()

    def process_import(self, file_path: str, rows: Optional[List[Dict[str, Any]]] = None) -> ImportSession:
        file_hash = self.parser.get_file_sha256(file_path)
        
        # Check if already imported
        existing_session = self.session.execute(
            select(ImportSession).where(ImportSession.source_file_sha256 == file_hash)
        ).scalar_one_or_none()
        
        # Note: Caller should handle warning if existing_session is found
        
        import_session = ImportSession(
            source_file_name=file_path.split('/')[-1],
            source_file_sha256=file_hash,
            started_at=datetime.now()
        )
        self.session.add(import_session)
        self.session.flush() # Get ID
        
        # rows передаёт экран импорта: он уже разобрал файл для проверки
        # (check_report) — не читаем его второй раз.
        if rows is None:
            rows = self.parser.parse_file(file_path)
        import_session.total_rows = len(rows)
        
        logs = []
        
        for row in rows:
            import_session.total_rows += 1 # This is already len(rows), maybe count in parser?
            # Re-calculating correctly:
            pass
        
        # Reset counters for actual count
        import_session.total_rows = len(rows)
        import_session.kty_rows = 0
        import_session.matched_rows = 0
        import_session.new_to_ship_rows = 0
        import_session.already_on_point = 0
        import_session.returned_rows = 0
        import_session.not_ours_rows = 0
        import_session.errors_rows = 0

        # Индекс активных клиентов по нормализованному Ozon ID (без ведущих нулей).
        # Так совпадают «224933356» в базе и «0224933356» в отчёте. setdefault —
        # при коллизии одинаковых номеров берём первого (это один и тот же клиент).
        clients_by_norm = {}
        for c in self.session.execute(
            select(Client).where(Client.is_active == True)
        ).scalars().all():
            clients_by_norm.setdefault(self.parser.normalize_ozon_id(c.ozon_client_id), c)

        # Ключи (номер отправления + штрихкод) из этого файла, уже обработанные в
        # текущем импорте. Отчёты Ozon нередко содержат дубли строк — вторую
        # встречу пропускаем, иначе при ещё не сброшенном INSERT упадёт UNIQUE.
        # Разные посылки одного клиента в одной ячейке различаются штрихкодом, а
        # не только номером, поэтому ключ составной.
        seen_in_file = set()

        # Дубль — выданная посылка, которой не было в прошлом отчёте, а теперь она
        # снова пришла. Если прошлый отчёт неполный, пропуск только в нём не в счёт.
        previous_id, before_previous_id = self._previous_sessions(import_session.id)
        lost, kept = self._missing_in_previous(rows, previous_id, before_previous_id)
        previous_incomplete = is_incomplete(lost, kept)
        self.skipped_duplicates = 0

        for row_data in rows:
            posting_number = row_data['posting_number']
            product_label = row_data['product_label']
            ozon_client_id = row_data['ozon_client_id']

            key = (posting_number, product_label)
            if key in seen_in_file:
                continue
            seen_in_file.add(key)

            # Посылка могла встречаться в прошлых импортах — тогда не вставляем
            # заново (UNIQUE posting_number+product_label), а только двигаем
            # last_seen. Тот же номер, но другой штрихкод — физически другой товар:
            # existing не найдётся, создастся новая запись.
            existing_shipment = self.session.execute(
                select(Shipment).where(
                    Shipment.posting_number == posting_number,
                    Shipment.product_label == product_label,
                )
            ).scalar_one_or_none()

            if row_data['is_kty']:
                import_session.kty_rows += 1
                if existing_shipment:
                    self._touch(existing_shipment, import_session.id, row_data)
                else:
                    self._create_shipment(row_data, import_session.id, AssignmentStatus.EXCLUDED_KTY)
                continue

            # Find client (сравнение по числовому значению id, без ведущих нулей)
            client = clients_by_norm.get(self.parser.normalize_ozon_id(ozon_client_id))

            if not client:
                import_session.not_ours_rows += 1
                if existing_shipment:
                    self._touch(existing_shipment, import_session.id, row_data)
                else:
                    self._create_shipment(row_data, import_session.id, AssignmentStatus.EXCLUDED_NOT_OURS)
                continue

            import_session.matched_rows += 1
            is_ready = self.parser.is_ready_for_pickup(
                row_data.get('status'), row_data.get('cell')
            )

            if existing_shipment:
                # До _touch: он перезапишет отметку последнего появления, по
                # которой виден пропуск.
                reappeared = False
                if (is_ready and is_issued(existing_shipment)
                        and (existing_shipment.last_seen_import_session_id or 0) < previous_id):
                    if (previous_incomplete
                            and existing_shipment.last_seen_import_session_id == before_previous_id):
                        self.skipped_duplicates += 1
                    else:
                        reappeared = True
                previous_seen_at = existing_shipment.last_seen_at

                self._touch(existing_shipment, import_session.id, row_data)
                if existing_shipment.assignment_status == AssignmentStatus.EXCLUDED_NOT_OURS:
                    # Клиента могли добавить уже после первой встречи посылки.
                    # Повторный импорт должен переклассифицировать сохранённую
                    # запись, иначе она навсегда останется «не нашей» и не
                    # попадёт в экспорт даже при успешном матчинге клиента.
                    existing_shipment.client_id = client.id
                    existing_shipment.assigned_point = client.fixed_delivery_point
                    existing_shipment.ozon_status = row_data.get('status')
                    if is_ready:
                        existing_shipment.assignment_status = AssignmentStatus.TO_SHIP
                        existing_shipment.exported_import_session_id = None
                        import_session.new_to_ship_rows += 1
                    else:
                        existing_shipment.assignment_status = AssignmentStatus.RETURNED
                        import_session.returned_rows += 1
                elif not is_ready:
                    # Возврат: снять с отгрузки. Уже привезённые/на точке не трогаем
                    # — их мы физически забрали раньше, склад их назад не заберёт.
                    if existing_shipment.assignment_status in (
                        AssignmentStatus.TO_SHIP, AssignmentStatus.TO_ASSIGN
                    ):
                        existing_shipment.assignment_status = AssignmentStatus.RETURNED
                    existing_shipment.ozon_status = row_data.get('status')
                    import_session.returned_rows += 1
                elif reappeared:
                    # Выданная посылка снова пришла: тот же номер, штрихкод и клиент.
                    days = (import_session.started_at.date() - previous_seen_at.date()).days
                    existing_shipment.assigned_point = client.fixed_delivery_point
                    existing_shipment.ozon_status = row_data.get('status')
                    existing_shipment.first_seen_at = existing_shipment.last_seen_at
                    if days <= DUPLICATE_DAYS:
                        # До решения оператора в отгрузку не идёт.
                        existing_shipment.assignment_status = AssignmentStatus.DUPLICATE
                        existing_shipment.delivered_at = previous_seen_at
                        logs.append(f"DUPLICATE: Shipment {posting_number} was issued and reappeared in import.")
                    else:
                        # Давно выдана — это уже новая посылка.
                        existing_shipment.assignment_status = AssignmentStatus.TO_SHIP
                        existing_shipment.exported_import_session_id = None
                        existing_shipment.delivered_at = None
                        import_session.new_to_ship_rows += 1
                        logs.append(f"Shipment {posting_number} reappeared {days} days after issue, shipping as new.")
                elif existing_shipment.assignment_status == AssignmentStatus.ON_POINT:
                    import_session.already_on_point += 1
                    logs.append(f"Shipment {posting_number} already on point.")
                elif existing_shipment.assignment_status == AssignmentStatus.DELIVERED:
                    logs.append(f"WARNING: Shipment {posting_number} marked as DELIVERED but seen again in import.")
                elif existing_shipment.assignment_status == AssignmentStatus.RETURNED:
                    # Раньше была возвратом, в отчёте снова «Готово к выдаче» —
                    # вернуть в отгрузку на точку клиента. Сбрасываем отметку
                    # выгрузки: посылку мы так и не забрали, она снова доступна и
                    # обязана попасть в отчёт отгрузки.
                    existing_shipment.assignment_status = AssignmentStatus.TO_SHIP
                    existing_shipment.assigned_point = client.fixed_delivery_point
                    existing_shipment.ozon_status = row_data.get('status')
                    existing_shipment.exported_import_session_id = None
                    import_session.new_to_ship_rows += 1
                else:
                    # Ещё не привезённая посылка (TO_SHIP/TO_ASSIGN) или дубль без
                    # решения: синхронизируем точку с актуальной у клиента — если
                    # её сменили, посылка не должна уехать на старую точку.
                    existing_shipment.assigned_point = client.fixed_delivery_point
            elif is_ready:
                # Новая посылка, готова к выдаче: к отгрузке на точку клиента.
                self._create_shipment(
                    row_data,
                    import_session.id,
                    AssignmentStatus.TO_SHIP,
                    client_id=client.id,
                    assigned_point=client.fixed_delivery_point,
                )
                import_session.new_to_ship_rows += 1
            else:
                # Новая посылка нашего клиента, но это возврат — фиксируем (видна в
                # «Контроле»), в отгрузку не ставим.
                self._create_shipment(
                    row_data,
                    import_session.id,
                    AssignmentStatus.RETURNED,
                    client_id=client.id,
                    assigned_point=client.fixed_delivery_point,
                )
                import_session.returned_rows += 1
                
        if previous_incomplete:
            logs.append(
                f"WARNING: Previous import looks incomplete: {lost} of {lost + kept} shipments missing. "
                f"Not treated as duplicates: {self.skipped_duplicates}."
            )
        import_session.finished_at = datetime.now()
        import_session.log_json = json.dumps(logs)
        self.session.commit()
        return import_session

    def _previous_sessions(self, session_id: int) -> Tuple[int, Optional[int]]:
        """(id прошлого импорта, id позапрошлого); 0 и None — таких нет."""
        ids = self.session.execute(
            select(ImportSession.id)
            .where(ImportSession.id < session_id)
            .order_by(ImportSession.id.desc())
            .limit(2)
        ).scalars().all()
        return (ids[0] if ids else 0), (ids[1] if len(ids) > 1 else None)

    def _missing_in_previous(self, rows: List[Dict[str, Any]], previous_id: int,
                             before_previous_id: Optional[int]) -> Tuple[int, int]:
        """(lost, kept) по уникальным посылкам текущего отчёта, включая чужие и
        КТЯ: lost — были в позапрошлом отчёте, но не в прошлом; kept — были в
        прошлом. Много lost — прошлый отчёт пришёл неполным."""
        if before_previous_id is None:
            return 0, 0
        last_seen = {
            (posting_number, product_label): session_id
            for posting_number, product_label, session_id in self.session.execute(
                select(Shipment.posting_number, Shipment.product_label,
                       Shipment.last_seen_import_session_id)
                .where(Shipment.last_seen_import_session_id.in_((previous_id, before_previous_id)))
            ).all()
        }
        keys = {(row['posting_number'], row['product_label']) for row in rows}
        seen = [last_seen.get(key) for key in keys]
        return seen.count(before_previous_id), seen.count(previous_id)

    def _touch(self, shipment: Shipment, session_id: int, row_data: Dict[str, Any]):
        """Отметить, что посылка встречена в текущем импорте (без смены статуса)."""
        shipment.last_seen_at = datetime.now()
        shipment.last_seen_import_session_id = session_id
        # Склад мог переложить посылку в другую ячейку — берём актуальную из отчёта.
        # Пустое (в т.ч. из одних пробелов) значение не затирает известную ячейку.
        cell = row_data.get('cell')
        if cell is not None and str(cell).strip():
            shipment.cell = cell

    def _create_shipment(self, row_data: Dict[str, Any], session_id: int, status: AssignmentStatus, client_id: Optional[int] = None, assigned_point: Optional[DeliveryPoint] = None):
        shipment = Shipment(
            posting_number=row_data['posting_number'],
            client_id=client_id,
            ozon_client_id_raw=row_data['ozon_client_id'] or "",
            product_label=row_data.get('product_label'),
            product_name=row_data.get('product_name'),
            ozon_type=row_data.get('type'),
            ozon_status=row_data.get('status'),
            cell=row_data.get('cell'),
            shipment_date_ozon=row_data.get('shipment_date_ozon'),
            is_damaged=row_data.get('is_damaged', False),
            is_kty=row_data.get('is_kty', False),
            assignment_status=status,
            assigned_point=assigned_point,
            import_session_id=session_id,
            last_seen_import_session_id=session_id,
            first_seen_at=datetime.now(),
            last_seen_at=datetime.now()
        )
        self.session.add(shipment)
