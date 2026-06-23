"""Массовый импорт клиентов из xlsx.

Формат: одна строка-заголовок (Ozon ID, ФИО, Телефон, Точка) и строки данных.
Невалидная строка не валит весь файл — собираем ошибки с номерами строк.
Существующий клиент (по Ozon ID) обновляется значениями из файла.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import openpyxl
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Client, DeliveryPoint


# Логическое имя колонки -> допустимые заголовки (нормализованные: lower, без пробелов).
_HEADER_ALIASES = {
    "ozon_client_id": {"ozonid", "id", "озонid", "озонид"},
    "full_name": {"фио", "имя", "name"},
    "phone": {"телефон", "phone", "тел"},
    "point": {"точка", "точкаповыдаче", "точкапоумолчанию", "point"},
}

_POINT_ALIASES = {
    "комсомольская4": DeliveryPoint.KOMSOMOLSKAYA_4,
    "комсомольская": DeliveryPoint.KOMSOMOLSKAYA_4,
    "кольцевая16": DeliveryPoint.KOLTSEVAYA_16,
    "кольцевая": DeliveryPoint.KOLTSEVAYA_16,
}

_REQUIRED_COLUMNS = ("ozon_client_id", "point")

TEMPLATE_HEADERS = ["Ozon ID", "ФИО", "Телефон", "Точка"]


@dataclass
class ImportResult:
    added: int = 0
    updated: int = 0
    data_rows: int = 0
    errors: List[Tuple[int, str]] = field(default_factory=list)


def _norm(value) -> str:
    return str(value).strip().lower().replace(" ", "").replace("\n", "") if value is not None else ""


def _cell_to_str(value) -> str:
    """openpyxl может вернуть число для ID/телефона — приводим без .0."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    return str(value).strip()


class ClientImportService:
    def __init__(self, db_session: Session):
        self.session = db_session

    @staticmethod
    def write_template(path: str) -> None:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Клиенты"
        ws.append(TEMPLATE_HEADERS)
        ws.append(["0224933356", "Иванов И.И.", "", "Комсомольская 4"])
        ws.append(["0301234567", "Петров П.П.", "", "Кольцевая 16"])
        widths = [16, 22, 16, 20]
        for idx, w in enumerate(widths, 1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(idx)].width = w
        wb.save(path)

    def import_clients(self, file_path: str) -> ImportResult:
        wb = openpyxl.load_workbook(file_path, data_only=True)
        ws = wb.active

        header_row_idx, col_map = self._find_header(ws)
        if header_row_idx is None:
            raise ValueError(
                "Не найден заголовок. Обязательные колонки: «Ozon ID» и «Точка». "
                "Скачайте шаблон и заполните его."
            )

        result = ImportResult()
        for row_idx, row in enumerate(
            ws.iter_rows(min_row=header_row_idx + 1, values_only=True),
            start=header_row_idx + 1,
        ):
            raw = {name: row[idx] for name, idx in col_map.items() if idx < len(row)}
            if not _cell_to_str(raw.get("ozon_client_id")):
                continue  # пустая строка — молча пропускаем

            result.data_rows += 1
            parsed, err = self._parse_row(raw)
            if err:
                result.errors.append((row_idx, err))
                continue

            existing = self.session.execute(
                select(Client).where(Client.ozon_client_id == parsed["ozon_client_id"])
            ).scalar_one_or_none()

            if existing:
                existing.full_name = parsed["full_name"]
                existing.phone = parsed["phone"]
                existing.fixed_delivery_point = parsed["point"]
                existing.is_active = True
                result.updated += 1
            else:
                self.session.add(Client(
                    ozon_client_id=parsed["ozon_client_id"],
                    full_name=parsed["full_name"],
                    phone=parsed["phone"],
                    fixed_delivery_point=parsed["point"],
                ))
                result.added += 1

        self.session.commit()
        return result

    def _parse_row(self, raw: Dict) -> Tuple[Optional[Dict], Optional[str]]:
        ozon_id = _cell_to_str(raw.get("ozon_client_id"))
        if not ozon_id.isdigit():
            return None, f"Ozon ID «{ozon_id}» — только цифры, без дефисов и букв"

        point_raw = _norm(raw.get("point"))
        if not point_raw:
            return None, "Не указана точка («Комсомольская 4» или «Кольцевая 16»)"
        point = _POINT_ALIASES.get(point_raw)
        if point is None:
            return None, (
                f"Точка «{raw.get('point')}» — допустимо «Комсомольская 4» "
                f"или «Кольцевая 16»"
            )

        return {
            "ozon_client_id": ozon_id,
            "full_name": _cell_to_str(raw.get("full_name")) or None,
            "phone": _cell_to_str(raw.get("phone")) or None,
            "point": point,
        }, None

    def _find_header(self, ws) -> Tuple[Optional[int], Dict[str, int]]:
        for row_idx, row in enumerate(ws.iter_rows(max_row=10, values_only=True), 1):
            normalized = [_norm(c) for c in row]
            col_map: Dict[str, int] = {}
            for col_idx, cell in enumerate(normalized):
                if not cell:
                    continue
                for logical, aliases in _HEADER_ALIASES.items():
                    if cell in aliases and logical not in col_map:
                        col_map[logical] = col_idx
            if all(req in col_map for req in _REQUIRED_COLUMNS):
                return row_idx, col_map
        return None, {}
