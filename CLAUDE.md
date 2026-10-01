# CLAUDE.md — OzonSorter

Десктоп-приложение для сортировки посылок Ozon на ПВЗ: импорт Excel-отчётов, матчинг клиентов, экспорт «Отгрузки». **PySide2 (Qt5) + SQLAlchemy/SQLite + openpyxl.**

- Git: remote `origin` = `Byrnison666/ozonSorter`, ветка **master**. Исходник: `~/it/OzonSorter`.
- **Целевая платформа — Windows 8.1.** Один кроссплатформенный исходник; продукт — Windows `.exe`, Linux только для dev/теста.
- Стек: **Python 3.10** + **PySide2 5.15.2.1** (Qt6/PySide6 требуют Win10; 3.10 — последний с колёсами PySide2). Версия в UI — футер `main_window.py`.

## Запуск на Linux (dev)

Системный Python 3.12 не годится (нет колёс PySide2). Нужен portable CPython 3.10:

```bash
cd ~/it/OzonSorter && DISPLAY=:1 \
  LD_LIBRARY_PATH="$HOME/.local/qtlibs:$LD_LIBRARY_PATH" \
  .venv/bin/python -m src.main
```

- Интерпретатор: `~/.local/python310/bin/python3.10` (python-build-standalone), venv `~/it/OzonSorter/.venv`.
- Нужна `libxcb-xinerama.so.0` (нет в Ubuntu 24.04, извлечена в `~/.local/qtlibs`).
- **Точка входа только `python -m src.main` из корня** (смешаны `from src...` и `from .models`).

## Сборка .exe (Wine)

Нативно на Linux нельзя — только Windows или Wine. Префикс `~/.wine-ozon` (win64), Windows-Python 3.10.11 в `C:\Python310`, проект в `C:\build\OzonSorter`.

```bash
WINEPREFIX=~/.wine-ozon wine C:\\Python310\\python.exe -m PyInstaller OzonSorter.spec
```

Два критичных грабля Wine:
1. **PyInstaller и любой Wine-Python — только в FOREGROUND.** Detached/фон → `init_sys_streams: Invalid handle`. Это же ломает запуск собранного exe под Wine — гонять с таймаутом в foreground.
2. **pip ставить явным списком пакетов, не `-r requirements.txt`** — под Wine pip читает файл в cp1252 и падает на кириллице в комментариях (UnicodeDecodeError).

Поставка: папка `OzonSorter/` (exe + `_internal` ~104M + README) + одноимённый `.zip` в `~/Загрузки/`.

## Ключевая доменная логика (не сломать)

- **Нормализация Ozon-id.** Ozon в отчёте добавляет ведущий ноль (`0224933356`), в базе клиент часто без него (`224933356`). Матчинг — по нормализованному id (`ExcelParser.normalize_ozon_id`, `lstrip` нулей) с обеих сторон, иначе теряется ~90% посылок. Хранимый `ozon_client_id` всегда нормализован; миграция-дедуп в `DatabaseManager._migrate` схлопывает дубли по норм-id (survivor = `min(id)`, посылки перецепляются).
- **Идентичность посылки — пара `(posting_number, product_label)`**, не один номер. В одну ячейку кладут разные посылки одного клиента; Ozon повторяет номер для другого товара. `product_label` fallback = номер отправления, если этикетка пуста (NULL в UNIQUE считается различным → дубли). Миграция `shipments` — rebuild таблицы (SQLite не меняет UNIQUE через ALTER), идемпотентна.
- **Статус готовности.** `ExcelParser.is_ready_for_pickup`: только «Готово к выдаче» → `TO_SHIP`; «Отправить на склад»/«Вернуть продавцу» → возврат (`RETURNED`), в отгрузку не идёт. Пустой/отсутствующий статус = готова (совместимость со старым 3-колоночным форматом).
- **Дедуп между отчётами.** `Shipment.exported_import_session_id`: `generate_export` берёт `TO_SHIP` с `last_seen==текущая` и (`exported_import_session_id IS NULL` или `==текущая`), затем помечает. «Остатки на складе» не выгружаются повторно.
- **Формат экспорта «Отгрузка <ТОЧКА>»** (заказчик): A=описание (`product_name`), B=номер, C=штрихкод, D=ячейка, E=повреждение. Столбца «Статус» быть НЕ должно.

## Проверка

- Dev: запуск по команде выше, прогон тестов (`.venv/bin/python -m unittest discover -s tests`).
- Перед отдачей заказчику — проверить на реальной Win8.1 (Wine ≠ Windows: файловые диалоги + openpyxl). Разовые миграции БД тестировать на копии боевой базы.

## Android-приложение (`mobile/`)

Полная копия ПК на **Expo 54 + TypeScript** (`com.ozonsorter.app`): импорт отчёта, клиенты, «Отгрузка», залежавшиеся, синхронизация через Яндекс.Диск. Зависимости — `npm` (`package-lock.json`, ставить через `npx expo install`, после — `npx expo-doctor`); тесты — `bun test`.

- Логика — `mobile/src/core/` (зависит только от синхронного интерфейса `Db`: телефон — expo-sqlite, тесты — bun:sqlite); платформа — `src/platform/`; экраны — `src/screens/`, `src/app/`.
- **Телефон повторяет ПК байт в байт** (формат DateTime SQLAlchemy, `json.dumps`, `str()` Python, схема = DDL свежей базы ПК). Иначе база «меняется» при открытии другим устройством. Любая правка доменной логики на ПК → такая же в `src/core/` + эталонный тест.
- **Эталонные тесты** (`mobile/tests/*.test.ts`) запускают настоящий Python-код ПК (`tests/support/pc.ts`, скрипты `tests/py/`, venv `../.venv`) и сравнивают результат и таблицы базы. Настоящий отчёт — только обезличенный (`tests/fixtures/`): **репозиторий публичный**.

```bash
cd mobile && bun test && npx tsc --noEmit
```

### Сборка APK

```bash
cd mobile && npx expo prebuild --platform android --no-install
echo "sdk.dir=$HOME/Android/Sdk" > android/local.properties
cd android && NODE_ENV=production ./gradlew assembleRelease
```

`android/` не в git. Подпись release — config-плагин `plugins/withReleaseSigning.js`, ключ `~/keystores/ozonsorter-release.{jks,credentials}` (копии: приватный репо KEYS, Obsidian Secrets). Без ключа release падает, а не подписывается debug-ключом. **Потеря ключа = нельзя обновить установленное приложение.**

### Синхронизация (ПК и телефон)

- Обмен целым файлом SQLite через WebDAV, слияния нет — работа по очереди; при правках с двух сторон — выбор версии.
- **Защита базы на Диске от стирания** (требование владельца, на обоих устройствах): пустая база не выкладывается; устройство с `base_rev == 0` не заменяет базу на Диске; ежедневные копии `OzonSorter/backups/`, 30 дней. Правки синхронизации не должны снимать эти рубежи.
- Сквозная проверка: эмулятор + `tests/py/webdav_server.py` + `tests/e2e/adb_ui.py` (uiautomator). На настоящем Диске — только в отдельной папке, не в боевой `OzonSorter/`.
