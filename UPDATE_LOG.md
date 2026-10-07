# Sayuri Yukishiro — журнал обновлений

Журнал ведётся по docs/UPDATE_PROTOCOL.md. ID никогда не сбрасываются и не переиспользуются.

---

## v0.1.0

Дата: 2026-10-07

Статус: completed

Изменения:

- CHG-0001 / ARCH-0001 — заложено разделение слоёв: лаунчер, системное ядро, хранилище, локальный web shell; будущие модули получают отдельные БД.
- CHG-0002 / FEAT-0001 — добавлены переносимые пути без привязки к букве диска и политика журнала SQLite по типу носителя: WAL на локальном диске, DELETE на съёмных, сетевых и синхронизируемых путях.
- CHG-0003 / FEAT-0002 — добавлена центральная база ядра с объявленным списком миграций, отказом при базе новее сборки и явным циклом open -> transaction -> close.
- CHG-0004 / FEAT-0003 — добавлены реестр служб и управляемый жизненный цикл с откатом уже запущенных служб при сбое запуска.
- CHG-0005 / FEAT-0004 — добавлены единая конфигурация, структурированный журнал с ротацией и шина событий с изоляцией ошибок обработчиков.
- CHG-0006 / FEAT-0005 — добавлен диспетчер фоновых задач с ограниченной историей, освобождением futures и фиксацией прерванных задач после перезапуска.
- CHG-0007 / FEAT-0006 — добавлены долговечные контрольные точки с обязательным next_action и восстановление без автоматического повторения действий.
- CHG-0008 / FEAT-0007 — добавлен CoreAPI как единственный интерфейс ядра для будущих модулей.
- CHG-0009 / FEAT-0008 — добавлен локальный HTTP-сервер только на петлевом интерфейсе с автовыбором свободного порта и мягкой остановкой по токену.
- CHG-0010 / FEAT-0009 — добавлен файл точки доступа data/runtime/endpoint.json с атомарной записью и защита от второго запуска с того же носителя.
- CHG-0011 / FEAT-0010 — добавлено автоматическое открытие сайта после того, как порт реально принимает соединения.
- CHG-0012 / FEAT-0011 — добавлен локальный web shell с состоянием ядра, служб, модулей и событий.
- CHG-0013 / FEAT-0012 — добавлена предстартовая диагностика: Python, носитель, хранилище, интерфейс, база, ядро, браузер, экземпляр, порт, Git.
- CHG-0014 / FEAT-0013 — добавлен единственный пользовательский вход «Sayuri Yukishiro.bat» и scripts/launcher.ps1: порядок поиска Python от носителя к системе, резервное хранилище при носителе только для чтения, открытие сайта уже запущенного экземпляра.
- CHG-0015 / FEAT-0014 — введён обязательный протокол обновлений с машинным реестром ID и валидатором scripts/validate_update_protocol.py.
- CHG-0016 / FEAT-0015 — добавлен GitHub Actions Foundation Smoke на Windows и Linux с проверкой синтаксиса PowerShell.

Проверки:

- tests: PASS (56 тестов)
- lint: NOT_CONFIGURED
- type-check: NOT_CONFIGURED
- smoke-test: PASS
- protocol-validation: PASS
- python-compile: PASS
- powershell-syntax: NOT_APPLICABLE (проверяется в CI на windows-latest)
- live-http-api: PASS (/, /api/health, /api/system, автовыбор порта, токен-остановка)

Commit:

2f940426043970a78108a406595cab7f5658b018

Следующий шаг:

next_action: v0.2.0 — Module Runtime: манифест module.json, обнаружение модулей, граф зависимостей, разрешения, миграции модульных БД, health и управляемый запуск/остановка.

---

## v0.1.1

Дата: 2026-10-07

Статус: completed

Изменения:

