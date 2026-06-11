import logging
from typing import Optional
from datetime import datetime

import httpx

from yougile.config import YOUGILE_API_KEY

API_BASE = "https://yougile.com/api-v2"
COLUMN_NAMES = ["Задачи", "В работе", "На проверке", "Готово"]

logger = logging.getLogger(__name__)


class YougileError(Exception):
    pass


class YougileClient:
    def __init__(self, api_key: str = ""):
        self.api_key = api_key or YOUGILE_API_KEY
        if not self.api_key:
            raise YougileError("YOUGILE_API_KEY is not set")
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, json_data: Optional[dict] = None, params: Optional[dict] = None):
        url = f"{API_BASE}/{path}"
        try:
            r = httpx.request(method, url, headers=self.headers, json=json_data, params=params, timeout=15)
        except httpx.RequestError as e:
            raise YougileError(f"Yougile API request failed: {e}") from e
        if r.status_code >= 400:
            body = r.text[:300]
            raise YougileError(f"Yougile API error {r.status_code}: {body}")
        return r.json()

    # ── Projects ──

    def get_projects(self) -> list[dict]:
        data = self._request("GET", "projects")
        return data.get("content", [])

    # ── Boards ──

    def get_boards(self) -> list[dict]:
        data = self._request("GET", "boards")
        return data.get("content", [])

    def get_boards_by_project(self, project_id: str) -> list[dict]:
        all_boards = self.get_boards()
        return [b for b in all_boards if b.get("projectId") == project_id]

    # ── Columns ──

    def get_columns(self, board_id: str) -> list[dict]:
        data = self._request("GET", "columns")
        all_cols = data.get("content", [])
        return [c for c in all_cols if c.get("boardId") == board_id]

    def get_first_column(self, board_id: str) -> Optional[dict]:
        cols = self.get_columns(board_id)
        return cols[0] if cols else None

    def get_columns_by_project(self, project_id: str) -> list[dict]:
        boards = self.get_boards_by_project(project_id)
        all_cols: list[dict] = []
        for b in boards:
            all_cols.extend(self.get_columns(b["id"]))
        return all_cols

    # ── Board / Column creation ──

    def create_board(self, project_id: str, title: str) -> dict:
        return self._request("POST", "boards", {"title": title, "projectId": project_id})

    def create_column(self, board_id: str, title: str) -> dict:
        return self._request("POST", "columns", {"title": title, "boardId": board_id})

    def delete_board(self, board_id: str):
        self._request("PUT", f"boards/{board_id}", {"deleted": True})

    def delete_column(self, column_id: str):
        self._request("PUT", f"columns/{column_id}", {"deleted": True})

    # ── Column mapping (single board with 4 columns) ──

    def get_column_mapping(self, project_id: str) -> dict[str, dict]:
        boards = self.get_boards_by_project(project_id)
        if not boards:
            return {}
        board = boards[0]
        cols = self.get_columns(board["id"])
        mapping = {}
        for c in cols:
            title = c.get("title", "")
            if title in COLUMN_NAMES:
                mapping[title] = {"board_id": board["id"], "column_id": c["id"]}
        return mapping

    def ensure_project_columns(self, project_id: str, board_title: str = "Доска") -> dict[str, dict]:
        boards = self.get_boards_by_project(project_id)

        if not boards:
            board = self.create_board(project_id, board_title)
        else:
            board = boards[0]

        existing = self.get_column_mapping(project_id)
        for name in COLUMN_NAMES:
            if name not in existing:
                col = self.create_column(board["id"], name)
                existing[name] = {"board_id": board["id"], "column_id": col["id"]}
                logger.info("Created column '%s' on board '%s'", name, board["title"])
        return existing

    # ── Users ──

    def get_users(self) -> list[dict]:
        data = self._request("GET", "users")
        return data.get("content", [])

    # ── Tasks ──

    def create_task(
        self,
        column_id: str,
        title: str,
        description: str = "",
        assigned: Optional[list[str]] = None,
        deadline_ms: Optional[int] = None,
    ) -> str:
        body = {"title": title, "columnId": column_id}
        if description:
            body["description"] = description
        if assigned:
            body["assigned"] = assigned
        if deadline_ms:
            body["deadline"] = {"deadline": deadline_ms, "withTime": True}
        result = self._request("POST", "tasks", body)
        return result["id"]

    def update_task(self, task_id: str, **kwargs):
        self._request("PUT", f"tasks/{task_id}", kwargs)

    def move_task(self, task_id: str, column_id: str):
        self.update_task(task_id, columnId=column_id)

    def set_completed(self, task_id: str, completed: bool = True):
        self.update_task(task_id, completed=completed)

    def get_task(self, task_id: str) -> dict:
        return self._request("GET", f"tasks/{task_id}")

    # ── Chat (comments) ──

    def send_message(self, task_id: str, text: str):
        self._request("POST", f"chats/{task_id}/messages", {"text": text})

    def get_messages(self, task_id: str) -> list[dict]:
        data = self._request("GET", f"chats/{task_id}/messages")
        return data.get("content", [])

    # ── Webhooks ──

    def create_webhook(self, url: str, event: str, project_id: Optional[str] = None):
        body = {"url": url, "event": event}
        if project_id:
            body["projectId"] = project_id
        return self._request("POST", "webhooks", body)

    def list_webhooks(self) -> list[dict]:
        data = self._request("GET", "webhooks")
        return data if isinstance(data, list) else data.get("content", [])

    def delete_webhook(self, webhook_id: str):
        self._request("PUT", f"webhooks/{webhook_id}", {"deleted": True})

    # ── High-level helpers ──

    def create_task_in_project(
        self,
        project_id: str,
        title: str,
        description: str = "",
        assignee_yougile_ids: Optional[list[str]] = None,
        deadline_ms: Optional[int] = None,
    ) -> str:
        mapping = self.get_column_mapping(project_id)
        zadachi = mapping.get("Задачи")
        if not zadachi:
            available = list(mapping.keys())
            raise YougileError(
                f"Не найдена колонка «Задачи» в проекте. Доступные колонки: {', '.join(available) or 'нет'}"
            )
        return self.create_task(
            column_id=zadachi["column_id"],
            title=title,
            description=description,
            assigned=assignee_yougile_ids,
            deadline_ms=deadline_ms,
        )

    def move_to_column(self, task_id: str, project_id: str, column_name: str):
        mapping = self.get_column_mapping(project_id)
        col = mapping.get(column_name)
        if not col:
            available = list(mapping.keys())
            raise YougileError(f"Не найдена колонка «{column_name}» в проекте. Доступные: {', '.join(available)}")
        self.move_task(task_id, col["column_id"])

    def add_done_comment(self, task_id: str, comment: str = "", telegram_username: str = ""):
        prefix = f"@{telegram_username}: " if telegram_username else ""
        text = f"{prefix}✅ Задача выполнена"
        if comment:
            text += f"\n\nКомментарий: {comment}"
        self.send_message(task_id, text)

    def add_approval_comment(self, task_id: str, approved: bool, moderator: str = ""):
        prefix = f"@{moderator}: " if moderator else ""
        if approved:
            text = f"{prefix}✅ Подтверждено администратором"
        else:
            text = f"{prefix}🔄 Отклонено администратором"
        self.send_message(task_id, text)
