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
- **Статус готовности.** `ExcelParser.is_ready_for_pickup`: только «Готово к выдаче» → `TO_SHIP`; «Отправить на склад»/«Вернуть продавцу»/«Разместить на Ozon Полку» → возврат (`RETURNED`), в отгрузку не идёт. Без статуса (короткий 3-колоночный формат) возврат определяется только по ячейке «На проверку-*»/«Возврат-*» (`RETURN_CELL_PREFIXES`), остальное = готова.
- **Проверка отчёта до импорта.** `ExcelParser.check_report` (телефон — `checkReport`): 0 строк или больше половины КТЯ → ошибка, файл не грузится (иначе он стал бы «последним отчётом» и опустошил «Отгрузку»/«Залежавшиеся»); статус вне `KNOWN_STATUSES` → предупреждение с вопросом.
- **«Скоро возврат».** `StaleShipmentService.return_alert`: не-возвраты из последнего отчёта от `ALERT_DAYS` (10), из них от `RETURN_DAYS` (14) — Ozon хранит ~14 дней. Показ: после импорта и плашка на «Главной» → «Залежавшиеся» с фильтром 10. Даты начала хранения в отчёте нет — дни от `first_seen_at`, могут быть занижены.
- **SheetJS и `<dimension>`.** Генератор отчётов Ozon оставляет в `<dimension>` размер шаблона (`B1:J6`); openpyxl его игнорирует, SheetJS обрезает `!ref` → 0 строк. `mobile/src/core/sheet.ts` расширяет диапазон по ключам ячеек.
- **Дедуп между отчётами.** `Shipment.exported_import_session_id`: `generate_export` берёт `TO_SHIP` с `last_seen==текущая` и (`exported_import_session_id IS NULL` или `==текущая`), затем помечает. «Остатки на складе» не выгружаются повторно.
- **Дубли** (`src/duplicate_service.py`, телефон — `src/core/duplicates.ts`). Выгруженная посылка (`exported_import_session_id` есть), которой не было в прошлом отчёте, снова пришла не позже `DUPLICATE_DAYS` (30) от `last_seen_at` → `DUPLICATE`, `delivered_at` = прошлый `last_seen_at`. `DELIVERED` (решение «Ошибка Ozon»), который пропадал из отчёта, тоже снова становится дублем. Позже 30 дней → новая посылка (`TO_SHIP`, `exported=NULL`). До решения оператора («Отгрузить» → `TO_SHIP`, «Ошибка Ozon» → `DELIVERED`) в отгрузку и «Залежавшиеся» не идёт. Ложный дубль от неполного отчёта: если посылки не было только в прошлом отчёте, а он неполный (`is_incomplete`: выпало ≥5 и ≥5% посылок позапрошлого), дублем не считается — `skipped_duplicates` + WARNING в журнале. Пороги стоит откалибровать на настоящих отчётах.
- **Версия схемы** — `SCHEMA_VERSION` в `PRAGMA user_version` (сейчас 2, с дублями). ПК поднимает её при открытии базы, телефон схему существующей базы не трогает — версию поднимает его импорт. Базу новее себя ни одно устройство не открывает и с Диска не берёт → устройства обновлять вместе.
- **Формат экспорта «Отгрузка <ТОЧКА>»** (заказчик): A=описание (`product_name`), B=номер, C=штрихкод, D=ячейка, E=повреждение. Столбца «Статус» быть НЕ должно.

## Оформление (с 1.10.0 / телефон 1.2.0)

Дизайн-система «Ink & Porcelain» (макеты в Figma): палитра и стили — `src/ui/theme.py` и `mobile/src/ui/theme.ts`, держать их согласованными. Шрифт Onest (`assets/fonts`, `mobile/assets/fonts`, OFL), иконки Lucide на ПК (`assets/icons`), Feather на телефоне. Иконки приложений генерирует `tools/make_icons.py`.

- QSS `min-height`/`max-height` перекрывают `setFixedHeight`/`setMinimumHeight` из кода — высоты задавать в QSS.
- Onest Medium/SemiBold под GDI — отдельные семейства (`FONT_MEDIUM`/`FONT_SEMIBOLD`), не font-weight.
- Телефон: Onest — XML-семейство плагина expo-font в `app.json` (нужен prebuild), экраны берут `Text`/`TextInput` из `src/ui/components.tsx`, а не из react-native. `tabular-nums` у Onest рисует «11» как «1 1».

## Проверка

- Dev: запуск по команде выше, прогон тестов (`.venv/bin/python -m unittest discover -s tests`).
- `src/database.py`, `src/models.py`, `src/services.py`, `src/export_service.py` — с окончаниями CRLF (в `services.py` есть и LF-строки). Правки скриптом — сохранять окончания, иначе diff на весь файл.
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

