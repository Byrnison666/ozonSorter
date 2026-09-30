"""Версия схемы базы и список допустимых таблиц синхронизации не должны
расходиться с моделями: устройства отвергают базу «из будущего» и базу с
посторонними таблицами.
"""
import hashlib
import unittest

from src.database import SCHEMA_VERSION
from src.models import Base
from src.sync_service import _ALLOWED_TABLES

# Отпечаток схемы (таблицы, колонки, типы, ограничения) для текущей SCHEMA_VERSION.
# Менялись модели — поднимите SCHEMA_VERSION в src/database.py, внесите миграцию в
# DatabaseManager._migrate и обновите отпечаток здесь. Так версия схемы не
# отстаёт от моделей, а Android-приложение получает чёткий сигнал об изменении.
EXPECTED_FINGERPRINTS = {
    1: "e180ec1d6c3d1c68",
}


def schema_fingerprint() -> str:
    parts = []
    for table in sorted(Base.metadata.sorted_tables, key=lambda t: t.name):
        parts.append(f"table {table.name}")
        for column in table.columns:
            parts.append(
                f"  {column.name} {type(column.type).__name__} null={column.nullable} "
                f"pk={column.primary_key} fk={sorted(fk.target_fullname for fk in column.foreign_keys)}"
            )
        for constraint in sorted(
                table.constraints,
                key=lambda c: (type(c).__name__, repr(c.name), sorted(col.name for col in c.columns))):
            parts.append(f"  constraint {type(constraint).__name__} "
                         f"{sorted(c.name for c in constraint.columns)}")
        for index in sorted(table.indexes, key=lambda i: i.name):
            parts.append(f"  index {index.name} {[c.name for c in index.columns]}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


class SchemaVersionTest(unittest.TestCase):
    def test_schema_fingerprint_matches_declared_version(self):
        expected = EXPECTED_FINGERPRINTS.get(SCHEMA_VERSION)
        self.assertIsNotNone(expected, "для SCHEMA_VERSION нет отпечатка схемы")
        self.assertEqual(
            schema_fingerprint(), expected,
            "Модели изменились: поднимите SCHEMA_VERSION, добавьте миграцию и обновите отпечаток",
        )

    def test_every_model_table_is_allowed_for_sync(self):
        for name in Base.metadata.tables:
            self.assertIn(name, _ALLOWED_TABLES)

    def test_sqlite_service_tables_are_allowed(self):
        self.assertIn("sqlite_sequence", _ALLOWED_TABLES)


if __name__ == "__main__":
    unittest.main()
