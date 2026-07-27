import asyncio
import html as _html
import json
import logging
import os
import re as _re
import time
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from typing import Optional

from yougile.config import YOUGILE_WEBHOOK_SECRET
from yougile.client import YougileClient
from db.crud import get_task_by_yougile_id, add_task, get_user_by_yougile_id, update_task_field, update_task_status
from utils.date_parser import format_datetime_ru
from config import ADMIN_ID, SILENT_USERS

def _strip_html(text: str) -> str:
    """Remove HTML tags and unescape entities."""
    text = _re.sub(r"<[^>]+>", " ", text)
    text = _html.unescape(text)
    return " ".join(text.split())


logger = logging.getLogger(__name__)

_app_ref = None
_loop_ref = None

PENDING_FILE = os.path.join(os.path.dirname(__file__), "..", "yougile_pending.json")
PENDING_DELAY = 120  # seconds to wait before processing

# Deduplication cache for webhook events
_event_cache = {}  # key -> timestamp
EVENT_CACHE_TTL = 30  # seconds


def set_app(app):
    global _app_ref, _loop_ref
    _app_ref = app
    _loop_ref = None  # will be set by set_loop() once event loop is running


def set_loop(loop):
    global _loop_ref
    _loop_ref = loop


from bot.keyboards import task_actions_keyboard


def notify_chat(chat_id: int, text: str, task_id: int = 0, *, viewer_id: int = 0, assignee_id: int = 0):
    """Schedule a notification on the main event loop.
    If task_id is given, appends an inline action keyboard.
    Deduplicates recipients — each chat_id gets at most one message."""
    if _app_ref and _loop_ref:
        async def _send():
            try:
                reply_markup = task_actions_keyboard(task_id, "active", viewer_id=viewer_id, assignee_id=assignee_id) if task_id else None
                await _app_ref.bot.send_message(
                    chat_id=chat_id, text=text,
                    parse_mode="HTML", reply_markup=reply_markup,
                )
            except Exception as e:
                logger.warning("Failed to notify %s: %s", chat_id, e)
        asyncio.run_coroutine_threadsafe(_send(), _loop_ref)


def _notify_all(text: str, *chat_ids: int, task_id: int = 0, assignee_id: int = 0):
    """Notify multiple recipients, deduplicating by chat_id.
    Skips admin notification when task belongs to a silent user."""
    seen = set()
    for cid in chat_ids:
        if cid and cid not in seen:
            if cid == ADMIN_ID and assignee_id in SILENT_USERS:
                continue
            seen.add(cid)
            notify_chat(cid, text, task_id=task_id, viewer_id=cid, assignee_id=assignee_id)