- Обмен целым файлом SQLite через **REST API Яндекс.Диска** (`src/yadisk_api.py`, `mobile/src/core/sync/yadisk.ts`), слияния нет — работа по очереди; при правках с двух сторон — выбор версии. WebDAV (до ПК 1.9.0 / телефона 1.1.0) брошен: Яндекс искусственно режет по нему выгрузку. REST тоже режет файлы `.gz`, поэтому файл > 64 КБ выгружается как `.tmp` и переименовывается на сервере.
- **Доступ — только папка приложения** (`app:/` = «Приложения/OzonSorter»), вход по коду устройства (OAuth device flow, `src/yandex_oauth.py`, `mobile/src/core/sync/yandexOAuth.ts`), токен год, обновление за 30 дней. Секрет приложения Яндекс OAuth **не в git**: ПК — `src/_yandex_secret.py` (`CLIENT_SECRET = "..."`, hiddenimport в spec; для сборки скопировать в `C:\build\OzonSorter\src\`), телефон — `mobile/.env.local` (`EXPO_PUBLIC_YANDEX_CLIENT_SECRET=...`, вшивается при сборке). Без секрета вход падает с «не настроен вход». Значение — Obsidian `Claude Code/Secrets/`.
- Переход со старых версий: состояние синхронизации переносится, только если логин WebDAV совпал с логином OAuth (`adopt_legacy_state`). Базу с `disk:/OzonSorter/` в «Приложения/OzonSorter/» переносят вручную один раз.
- **Защита базы на Диске от стирания** (требование владельца, на обоих устройствах): пустая база не выкладывается; устройство с `base_rev == 0` не заменяет базу на Диске; ежедневные копии `backups/` (копированием на стороне Диска), 30 дней. Правки синхронизации не должны снимать эти рубежи.
- **Работа без Диска** (с ПК 1.11.1 / телефона 1.3.1): «недоступен» = нет связи, 5xx кроме 507, 429, неудачная операция Диска (`is_unavailable` / `isUnavailable`). Чтение (GET) при 5xx/429 повторяется до 2 раз (1 и 2 с), запись — никогда. Автоматические попытки (после правки, запуск, фон, закрытие) при недоступности молчат: только отметка (ПК — боковая панель, телефон — плашка на «Главной») и пауза 10 минут; ручная кнопка показывает окно «работать можно». 401/403, 507, TLS — по-прежнему окном. Телефон не начинает фоновую выгрузку, пока открыт выбор файла или «Поделиться» (`external`), а импорт и «Отгрузка» ждут идущую (`dbReady`). С ПК 1.12.0 / телефона 1.4.0 недоступностью считается и испорченный ответ: 2xx не того вида (`is_bad_response` / `isBadResponse`), битые `meta.json`, архив или сумма (`RemoteDataError`). Правки во время фоновой выгрузки уходят обменом сразу после неё, но не при открытом `external` (`syncAfterBackground`): «Отгрузка» ждёт там между файлом и `commitExport`, замена базы обменом оставила бы отметку не в той базе.
- **Локальные копии** (с ПК 1.12.0 / телефона 1.4.0) — страховка от сбоев Диска, независимо от него. ПК: `src/backup.py` при запуске (до окна) — backup API SQLite во временный файл, `quick_check`, пустая база (нет `clients`/`import_sessions`/`shipments`) не копируется, ротация 30 только после удачной копии. backup API CPython при SQLITE_BUSY повторяет шаг бесконечно — предел `LOCK_TIMEOUT_S` через `progress`-колбэк, иначе вторая копия программы посреди записи вешала бы запуск. Телефон: `mobile/src/core/localBackup.ts`, экран «Резервные копии» из «Настроек». Копия дня — `VACUUM INTO` из открытого соединения в `backups/daily/` (30); снимок фоновой выгрузки — побайтный (VACUUM меняет сумму, база казалась бы изменённой). Восстановление (копия, файл, в т.ч. `.db.bak` с ПК) = как загрузка с Диска: `checkBackupFile` (целостность, версия, таблицы, колонки — `missingColumns`, данные; база без версии — до ПК 1.8.0 — отказ: телефон не мигрирует, она могла не пройти миграции ПК) → `backups/presync` → замена → открытие, не открылась — прежняя на место; состояние синхронизации не трогается, следующий обмен выложит базу.
- Тестовый Диск — `tests/yadisk_fake.py` (REST + OAuth + info); телефон гоняет его через `mobile/tests/py/yadisk_server.py`. Сквозная проверка: эмулятор + `yadisk_server.py` + `tests/e2e/adb_ui.py` (uiautomator). На настоящем Диске — только в отдельной подпапке, не в корне папки приложения (там боевая база).