- CHG-0017 / FEAT-0016 — добавлен машинно-читаемый вывод диагностики --preflight --format json, чтобы лаунчер рисовал статусы сам, а не разбирал текст.
- CHG-0018 / IMP-0001 — введён единый визуальный язык статусов в src/sayuri_yukishiro/console.py: значки состояний, цвета, выравненные колонки, разделители и панели для диагностики, запуска и лаунчера.
- CHG-0019 / IMP-0002 — добавлен ASCII-фолбэк для консолей без Unicode: значки и рамки деградируют до ASCII по способности кодировки потока, принудительно через -Ascii или SAYURI_ASCII.
- CHG-0020 / IMP-0003 — добавлены шаговые индикаторы этапов запуска, итоговая панель с адресом сайта и отдельная панель отказа при критических ошибках.
- CHG-0021 / IMP-0004 — текстовый отчёт Python получил те же значки; цвет включается только в терминале и уважает NO_COLOR; добавлено русское склонение числительных в сводке.
- CHG-0022 / IMP-0005 — добавлены тесты визуального контракта: полнота обоих наборов значков, чистота ASCII-вывода, прямоугольность панели, статусы из JSON, родные цвета PowerShell вместо ANSI.

Проверки:

- tests: PASS (89 тестов, из них 33 новых)
- lint: NOT_CONFIGURED
- type-check: NOT_CONFIGURED
- smoke-test: PASS
- protocol-validation: PASS
- python-compile: PASS
- powershell-syntax: NOT_APPLICABLE (проверяется в CI на windows-latest)
- unicode-and-ascii-output: PASS (оба режима проверены вживую)
- exit-code-on-fatal: PASS (код 1 при критических ошибках)

Commit:

c2bafc090bf4d528d1e9bfcbfdb9a606a3fe1215

Следующий шаг:

next_action: v0.2.0 — Module Runtime: манифест module.json, обнаружение модулей, граф зависимостей, разрешения, миграции модульных БД, health и управляемый запуск/остановка.

---

## v0.2.0

Дата: 2026-10-07

Статус: completed

Изменения:

- CHG-0023 / FEAT-0017 — добавлены иконка приложения и генератор ICO без внешних зависимостей: шесть слоёв 16-64 px, 32 бита с альфа-каналом, детализация снежинки зависит от размера.
- CHG-0024 / FEAT-0018 — добавлен tray-хост на Windows Forms NotifyIcon с контекстным меню, открытием сайта по двойному клику и уведомлениями.
- CHG-0025 / FEAT-0019 — добавлено сворачивание окна консоли в область уведомлений через ShowWindow и возврат окна из меню трея.
- CHG-0026 / FEAT-0020 — ядро запускается отдельным скрытым процессом; лаунчер ждёт endpoint.json именно от своего pid.
- CHG-0027 / FEAT-0021 — выход из трея выполняет мягкую остановку по токену; принудительное завершение только после таймаута 15 секунд.
- CHG-0028 / FEAT-0022 — трей опрашивает /api/health по таймеру, обновляет подсказку с обрезкой до лимита 63 символа и уведомляет о смене состояния.
- CHG-0029 / FEAT-0023 — добавлен перезапуск ядра из меню трея.
- CHG-0030 / IMP-0006 — добавлены ключи -NoTray и -Tray, раздел tray в config/system.json и переменные SAYURI_TRAY, SAYURI_TRAY_HIDE_CONSOLE, SAYURI_TRAY_POLL_SECONDS.
- CHG-0031 / IMP-0007 — добавлена статическая проверка PowerShell scripts/check_powershell.py, тесты контракта трея и иконки, разделы README и ARCHITECTURE о режиме трея.

Проверки:

- tests: PASS (117 тестов, из них 28 новых)
- lint: NOT_CONFIGURED
- type-check: NOT_CONFIGURED
- smoke-test: PASS
- protocol-validation: PASS
- python-compile: PASS
- powershell-static: PASS (баланс скобок, порядок объявлений, автоматические переменные)
- powershell-syntax: NOT_APPLICABLE (полный парсер в CI на windows-latest)
- tray-scenario: PASS (фоновое ядро, опрос состояния, мягкая остановка за 1 с без Kill, снятие endpoint.json)
- stale-endpoint-cleanup: PASS (после жёсткого завершения ядро не считается живым)
- icon-reproducibility: PASS (генератор даёт тот же файл)

Commit:

931c8c1f0f2e8bacc255c3835a53b4dca1706693

