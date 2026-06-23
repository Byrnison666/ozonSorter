"""Парсер отчёта Ozon: новый 3-колоночный формат и старый 9-колоночный.

Регрессия: Ozon убрал колонки «Тип» и «Статус» (отчёт стал 3-колоночным), из-за
чего распознавание шапки падало. Обязательны только «Номер отправления» и «Ячейка».
"""
import os
import tempfile
import unittest

import openpyxl

from src.parser import ExcelParser


def _xlsx(rows):
    """rows — список списков; пишем как есть (с учётом пустых строк-отступов)."""
    path = tempfile.mktemp(suffix=".xlsx")
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


class ParserFormatsTest(unittest.TestCase):
    def setUp(self):
        self.parser = ExcelParser()
        self._files = []

    def tearDown(self):
        for f in self._files:
            if os.path.exists(f):
                os.remove(f)

    def _parse(self, rows):
        path = _xlsx(rows)
        self._files.append(path)
        return self.parser.parse_file(path)

    def test_new_three_column_format(self):
        # Реальный новый отчёт: 5 пустых строк, шапка на 6-й, 3 колонки.
        rows = [
            [], [], [], [], [],
            ["Этикетка\nНазвание", "Номер отправления", "Ячейка"],
            ["0147012251-0295-1\nПередний стабилизатор", "0147012251-0295-1", "На проверку-3"],
            ["40330930-0763-1\nФутболка", "40330930-0763-1", "На проверку-1"],
        ]
        result = self._parse(rows)
        self.assertEqual(len(result), 2)
        first = result[0]
        self.assertEqual(first["posting_number"], "0147012251-0295-1")
        self.assertEqual(first["cell"], "На проверку-3")
        self.assertEqual(first["product_label"], "0147012251-0295-1")
        self.assertEqual(first["product_name"], "Передний стабилизатор")
        self.assertEqual(first["ozon_client_id"], "0147012251")
        self.assertFalse(first["is_kty"])
        self.assertFalse(first["is_damaged"])  # нет колонки Тип — повреждение неизвестно

    def test_old_nine_column_format_still_works(self):
        rows = [
            [], [], [], [], [],
            ["", "Этикетка\nНазвание", "Номер отправления", "Тип", "Статус", "Ячейка",
             "Отсчётная дата отправки", "Перевозка", "Контейнер\nШтрихкод"],
            ["", "ii\nНоски", "0107174712-0101-4", "Отправление", "Отправить на склад",
             "235-1", "", "", ""],
            ["", "x\nЧистящее", "0146744646-0115-2", "Отправление\nПовреждено",
             "Отправить на склад", "На проверку-1", "", "", ""],
        ]
        result = self._parse(rows)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["ozon_type"] if "ozon_type" in result[0] else result[0].get("type"),
                         "Отправление")
        self.assertFalse(result[0]["is_damaged"])
        self.assertTrue(result[1]["is_damaged"])  # «Повреждено» в Тип

    def test_kty_when_no_client_id_prefix(self):
        rows = [
            ["Этикетка\nНазвание", "Номер отправления", "Ячейка"],
            ["штрихкод\nТовар", "ii17574018168", "77-1"],  # нет цифрового префикса -> КТЯ
        ]
        result = self._parse(rows)
        self.assertTrue(result[0]["is_kty"])
        self.assertIsNone(result[0]["ozon_client_id"])

    def test_missing_required_cell_column_raises(self):
        rows = [
            ["Этикетка\nНазвание", "Номер отправления"],  # нет «Ячейка»
            ["x\ny", "0147012251-0295-1"],
        ]
        with self.assertRaises(ValueError):
            self._parse(rows)


if __name__ == "__main__":
    unittest.main()