def handle_webhook_event(event: str, payload: dict):
    task_id = payload.get("id", "")
    if not task_id:
        return

    # task-created doesn't exist in local DB yet, handle it differently
    if event == "task-created":
        _handle_task_created(task_id)
        return

    task = get_task_by_yougile_id(task_id)
    if not task:
        # Unknown task — maybe missed task-created, treat like one
        logger.info("Yougile task %s not found in local DB for event %s — treating as task-created", task_id, event)
        _handle_task_created(task_id)
        return

    user_id = task.get("assignee_id")
    creator_id = task.get("created_by")
    title = task.get("title", "")
    local_id = task.get("id", "")
    project_name = task.get("project_name", "")
    deadline_display = task.get("deadline", "не указан")
    assignee_name = task.get("assignee", "Не назначен")
    meta = f"📁 {project_name} | 👤 {assignee_name} | ⏰ {deadline_display}"

    if event == "task-moved":
        column_id = payload.get("columnId", "")
        dedup_key = f"moved:{task_id}:{column_id}"
        if _is_duplicate_event(dedup_key):
            logger.info("Skipping duplicate task-moved event for %s", task_id)
            return
        client = YougileClient()
        all_cols = client.get_columns_by_project(task.get("yougile_project_id", ""))
        col_name = next((c.get("title", "") for c in all_cols if c.get("id") == column_id), "другую колонку")

        # If task is already done, ignore side effects
        if task.get("status") == "done":
            logger.info("Task #%s already done, skipping move side effects", local_id)
            return

        if col_name == "Готово":
            update_task_status(local_id, "done")
            text = f"✅ <b>Задача #{local_id} выполнена</b>\n{title}\n{meta}"
        elif col_name == "В работе":
            update_task_status(local_id, "active")
            text = f"▶️ <b>Задача #{local_id} взята в работу</b>\n{title}\n{meta}"
        elif col_name == "На проверке":
            from datetime import timedelta
            new_dl = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
            update_task_field(local_id, "deadline", new_dl)
            text = f"🔍 <b>Задача #{local_id} на проверке</b>\n{title}\n{meta}\n⏰ Новый дедлайн: {new_dl}"
        else:
            text = f"🔄 <b>Задача #{local_id} перемещена</b>\n{title}\n{meta}\n→ {col_name}"

        _notify_all(text, user_id, creator_id, ADMIN_ID, task_id=local_id, assignee_id=user_id or 0)

    elif event == "task-updated":
        dedup_key = f"updated:{task_id}:{payload.get('completed')}:{payload.get('assigned', [])}"
        if _is_duplicate_event(dedup_key):
            logger.info("Skipping duplicate task-updated event for %s", task_id)
            return
        if payload.get("completed") is True:
            if task.get("status") != "done":
                update_task_status(local_id, "done")
            text = f"✅ <b>Задача #{local_id} выполнена в Yougile</b>\n{title}\n{meta}"
            _notify_all(text, user_id, creator_id, ADMIN_ID, task_id=local_id, assignee_id=user_id or 0)

    elif event == "task-renamed":
        new_title = payload.get("title", "")
        dedup_key = f"renamed:{task_id}:{new_title}"
        if _is_duplicate_event(dedup_key):
            logger.info("Skipping duplicate task-renamed event for %s", task_id)
            return
        text = f"✏️ <b>Задача #{local_id} переименована</b>\n{title} → {new_title}\n{meta}"
        _notify_all(text, user_id, creator_id, ADMIN_ID, task_id=local_id, assignee_id=user_id or 0)


