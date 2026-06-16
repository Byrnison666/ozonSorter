# OzonSorter — заметки для разработки

Десктоп для сортировки посылок Ozon. Исходник восстановлен из PyInstaller-сборки
`OzonSorter_v1.1_premium_2026-05-16` (онедир-бандл вёз `.py` целиком, декомпиляция не
понадобилась). Бинарный рантайм (`.dll`/`.pyd`) не переносился — пересоберётся при упаковке.

## Стек
Целевая платформа — **Windows 8.0**, поэтому стек понижен с оригинального
(PySide6/Python 3.14 — не запускается на Win8):
- Python **3.8** (последний с поддержкой Windows 8.0; 3.9+ требуют 8.1)
- PySide2 (Qt5 GUI — Qt6 не работает на Win8), SQLAlchemy 2.x (ORM/SQLite), openpyxl (Excel I/O)

Порт PySide6→PySide2 был минимальным: импорты + `.exec()`→`.exec_()`. Enum'ы и
прочий API совпали. См. ветку `win8-port`.

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

### Linux dev-окружение (зеркало Win8.0)
Системный Python 3.12 не годится (нет колёс PySide2). Используется portable
CPython 3.8.20 от python-build-standalone (с вшитыми sqlite/ssl, без sudo):
- интерпретатор: `~/.local/python38/bin/python3.8`
- venv проекта: `.venv` (создан этим интерпретатором)

PySide2 на Ubuntu 24.04 не стартует из-за отсутствия `libxcb-xinerama.so.0`
(её нет в системе, ставится `sudo apt install libxcb-xinerama0`). Без sudo
библиотека извлечена из .deb в `~/.local/qtlibs`. Запуск GUI:
```
DISPLAY=:1 LD_LIBRARY_PATH="$HOME/.local/qtlibs:$LD_LIBRARY_PATH" .venv/bin/python -m src.main
```
Smoke-тест без дисплея: `QT_QPA_PLATFORM=offscreen .venv/bin/python -c '...'`
(собрать MainWindow и выйти по таймеру — main.py сам блокируется в exec_).

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
- Косметика под Qt5: на дашборде в карточке импорта текст-описание перекрывается
  кнопкой «Выбрать файл…» (наезд по вертикали). Похоже на фикс. высоту карточки или
  spacing лейбла в QSS/layout — поправить в dashboard_screen.py / theme.py.
