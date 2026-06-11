import json
import logging
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from typing import Optional

from yougile.config import YOUGILE_WEBHOOK_SECRET
from yougile.client import YougileClient
from db.crud import get_task_by_yougile_id, add_task, get_user_by_yougile_id, update_task_field
from config import ADMIN_ID

logger = logging.getLogger(__name__)

_app_ref = None


def set_app(app):
    global _app_ref
    _app_ref = app


async def _notify_chat(chat_id: int, text: str):
    if _app_ref:
        try:
            await _app_ref.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
        except Exception as e:
            logger.warning("Failed to notify %s: %s", chat_id, e)


async def handle_webhook_event(event: str, payload: dict):
    task_id = payload.get("id", "")
    if not task_id:
        return

    task = get_task_by_yougile_id(task_id)
    if not task:
        logger.info("Yougile task %s not found in local DB, skipping", task_id)
        return

    user_id = task.get("assignee_id")
    creator_id = task.get("created_by")
    title = task.get("title", "")
    local_id = task.get("id", "")

    if event == "task-moved":
        column_id = payload.get("columnId", "")
        client = YougileClient()
        all_cols = client.get_columns_by_project(task.get("yougile_project_id", ""))
        col_name = next((c.get("title", "") for c in all_cols if c.get("id") == column_id), "другую колонку")
        text = f"🔄 <b>Задача #{local_id} перемещена</b>\n{title}\n→ {col_name}"
        if user_id:
            await _notify_chat(user_id, text)
        if creator_id and creator_id != user_id:
            await _notify_chat(creator_id, text)
        await _notify_chat(ADMIN_ID, text)

    elif event == "task-updated":
        if payload.get("completed") is True:
            text = f"✅ <b>Задача #{local_id} выполнена в Yougile</b>\n{title}"
            if user_id:
                await _notify_chat(user_id, text)
            if creator_id and creator_id != user_id:
                await _notify_chat(creator_id, text)
            await _notify_chat(ADMIN_ID, text)

    elif event == "task-renamed":
        new_title = payload.get("title", "")
        text = f"✏️ <b>Задача #{local_id} переименована</b>\n{title} → {new_title}"
        if user_id:
            await _notify_chat(user_id, text)
        if creator_id and creator_id != user_id:
            await _notify_chat(creator_id, text)
        await _notify_chat(ADMIN_ID, text)

    elif event == "task-created":
        client = YougileClient()
        try:
            yougile_task = client.get_task(task_id)
        except Exception:
            yougile_task = payload

        yg_title = yougile_task.get("title", "Без названия")
        yg_assigned = yougile_task.get("assigned", [])
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

        # Create local task
        local_id = add_task(
            title=yg_title,
            assignee=tg_assignee_name,
            deadline=deadline_str,
            description=yougile_task.get("description", ""),
            assignee_id=tg_assignee_id,
            created_by=ADMIN_ID,
        )
        update_task_field(local_id, "yougile_task_id", task_id)
        if project_id:
            update_task_field(local_id, "yougile_project_id", project_id)

        from bot.messages import format_datetime_ru
        deadline_display = format_datetime_ru(deadline_str) if deadline_str else "не указан"
        desc_text = f"📋 {yougile_task.get('description', '')}" if yougile_task.get("description") else ""
        text = (
            f"📌 <b>Новая задача из Yougile</b>\n"
            f"#{local_id} {yg_title}\n"
            f"👤 {tg_assignee_name}\n"
            f"⏰ {deadline_display}\n"
            f"{desc_text}"
        ).strip()

        targets = set()
        if tg_assignee_id:
            targets.add(tg_assignee_id)
        targets.add(ADMIN_ID)
        for tid in targets:
            await _notify_chat(tid, text)


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

        import asyncio
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(handle_webhook_event(event, payload))
        finally:
            loop.close()

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
