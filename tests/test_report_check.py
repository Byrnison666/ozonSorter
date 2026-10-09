"""Проверка отчёта до импорта (ExcelParser.check_report).

Ошибка — файл не загружается: пустой или чужой отчёт стал бы «последним» и
опустошил бы «Отгрузку» и «Залежавшиеся». Предупреждение — незнакомый статус:
загрузить можно, но оператор подтверждает.
"""
import unittest

from src.parser import ExcelParser


def _row(posting, status=None):
    row = {
        "posting_number": posting,
        "ozon_client_id": ExcelParser.extract_ozon_client_id(posting),
    }
    row["is_kty"] = row["ozon_client_id"] is None
    if status is not None:
        row["status"] = status
    return row


class ReportCheckTest(unittest.TestCase):
    def test_good_report_passes(self):
        rows = [_row("111-0001-1", "Готово к выдаче"),
                _row("111-0001-2", " отправить на склад "),
                _row("111-0001-3", "Вернуть продавцу"),
                _row("111-0001-4", "Разместить на Ozon Полку"),
                _row("111-0001-5", "")]
        self.assertEqual(ExcelParser.check_report(rows), ([], []))

    def test_short_format_without_status_passes(self):
        self.assertEqual(ExcelParser.check_report([_row("111-0001-1")]), ([], []))

    def test_empty_report_is_error(self):
        errors, warnings = ExcelParser.check_report([])
        self.assertEqual(len(errors), 1)
        self.assertIn("ни одной посылки", errors[0])
        self.assertEqual(warnings, [])

    def test_mostly_unrecognized_numbers_is_error(self):
        rows = [_row("111-0001-1"), _row("ii123"), _row("ii456")]
        errors, _ = ExcelParser.check_report(rows)
        self.assertEqual(len(errors), 1)
        self.assertIn("У 2 из 3 строк", errors[0])

    def test_half_unrecognized_numbers_passes(self):
        rows = [_row("111-0001-1"), _row("ii123")]
        self.assertEqual(ExcelParser.check_report(rows), ([], []))

    def test_unknown_status_is_warning_with_counts(self):
        rows = [_row("111-0001-1", "Готово к выдаче"),
                _row("111-0001-2", "Новый статус"),
                _row("111-0001-3", "Ещё один"),
                _row("111-0001-4", " Новый статус ")]
        errors, warnings = ExcelParser.check_report(rows)
        self.assertEqual(errors, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("«Новый статус» — 2 шт.\n«Ещё один» — 1 шт.", warnings[0])


if __name__ == "__main__":
    unittest.main()