Следующий шаг:

next_action: v0.3.0 — Module Runtime: манифест module.json, обнаружение модулей, граф зависимостей, разрешения, миграции модульных БД, health и управляемый запуск/остановка.

---

## v0.2.1

Дата: 2026-10-07

Статус: completed

Изменения:

- CHG-0032 / IMP-0008 — в порядок работы агента добавлено обязательное правило публикации: после финализации версии ветка разработки сливается в main только fast-forward и отправляется без отдельного запроса; там же зафиксировано ограничение среды на пуш tag-ссылок.

Проверки:

- tests: PASS (117 тестов)
- lint: NOT_CONFIGURED
- type-check: NOT_CONFIGURED
- smoke-test: PASS
- protocol-validation: PASS
- python-compile: PASS
- powershell-static: PASS

Commit:

6f2b25c7d700161fea3d72e8d6127927cbf0a76c

Следующий шаг:

next_action: v0.3.0 — Module Runtime: манифест module.json, обнаружение модулей, граф зависимостей, разрешения, миграции модульных БД, health и управляемый запуск/остановка.

---

## v0.3.0

Дата: 2026-10-07

Статус: completed

Изменения:

- CHG-0033 / ARCH-0002 — слой обновления выделен отдельным пакетом над системным ядром и подключён службой; схема центральной БД расширена до v4 журналом установок.
- CHG-0034 / FEAT-0024 — добавлена безопасная обёртка git: только чтение и fast-forward, с таймаутами; разрушающая операция разрешена единственным сценарием отката.
- CHG-0035 / FEAT-0025 — добавлен инвентарь восьми компонентов с версиями; у файлов без номера версии считается отпечаток содержимого.
- CHG-0036 / FEAT-0026 — добавлен разбор UPDATE_LOG.md в структурированную историю с группировкой изменений по типам ID.
- CHG-0037 / FEAT-0027 — добавлены восемь проверок готовности обновления; каждый отказ объясняет, что делать.
- CHG-0038 / FEAT-0028 — добавлена резервная копия базы средствами SQLite и файлов состояния с ротацией последних пяти.
- CHG-0039 / FEAT-0029 — добавлен план обновления: было и стало по компонентам, список коммитов и изменённых файлов.
- CHG-0040 / FEAT-0030 — добавлена установка шестью стадиями с событиями прогресса ядра.
- CHG-0041 / FEAT-0031 — добавлена проверка новым кодом отдельным процессом и автоматический откат при провале: файлы к прежнему коммиту, база из копии.
- CHG-0042 / FEAT-0032 — добавлен долговечный журнал установок обновлений в центральной БД.
- CHG-0043 / FEAT-0033 — добавлен локальный API обновления; изменяющие вызовы требуют токен и локального клиента, токен выдаётся странице через /api/session.
- CHG-0044 / FEAT-0034 — в интерфейсе добавлен раздел «Обновление проекта»: вкладки, проверки со значками, стадии установки с прогрессом.
- CHG-0045 / FEAT-0035 — добавлена визуализация «было и стало» по версиям компонентов.
- CHG-0046 / FEAT-0036 — добавлена история обновлений в интерфейсе: таймлайн версий с изменениями, проверками и коммитами.
- CHG-0047 / IMP-0009 — добавлены тесты слоя обновления на настоящем локальном репозитории, docs/UPDATE_SERVICE.md и шаги CI.

Проверки:

- tests: PASS (150 тестов, из них 33 новых)
- lint: NOT_CONFIGURED
- type-check: NOT_CONFIGURED
- smoke-test: PASS
- protocol-validation: PASS
- python-compile: PASS
- powershell-static: PASS
- update-api: PASS (отказ без токена и при непройденных проверках подтверждён)
- update-cycle: PASS (установка и откат проверены на локальном репозитории)

Commit:

057c15707aa1bd620a7bea941cd06a07ad1cffbe

Следующий шаг:

next_action: v0.4.0 — Module Runtime: манифест module.json, обнаружение модулей, граф зависимостей, разрешения, миграции модульных БД, health и управляемый запуск/остановка; версии модулей включить в инвентарь обновления.

