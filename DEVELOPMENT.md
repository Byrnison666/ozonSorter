# OzonSorter — заметки для разработки

Десктоп для сортировки посылок Ozon. Исходник восстановлен из PyInstaller-сборки
`OzonSorter_v1.1_premium_2026-05-16` (онедир-бандл вёз `.py` целиком, декомпиляция не
понадобилась). Бинарный рантайм (`.dll`/`.pyd`) не переносился — пересоберётся при упаковке.

## Стек
- Python **3.14** (рантайм оригинальной сборки)
- PySide6 (Qt6 GUI), SQLAlchemy 2.x (ORM поверх SQLite), openpyxl (Excel I/O)

## Структура
```
src/
  main.py            точка входа (QApplication, тема, MainWindow)
  database.py        DatabaseManager, путь к SQLite
  models.py          ORM-модели (SQLAlchemy Base)
  parser.py          разбор входного Excel-отчёта
  services.py        бизнес-логика сортировки/фильтрации
  export_service.py  выгрузка результатов в Excel
  backup.py          ротация бэкапов БД (хранит последние 30)
  ui/                экраны Qt: dashboard, clients, on_point,
                     route_assignment, issue, main_window, theme(QSS)
assets/              icon.png / icon.ico
```

## Запуск (dev)
Корень импортов — папка проекта (родитель `src/`). Запускать как модуль:
```
python -m src.main
```
Прямой `python src/main.py` упадёт: код мешает абсолютные (`from src...`) и
относительные (`from .models`) импорты — работает только из корня пакета.

```
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m src.main
```

## Где лежат данные
`database.py` кладёт БД в `%APPDATA%/OzonSorter/ozon_sorter.db`. На Linux `APPDATA`
не задан → fallback в `~/OzonSorter/`. Бэкапы — `<APP_DATA_DIR>/backups/*.db.bak`.

## Сборка .exe (как в оригинале)
PyInstaller onedir. Ориентировочно:
```
pyinstaller --noconsole --name OzonSorter \
  --icon assets/icon.ico \
  --add-data "assets:assets" \
  src/main.py
```
Точные опили оригинальной сборки неизвестны (`.spec` в бандл не попал) — при первой
сборке сверь результат с оригинальной раскладкой `_internal/`.

## Грабли
- Несогласованный стиль импортов (см. выше) — при рефакторинге привести к одному.
- В исходнике зашиты доменные строки («Казаков 68», «Комсомольская 4», «Кольцевая 16»)
  — вероятно, есть и другие хардкоды адресов/клиентов. Искать перед изменением логики.
- Точные версии зависимостей не зафиксированы (см. requirements.txt).
