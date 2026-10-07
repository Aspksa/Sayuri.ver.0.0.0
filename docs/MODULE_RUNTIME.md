# Module Runtime — v0.4.0

Module Runtime — управляемый слой расширений Sayuri Yukishiro. Он отделяет
прикладные возможности от системного ядра и даёт каждому модулю собственный
манифест, зависимости, capability-scoped API, SQLite-базу и жизненный цикл.

## Каталог модуля

Каждый модуль находится в `modules/<module_id>/` и обязан иметь
`module.json`. Имя каталога совпадает с `id`.

Минимальный пример:

```json
{
  "schema_version": 1,
  "id": "example",
  "name": "Example",
  "version": "1.0.0",
  "entrypoint": "module.py:Module",
  "enabled": true,
  "permissions": ["events.publish", "log.write"],
  "dependencies": [],
  "database": {
    "migrations": [
      {
        "version": 1,
        "description": "initial schema",
        "statements": [
          "CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
        ]
      }
    ]
  }
}
```

ID — только lowercase ASCII, цифры и underscore. Версия — строгая
`MAJOR.MINOR.PATCH`. Entry point обязан оставаться внутри каталога модуля.

## Entry point

Указанный Python-symbol вызывается с одним `ModuleContext`:

```python
class Module:
    def __init__(self, context):
        self.context = context

    def start(self):
        pass

    def stop(self):
        pass

    def health(self):
        return {"healthy": True, "detail": ""}
```

`start()` и `stop()` обязательны. `health()` необязателен.

## Зависимости

```json
{
  "id": "memory",
  "min_version": "1.2.0",
  "optional": false
}
```

Runtime запускает модули только после обязательных зависимостей. Отсутствующая,
слишком старая, disabled, failed или blocked зависимость блокирует зависимый
модуль. Цикл зависимостей блокируется, а ядро продолжает работать в degraded
состоянии.

Остановка контролируется в обратную сторону: нельзя остановить модуль, пока
работает другой обязательный модуль, который от него зависит.

## Permissions

Разрешённые capability:

- `core.status.read`
- `config.read`
- `events.publish`
- `events.subscribe`
- `jobs.submit`
- `jobs.read`
- `checkpoints.write`
- `recovery.read`
- `log.write`

Модуль получает `ModuleAPI`, а не `SystemCore`. События и job names
автоматически namespaced, checkpoints изолированы по module id.

### Граница безопасности

Это capability boundary, **не process sandbox**. Доверенный Python-код,
исполняемый in-process, технически может импортировать внутренние модули Python.
Недоверенные плагины в будущем должны запускаться в отдельном процессе с
отдельным IPC/broker policy. Module Runtime v0.4.0 не заявляет OS-level sandbox.

## База модуля

Каждый модуль получает отдельную SQLite-базу:

`data/modules/<module_id>.db`

Миграции перечислены в manifest, имеют последовательные версии `1..N` и
применяются транзакционно по порядку. Если база содержит неизвестную текущему
manifest миграцию, запуск модуля отклоняется вместо попытки downgrade.

Политика WAL/DELETE наследует правила носителя системного ядра.

## Состояния

Основные состояния модуля:

`discovered`, `disabled`, `running`, `stopped`, `blocked`, `failed`.

Ошибка одного модуля не уничтожает SystemCore: Module Runtime остаётся поднят,
но сообщает unhealthy, а общее состояние ядра становится degraded.

## API

- `GET /api/modules` — manifests, states, health, dependency graph, start order.
- `POST /api/modules/<module_id>/start` — ручной старт, локальный token required.
- `POST /api/modules/<module_id>/stop` — контролируемая остановка, token required.

## Обновления

`update.inventory` включает каждый manifest как компонент
`module:<module_id>`. План обновления умеет показать изменение версии,
удаление модуля и добавление нового модуля, которого ещё нет в текущем checkout.
