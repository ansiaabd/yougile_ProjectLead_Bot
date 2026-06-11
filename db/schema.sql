CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT DEFAULT '',
    full_name TEXT DEFAULT '',
    role TEXT NOT NULL DEFAULT 'user',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    assignee TEXT NOT NULL DEFAULT '',
    assignee_id INTEGER DEFAULT NULL,
    deadline TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'done', 'overdue', 'pending_approval')),
    created_by INTEGER DEFAULT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    calendar_event_id TEXT DEFAULT NULL,
    done_comment TEXT DEFAULT '',
    done_file_id TEXT DEFAULT '',
    done_file_type TEXT DEFAULT '',
    yougile_project_id TEXT DEFAULT '',
    yougile_task_id TEXT DEFAULT '',
    FOREIGN KEY (assignee_id) REFERENCES users(user_id)
);
