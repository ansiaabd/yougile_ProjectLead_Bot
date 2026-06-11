import sqlite3
from typing import Optional
from db import get_connection

SCHEMA_PATH = "db/schema.sql"


def init_db():
    with open(SCHEMA_PATH) as f:
        schema = f.read()
    conn = get_connection()
    conn.executescript(schema)
    _migrate(conn)
    conn.commit()
    conn.close()


def _migrate(conn):
    existing_cols = {
        r["name"]
        for r in conn.execute("PRAGMA table_info(users)").fetchall()
    }
    if "role" not in existing_cols:
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")

    existing_cols = {
        r["name"]
        for r in conn.execute("PRAGMA table_info(tasks)").fetchall()
    }
    if "created_by" not in existing_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN created_by INTEGER DEFAULT NULL")
    if "done_comment" not in existing_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN done_comment TEXT DEFAULT ''")
        conn.execute("ALTER TABLE tasks ADD COLUMN done_file_id TEXT DEFAULT ''")
        conn.execute("ALTER TABLE tasks ADD COLUMN done_file_type TEXT DEFAULT ''")
    if "yougile_project_id" not in existing_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN yougile_project_id TEXT DEFAULT ''")
        conn.execute("ALTER TABLE tasks ADD COLUMN yougile_task_id TEXT DEFAULT ''")


# ── Users ────────────────────────────────────────────────

def register_user(user_id: int, username: str, full_name: str):
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?, ?, ?)",
        (user_id, username, full_name),
    )
    conn.commit()
    conn.close()


def get_user_by_username(username: str) -> Optional[dict]:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM users WHERE username = ?", (username.lstrip("@"),)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_user(user_id: int) -> Optional[dict]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_role(user_id: int) -> str:
    conn = get_connection()
    row = conn.execute(
        "SELECT role FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return row["role"] if row else "user"


def set_user_role(user_id: int, role: str):
    conn = get_connection()
    conn.execute("UPDATE users SET role = ? WHERE user_id = ?", (role, user_id))
    conn.commit()
    conn.close()


def list_users() -> list[dict]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM users ORDER BY created_at").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_moderators() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM users WHERE role IN ('admin', 'moderator') ORDER BY created_at"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_user(user_id: int) -> bool:
    conn = get_connection()
    cur = conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


# ── Tasks ────────────────────────────────────────────────

def add_task(
    title: str,
    assignee: str,
    deadline: str,
    description: str = "",
    assignee_id: Optional[int] = None,
    created_by: Optional[int] = None,
) -> int:
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO tasks (title, description, assignee, assignee_id, deadline, created_by) VALUES (?, ?, ?, ?, ?, ?)",
        (title, description, assignee, assignee_id, deadline, created_by),
    )
    conn.commit()
    task_id = cur.lastrowid
    conn.close()
    return task_id


def get_task(task_id: int) -> Optional[dict]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_tasks(include_done: bool = False, user_id: Optional[int] = None, role: str = "user") -> list[dict]:
    conn = get_connection()
    if user_id is None or role == "admin":
        if include_done:
            rows = conn.execute("SELECT * FROM tasks ORDER BY deadline").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status != 'done' ORDER BY deadline"
            ).fetchall()
    elif role == "moderator":
        if include_done:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE assignee_id = ? OR created_by = ? ORDER BY deadline",
                (user_id, user_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status != 'done' AND (assignee_id = ? OR created_by = ?) ORDER BY deadline",
                (user_id, user_id),
            ).fetchall()
    else:
        if include_done:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE assignee_id = ? ORDER BY deadline",
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE assignee_id = ? AND status != 'done' ORDER BY deadline",
                (user_id,),
            ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_task_status(task_id: int, status: str) -> bool:
    conn = get_connection()
    cur = conn.execute(
        "UPDATE tasks SET status = ? WHERE id = ?", (status, task_id)
    )
    conn.commit()
    updated = cur.rowcount > 0
    conn.close()
    return updated


def update_task_field(task_id: int, field: str, value: str) -> bool:
    allowed = {"title", "description", "assignee", "assignee_id", "deadline", "status", "calendar_event_id", "done_comment", "done_file_id", "done_file_type", "yougile_project_id", "yougile_task_id"}
    if field not in allowed:
        raise ValueError(f"Field '{field}' is not allowed")
    conn = get_connection()
    cur = conn.execute(
        f"UPDATE tasks SET {field} = ? WHERE id = ?", (value, task_id)
    )
    conn.commit()
    updated = cur.rowcount > 0
    conn.close()
    return updated


def delete_task(task_id: int) -> bool:
    conn = get_connection()
    cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def list_overdue(user_id: Optional[int] = None, role: str = "user") -> list[dict]:
    conn = get_connection()
    if user_id is None or role == "admin":
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status = 'overdue' ORDER BY deadline"
        ).fetchall()
    elif role == "moderator":
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status = 'overdue' AND (assignee_id = ? OR created_by = ?) ORDER BY deadline",
            (user_id, user_id),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status = 'overdue' AND assignee_id = ? ORDER BY deadline",
            (user_id,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_pending_approval(user_id: Optional[int] = None, role: str = "admin") -> list[dict]:
    conn = get_connection()
    if role == "admin" or user_id is None:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status = 'pending_approval' ORDER BY deadline"
        ).fetchall()
    elif role == "moderator":
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status = 'pending_approval' AND created_by = ? ORDER BY deadline",
            (user_id,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM tasks WHERE 0").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_task_by_yougile_id(yougile_task_id: str) -> Optional[dict]:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM tasks WHERE yougile_task_id = ?", (yougile_task_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_active_tasks_by_project(yougile_project_id: str) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM tasks WHERE yougile_project_id = ? AND status != 'done' ORDER BY deadline",
        (yougile_project_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
