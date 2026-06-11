# TaskBot Telegram — Session State

## Project
Telegram-бот для управления задачами с ролями (админ, модератор, пользователь), подтверждением выполнения и уведомлениями.
Интеграция с Yougile (https://ru.yougile.com) — двусторонняя синхронизация задач.

## Current State
- Bot running with PID from start.sh
- All implemented features are stable
- Yougile integration active

## How to start a new session

1. Read this file first (AGENTS.md)
2. Check if bot is running: `ps aux | grep "python main.py"`
3. If not running, start with: `bash start.sh` (или `python main.py &`)
4. Bot log is in bot.log in project root

## Structure

```
├── main.py              # Entry point (bot + webhook server)
├── config.py            # .env config
├── bot/
│   ├── handlers.py      # All command/button handlers
│   ├── messages.py      # Message templates
│   └── keyboards.py     # Inline keyboards
├── db/
│   ├── schema.sql       # DDL (users, tasks with yougile fields)
│   ├── crud.py          # DB operations
│   └── __init__.py      # SQLite connection (WAL mode)
├── scheduler/
│   └── tasks.py         # Overdue check via JobQueue (60s)
├── utils/
│   └── date_parser.py   # Russian date parser
├── yougile/
│   ├── __init__.py
│   ├── .env             # Yougile credentials
│   ├── config.py        # Reads YOUGILE_* from .env
│   ├── client.py        # Yougile API client (httpx)
│   └── webhook.py       # HTTP server for Yougile → Telegram sync
├── .env                 # BOT_TOKEN, ADMIN_ID, YOUGILE_*
├── tasks.db             # SQLite DB
└── docs/spec.md         # Tech spec
```

## Yougile Integration

### Flow (Telegram → Yougile)
1. **Создание задачи**: `/add` → выбор проекта (из Yougile) → исполнитель → название/описание/дедлайн → задача создаётся в Yougile на доске **«Задачи»**
2. **Взял в работу**: `/take <id>` → задача в Yougile перемещается на доску **«В работе»**
3. **Выполнено**: `/done <id>` → задача в Yougile → **«На проверке»**, добавляется комментарий с результатом
4. **Подтверждение**: admin approve → задача в Yougile → **«Готово»**, `completed=true`
5. **Отклонение**: admin reject → задача в Yougile → **«В работе»**, добавляется комментарий

### Flow (Yougile → Telegram)
- Yougile присылает webhook на `http://server:8787/webhook`
- Обрабатываются события: task-created, task-moved, task-renamed, task-updated (completed)

### Доски в проекте Yougile (обязательны)
| Название доски | Назначение |
|---|---|
| Задачи | Новые задачи из бота |
| В работе | Исполнитель взял задачу |
| На проверке | Ожидает подтверждения |
| Готово | Выполнено |

## Key files & line numbers

### yougile/client.py
- `get_board_mapping(project_id)` — находит доски по названиям
- `create_task_in_project(...)` — создаёт задачу в доске «Задачи»
- `move_to_board(task_id, project_id, board_name)` — перемещает между досками
- `send_message(task_id, text)` — комментарий к задаче
- `set_completed(task_id)` — отметить выполненной

### yougile/webhook.py
- `WebhookServer` — HTTP сервер на порту 8787
- `handle_webhook_event` — обработка событий Yougile

### bot/handlers.py
- `PROJECT = 4` — новое состояние разговора
- `add_start:111` — загружает проекты, переход к _ask_project
- `project_callback:152` — выбор проекта
- `_finish_task:320` — создание задачи в Yougile
- `take_task_handler:446` — /take команда
- `_sync_yougile_*` — синхронизация с Yougile
- `setup_webhooks_handler:453` — создание webhook-ов в Yougile
- `get_handlers:967` — регистрация всех handler-ов

## Commands
| Command | Who | Description |
|---------|-----|-------------|
| /add | All | Create task (project → assignee → title → desc → deadline) |
| /take <id> | All | Take task into work (→ «В работе») |
| /done <id> | All | Mark done / send to review |
| /delete <id> | All | Delete task |
| /list | All | My tasks |
| /overdue | All | Overdue tasks |
| /pending | Admin+Mod | Tasks awaiting approval |
| /setup_webhooks <url> | Admin | Create Yougile webhooks |
| /users | Admin | List users |
| /promote /demote /removeuser | Admin | User management |
| /cancel | All | Cancel conversation |
| /help | All | Help |

## To start integration after first setup
1. Run bot: `python main.py`
2. In Telegram, run: `/setup_webhooks https://your-ngrok-url.ngrok-free.app`
3. Bot now syncs bidirectionally with Yougile

## Known issues
- Yougile file upload not yet implemented (only text comments)
- Webhook server requires public URL (ngrok in dev)