def _load_pending() -> dict:
    try:
        with open(PENDING_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_pending(pending: dict):
    with open(PENDING_FILE, "w") as f:
        json.dump(pending, f)


def _is_duplicate_event(key: str) -> bool:
    now = time.time()
    # Clean old entries
    stale = [k for k, ts in list(_event_cache.items()) if now - ts > EVENT_CACHE_TTL]
    for k in stale:
        _event_cache.pop(k, None)
    if key in _event_cache:
        return True
    _event_cache[key] = now
    return False


def _add_pending(task_id: str):
    pending = _load_pending()
    pending[task_id] = time.time()
    _save_pending(pending)


def _remove_pending(task_id: str):
    pending = _load_pending()
    pending.pop(task_id, None)
    _save_pending(pending)


def check_pending_yougile_tasks():
    """Called on startup and periodically — processes pending tasks older than 2 min."""
    now = time.time()
    pending = _load_pending()
    for task_id, ts in list(pending.items()):
        if now - ts >= PENDING_DELAY:
            _process_delayed_task(task_id)


def _handle_task_created(task_id: str):
    """Schedule task sync after 2 minutes (allow time for full filling in Yougile)."""
    _add_pending(task_id)
    logger.info("Scheduled processing of Yougile task %s in %ss", task_id, PENDING_DELAY)


def _process_delayed_task(task_id: str):
    """Fetch task from Yougile after delay, create local task, notify all parties."""
    client = YougileClient()
    try:
        yougile_task = client.get_task(task_id)
    except Exception:
        logger.warning("Failed to fetch Yougile task %s (delayed)", task_id)
        _remove_pending(task_id)
        return

    # Check if already synced
    existing = get_task_by_yougile_id(task_id)
    if existing:
        logger.info("Yougile task %s already synced, skipping", task_id)
        _remove_pending(task_id)
        return

    yg_title = _strip_html(yougile_task.get("title", "") or "Без названия")
    yg_assigned = yougile_task.get("assigned", [])
    yg_author_id = yougile_task.get("authorId", "")
    yg_parent_id = yougile_task.get("parentId", "")
    yg_deadline_ms = None
    dl = yougile_task.get("deadline")
    if isinstance(dl, dict) and dl.get("deadline"):
        yg_deadline_ms = dl["deadline"]
    yg_column = yougile_task.get("columnId", "")

    # Find Telegram user by yougile assignee
    tg_assignee_id = None
    tg_assignee_name = "Не назначен"
    for yuid in yg_assigned:
        u = get_user_by_yougile_id(yuid)
        if u:
            tg_assignee_id = u["user_id"]
            tg_assignee_name = u.get("full_name") or u.get("username") or f"ID {u['user_id']}"
            break

    # Skip if no Telegram-linked assignee found
    if not tg_assignee_id:
        logger.info("Yougile task %s skipped — no linked Telegram user among assignees", task_id)
        # If it's a subtask, keep it pending for retry on next update
        if yg_parent_id:
            _add_pending(task_id)
        return

    # Find Telegram user who created the task in Yougile
    tg_author_id = None
    if yg_author_id:
        author = get_user_by_yougile_id(yg_author_id)
        if author:
            tg_author_id = author["user_id"]

    deadline_str = ""
    if yg_deadline_ms:
        try:
            dt = datetime.fromtimestamp(yg_deadline_ms / 1000)
            deadline_str = dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            deadline_str = ""

    # Determine project_id from column
    project_id = ""
    try:
        all_boards = client.get_boards()
        all_cols_data = client._request("GET", "columns")
        all_cols = all_cols_data if isinstance(all_cols_data, list) else all_cols_data.get("content", [])
        for c in all_cols:
            if c.get("id") == yg_column:
                board_id = c.get("boardId", "")
                for b in all_boards:
                    if b.get("id") == board_id:
                        project_id = b.get("projectId", "")
                        break
                break
    except Exception:
        pass

    # Get project name
    project_name = ""
    if project_id:
        try:
            p = client.get_project(project_id)
            project_name = _strip_html(p.get("title", ""))
        except Exception:
            pass

    # If subtask, get parent task title
    parent_title = ""
    if yg_parent_id:
        try:
            parent = client.get_task(yg_parent_id)
            parent_title = _strip_html(parent.get("title", "") or "")
        except Exception:
            pass

    # Create local task — store the actual Yougile author as created_by
    local_id = add_task(
        title=yg_title,
        assignee=tg_assignee_name,
        deadline=deadline_str,
        description=yougile_task.get("description", ""),
        assignee_id=tg_assignee_id,
        created_by=tg_author_id or ADMIN_ID,
        project_name=project_name,
    )
    update_task_field(local_id, "yougile_task_id", task_id)
    if project_id:
        update_task_field(local_id, "yougile_project_id", project_id)

    # Update Yougile task title to include bot task ID
    try:
        yg_current_title = yougile_task.get("title", "")
        if f"#{local_id}" not in yg_current_title:
            client._request("PUT", f"tasks/{task_id}", {"title": f"{yg_current_title} #{local_id}"})
    except Exception:
        pass

    deadline_display = format_datetime_ru(deadline_str) if deadline_str else "не указан"
    raw_desc = _strip_html(yougile_task.get("description", "") or "")
    desc_text = f"📋 {raw_desc}" if raw_desc else ""
    parent_line = f"📎 Родительская задача: {parent_title}\n" if parent_title else ""
    task_label = "📌 <b>Новая подзадача из Yougile</b>" if yg_parent_id else "📌 <b>Новая задача из Yougile</b>"
    text = (
        f"{task_label}\n"
        f"#{local_id} {yg_title}\n"
        f"{parent_line}"
        f"📁 {project_name}\n"
        f"👤 {tg_assignee_name}\n"
        f"⏰ {deadline_display}\n"
        f"{desc_text}"
    ).strip()

    targets = {tg_assignee_id}
    if tg_assignee_id not in SILENT_USERS:
        targets.add(ADMIN_ID)
    if tg_author_id and tg_author_id != tg_assignee_id and tg_author_id != ADMIN_ID:
        targets.add(tg_author_id)
    for tid in targets:
        notify_chat(tid, text, task_id=local_id, viewer_id=tid, assignee_id=tg_assignee_id)

    _remove_pending(task_id)


class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/webhook":
            self.send_response(404)
            self.end_headers()
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            return

        event = data.get("event", "")
        payload = data.get("payload", {})

        logger.info("Yougile webhook: %s", event)

        handle_webhook_event(event, payload)

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True}).encode())

    def log_message(self, format, *args):
        logger.info("Webhook: %s", format % args)


class WebhookServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 8787):
        self.host = host
        self.port = port
        self.server: Optional[HTTPServer] = None
        self.thread: Optional[Thread] = None

    def start(self):
        self.server = HTTPServer((self.host, self.port), WebhookHandler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        logger.info("Webhook server started on %s:%s", self.host, self.port)

    def stop(self):
        if self.server:
            self.server.shutdown()
