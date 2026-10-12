from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select
from .export_service import natural_key
from .models import Client, Shipment, AssignmentStatus, DeliveryPoint
from .stale_service import POINT_LABELS, StaleShipmentService

# Выданная посылка снова пришла в отчёте склада не позже стольких дней после
# выдачи — это дубль. Позже — новая посылка, идёт в отгрузку как обычно.
DUPLICATE_DAYS = 30

# Прошлый отчёт считаем неполным, если из него выпало не меньше INCOMPLETE_MIN
# посылок и не меньше INCOMPLETE_PERCENT % от тех, что есть и в текущем отчёте,
# и в позапрошлом. Пропуск только в таком отчёте выдачей не считается — иначе
# каждая выпавшая из него посылка, уже выгруженная в «Отгрузку», стала бы дублем.
INCOMPLETE_MIN = 5
INCOMPLETE_PERCENT = 5


def is_issued(shipment: Shipment) -> bool:
    """Посылку уже забрали со склада: она попала в выгрузку «Отгрузки», выдана
    или уже признана дублем."""
    status = shipment.assignment_status
    if status == AssignmentStatus.TO_SHIP:
        return shipment.exported_import_session_id is not None
    return status in (AssignmentStatus.DELIVERED, AssignmentStatus.DUPLICATE)


def is_incomplete(lost: int, kept: int) -> bool:
    """lost — посылок нет в прошлом отчёте, но они есть в позапрошлом и в текущем;
    kept — есть и в прошлом, и в текущем. Целочисленно, как на телефоне."""
    return lost >= INCOMPLETE_MIN and lost * 100 >= INCOMPLETE_PERCENT * (lost + kept)


def duplicates_alert_text(count: int) -> str:
    """Текст предупреждения о дублях (после импорта и на «Главной»)."""
    return (f"В отчёте снова пришли посылки, которые уже выдавали: {count}.\n"
            f"Тот же номер отправления и штрихкод, тот же клиент — это дубли. "
            f"В отгрузку они не попадут, пока вы не решите в разделе «Дубли»: "
            f"отгрузить или отметить как ошибку Ozon.")


def incomplete_report_text(skipped: int) -> str:
    """Предупреждение после импорта: прошлый отчёт был неполным."""
    return (f"Прошлый отчёт, похоже, был неполным: в нём не хватало многих посылок. "
            f"Выданных посылок, которых не было только в нём: {skipped} — "
            f"дублями их не считаю.")


@dataclass(frozen=True)
class DuplicateRow:
    shipment_id: int
    posting_number: str
    product_label: str
    product_name: str
    client_name: str
    ozon_client_id: str
    phone: str
    point: Optional[DeliveryPoint]
    cell: str
    # Последний отчёт, в котором посылка была до выдачи.
    delivered_at: Optional[datetime]
    # Отчёт, в котором она пришла снова.
    reappeared_at: datetime

    @property
    def point_label(self) -> str:
        return POINT_LABELS.get(self.point, "—")

    @property
    def delivered_label(self) -> str:
        return self.delivered_at.strftime("%d.%m.%Y") if self.delivered_at else "—"

    @property
    def reappeared_label(self) -> str:
        return self.reappeared_at.strftime("%d.%m.%Y")


class DuplicateService:
    def __init__(self, db_session: Session):
        self.session = db_session

    def list_duplicates(self) -> List[DuplicateRow]:
        """Дубли из последнего отчёта, ждущие решения оператора."""
        latest = StaleShipmentService(self.session).latest_import()
        if latest is None:
            return []

        stmt = (
            select(Shipment, Client)
            .outerjoin(Client, Shipment.client_id == Client.id)
            .where(
                Shipment.assignment_status == AssignmentStatus.DUPLICATE,
                Shipment.last_seen_import_session_id == latest.id,
            )
        )
        rows = [
            DuplicateRow(
                shipment_id=shipment.id,
                posting_number=shipment.posting_number or "",
                product_label=shipment.product_label or "",
                product_name=shipment.product_name or "",
                client_name=(client.full_name if client else None) or "",
                ozon_client_id=(client.ozon_client_id if client else None) or "",
                phone=(client.phone if client else None) or "",
                point=shipment.assigned_point,
                cell=str(shipment.cell or ""),
                delivered_at=shipment.delivered_at,
                reappeared_at=shipment.first_seen_at,
            )
            for shipment, client in self.session.execute(stmt).all()
        ]
        rows.sort(key=lambda r: (natural_key(r.cell), r.posting_number, r.product_label))
        return rows

    def ship(self, shipment_id: int) -> bool:
        """«Отгрузить»: дубль — настоящая новая посылка, в «Отгрузку» последнего
        отчёта. False — посылка уже не дубль (решили на другом устройстве)."""
        return self._decide(shipment_id, AssignmentStatus.TO_SHIP)

    def dismiss(self, shipment_id: int) -> bool:
        """«Ошибка Ozon»: посылка выдана, в отгрузку не идёт. Пропадёт из отчёта и
        вернётся в пределах DUPLICATE_DAYS — снова станет дублем."""
        return self._decide(shipment_id, AssignmentStatus.DELIVERED)

    def _decide(self, shipment_id: int, status: AssignmentStatus) -> bool:
        shipment = self.session.get(Shipment, shipment_id)
        if shipment is None or shipment.assignment_status != AssignmentStatus.DUPLICATE:
            return False
        shipment.assignment_status = status
        if status == AssignmentStatus.TO_SHIP:
            shipment.exported_import_session_id = None
        self.session.commit()
        return True