---

## v0.3.1

Дата: 2026-10-07

Статус: completed

Изменения:

- CHG-0048 / BUG-0001 — Windows Foundation Smoke падал до выполнения тестов: системная `charmap`-кодировка runner не могла вывести кириллицу валидатора протокола.
- CHG-0049 / FIX-0001 -> BUG-0001 — валидатор переводит stdout/stderr в UTF-8 с безопасным fallback, а workflow явно включает UTF-8 режим Python на Windows и Linux.
- CHG-0050 / BUG-0002 — ошибки `git status`, `git log` и `git diff` могли превращаться в пустой результат и ошибочно выглядеть как чистое/пустое состояние.
- CHG-0051 / FIX-0002 -> BUG-0002 — критичные Git-чтения переведены в fail-closed через `require`; добавлен регрессионный тест, запрещающий маскировать ошибку Git как безопасное состояние.
- CHG-0052 / IMP-0010 — протокол релиза усилен обязательным зелёным CI до финализации и продвижения в main; валидатор теперь сверяет VERSION, PROJECT_STATE, README и версию ARCHITECTURE, документация синхронизирована.
- CHG-0053 / BUG-0003 — кроссплатформенный тест занятого порта жёстко использовал Linux errno 98 и поэтому ошибочно падал на Windows после устранения Unicode-блокера.
- CHG-0054 / FIX-0003 -> BUG-0003 — тесты bind_server переведены на платформенные константы errno.EADDRINUSE/errno.EADDRNOTAVAIL, сохранив проверку реальной логики автопорта.
- CHG-0055 / BUG-0004 — Windows workflow содержал PowerShell-строку `"$script: ..."`; двоеточие после имени переменной вызывало ParserError до проверки файлов launcher/tray.
- CHG-0056 / FIX-0004 -> BUG-0004 — интерполяция сообщения об ошибке исправлена на `${script}: ...`, чтобы PowerShell однозначно отделял имя переменной от двоеточия.
- CHG-0057 / BUG-0005 — после первого исправления второй такой же ParserError оставался в успешной ветке syntax-check: `"$script: синтаксис корректен"`.
- CHG-0058 / FIX-0005 -> BUG-0005 — успешное сообщение также переведено на `${script}: ...`; оба пути PowerShell syntax-check теперь синтаксически однозначны.
- CHG-0059 / BUG-0006 — Windows PowerShell 5.1 читает UTF-8 без BOM как системную ANSI-кодировку; launcher/tray с кириллицей и Unicode-глифами превращались в mojibake и launcher не парсился при реальном запуске через `powershell.exe`.
- CHG-0060 / FIX-0006 -> BUG-0006 — `launcher.ps1` и `tray.ps1` сохранены с UTF-8 BOM; добавлен регрессионный тест, фиксирующий требование кодировки для Windows PowerShell 5.1.

Проверки:

