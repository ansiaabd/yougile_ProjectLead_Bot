import json
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from typing import Optional

from yougile.config import YOUGILE_WEBHOOK_SECRET
from yougile.client import YougileClient
from db.crud import get_task_by_yougile_id
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
        text = f"📌 <b>Новая задача в Yougile</b>\n#{local_id} {title}"
        if user_id:
            await _notify_chat(user_id, text)
        if creator_id and creator_id != user_id:
            await _notify_chat(creator_id, text)
        await _notify_chat(ADMIN_ID, text)


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
