# OzonSorter — заметки для разработки

Десктоп для сортировки посылок Ozon. Исходник восстановлен из PyInstaller-сборки
`OzonSorter_v1.1_premium_2026-05-16` (онедир-бандл вёз `.py` целиком, декомпиляция не
понадобилась). Бинарный рантайм (`.dll`/`.pyd`) не переносился — пересоберётся при упаковке.

## Стек
Целевая платформа — **Windows 8.1**, поэтому стек понижен с оригинального
(PySide6/Python 3.14 — не запускается на Win8.x):
- Python **3.10** (последний с колёсами PySide2 5.15.2.1; на 8.1 формально
  работает до 3.12, но PySide2 кончается на 3.10)
- PySide2 (Qt5 GUI — Qt6 требует Win10), SQLAlchemy 2.x (ORM/SQLite), openpyxl (Excel I/O)

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

### Linux dev-окружение (зеркало Win8.1)
Системный Python 3.12 не годится (нет колёс PySide2). Используется portable
CPython 3.10 от python-build-standalone (с вшитыми sqlite/ssl, без sudo):
- интерпретатор: `~/.local/python310/bin/python3.10`
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

## Сборка .exe (под Windows 8.1)
**Кросс-сборки нет.** PyInstaller замораживает ОС, на которой запущен — на Linux
получится ELF, не `.exe`. Собирать ТОЛЬКО на Windows.

Точка входа — `app.py` в корне (тонкий launcher для `src.main`, нужен из-за
абсолютных импортов `from src...`). Сборка описана в `OzonSorter.spec` (onedir,
как оригинал; assets кладутся в `_internal/assets`, куда смотрит main_window.py).

На Windows-машине (лучше прямо на целевой Win8.1 — гарантия совместимости):
```bat
:: Python 3.10 x64 (под Win8.1; разрядность должна совпадать с целевой машиной)
py -3.10 -m venv venv
venv\Scripts\activate
pip install -r requirements.txt pyinstaller
pyinstaller OzonSorter.spec
:: результат: dist\OzonSorter\OzonSorter.exe  (+ папка _internal)
```
PyInstaller официально поддерживает Windows 8.1+ и Python 3.10 — отдельная старая
версия не нужна. Если целевая Win8.1 32-битная — ставить Python 3.10 **x86**.

## Грабли
- Несогласованный стиль импортов (см. выше) — при рефакторинге привести к одному.
- В исходнике зашиты доменные строки («Казаков 68», «Комсомольская 4», «Кольцевая 16»)
  — вероятно, есть и другие хардкоды адресов/клиентов. Искать перед изменением логики.
- Точные версии зависимостей не зафиксированы (см. requirements.txt).
- Косметика под Qt5: на дашборде в карточке импорта текст-описание перекрывается
  кнопкой «Выбрать файл…» (наезд по вертикали). Похоже на фикс. высоту карточки или
  spacing лейбла в QSS/layout — поправить в dashboard_screen.py / theme.py.