- tests: PASS (152 теста)
- lint: NOT_CONFIGURED
- type-check: NOT_CONFIGURED
- smoke-test: PASS
- protocol-validation: PASS
- python-compile: PASS
- powershell-static: PASS
- github-actions-windows: PASS (Foundation Smoke run #13)
- github-actions-linux: PASS (Foundation Smoke run #13)

Commit:

79941772b838a42eb83ac6ed3076af20c84e9d53

Следующий шаг:

next_action: v0.4.0 — Module Runtime: манифест module.json, обнаружение модулей, граф зависимостей, разрешения, миграции модульных БД, health и управляемый запуск/остановка; версии модулей включить в инвентарь обновления.

---

## v0.4.0

Дата: 2026-10-07

Статус: completed

Изменения:

- CHG-0061 / ARCH-0003 — Module Runtime выделен в отдельный слой `src/sayuri_yukishiro/modules/` и подключён как управляемая служба SystemCore между recovery и update service.
- CHG-0062 / FEAT-0037 — добавлен строгий `module.json`: schema version, безопасный id, SemVer, entrypoint внутри каталога, enabled, permissions, dependencies и декларативные DB migrations.
- CHG-0063 / FEAT-0038 — добавлено обнаружение модулей и dependency graph: обязательные зависимости проверяются по min_version, запускаются раньше dependants, отсутствующие/failed/disabled зависимости и циклы дают `blocked`.
- CHG-0064 / FEAT-0039 — добавлен `ModuleAPI` с capability permissions, namespaced events/jobs/checkpoints и запретом чтения чужих job id через штатный API.
- CHG-0065 / FEAT-0040 — добавлена отдельная `ModuleDatabase` для каждого модуля с последовательными транзакционными миграциями, защитой от базы новее manifest и той же политикой WAL/DELETE по типу носителя.
- CHG-0066 / FEAT-0041 — добавлен управляемый lifecycle модулей, health, degraded-состояние без падения SystemCore и ручной start/stop с запретом остановки используемой обязательной зависимости.
- CHG-0067 / FEAT-0042 — локальный API модулей расширен: `GET /api/modules`, tokenized `POST /api/modules/<id>/start` и `/stop`.
- CHG-0068 / FEAT-0043 — update inventory включает `module:<id>` и умеет видеть версии новых/удалённых модулей непосредственно на target Git ref до установки.
- CHG-0069 / IMP-0011 — добавлены конфигурация Module Runtime, документация границы безопасности и регрессионные тесты manifests, migrations, permissions, dependencies, lifecycle, API и update inventory.

Проверки:

- tests: PASS (166 тестов)
- lint: NOT_CONFIGURED
- type-check: NOT_CONFIGURED
- smoke-test: PASS
- protocol-validation: PASS
- python-compile: PASS
- powershell-static: PASS
- github-actions-windows: PASS (Foundation Smoke run #16)
- github-actions-linux: PASS (Foundation Smoke run #16)

Commit:

f0d05a8c7d1483dde3aa26cb8a149c6433dc15e2

Следующий шаг:

next_action: v0.5.0 — Cognitive Foundation: provider runtime, durable reasoning task context, planner/reasoning с evidence receipts и явными action boundaries поверх Module Runtime.

---

## v0.4.1

Дата: 2026-10-07

Статус: in_progress

Изменения:

- CHG-0070 / BUG-0007 — Python core писал `endpoint.json` в активный `SAYURI_DATA_DIR`, но launcher и tray жёстко искали его в `<project>/data/runtime`; при вынесенном или fallback-хранилище ядро запускалось, но оболочка считала запуск неудачным.
- CHG-0071 / FIX-0007 -> BUG-0007 — launcher теперь ждёт endpoint в runtime-каталоге выбранного `storage.Path`, передаёт тот же `DataDir` в tray, а tray закрепляет `SAYURI_DATA_DIR` и читает endpoint из того же каталога.
- CHG-0072 / BUG-0008 — старый `endpoint.json` считался живым только по PID; после переиспользования PID другим Windows-процессом новый запуск мог ложно завершаться как «Sayuri уже запущена».
- CHG-0073 / FIX-0008 -> BUG-0008 — `running_instance()` теперь подтверждает локальный `/api/health`, имя проекта и совпадение PID; неподтверждённый endpoint автоматически очищается.
- CHG-0074 / IMP-0012 — добавлены регрессионные тесты выбранного data/runtime пути, tray DataDir и восстановления после reused PID/stale endpoint.
- CHG-0075 / IMP-0013 — Windows Foundation Smoke дополнен реальным BAT-сценарием с вынесенным `SAYURI_DATA_DIR`: endpoint обязан появиться только в выбранном runtime, API ответить, а shutdown удалить endpoint из этого же каталога.

Проверки:

- tests: PENDING
- lint: NOT_CONFIGURED
- type-check: NOT_CONFIGURED
- smoke-test: PENDING
- protocol-validation: PENDING
- python-compile: PENDING
- powershell-static: PENDING
- github-actions-windows: PENDING
- github-actions-linux: PENDING

Commit:

PENDING

Следующий шаг:

next_action: проверить v0.4.1 Startup Recovery в Foundation Smoke; при зелёном Windows/Linux CI финализировать v0.4.1 и вернуть next_action к v0.5.0 Cognitive Foundation.
