"""
One-time sync: mark tasks as done in local DB if they are in "Готово" column in Yougile.
Run on VPS: python sync_yougile_done.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from yougile.client import YougileClient
from db.crud import get_connection, update_task_status

def main():
    client = YougileClient()
    conn = get_connection()

    tasks = conn.execute(
        "SELECT id, yougile_task_id, yougile_project_id, status, title FROM tasks WHERE yougile_task_id != '' AND yougile_task_id IS NOT NULL"
    ).fetchall()

    if not tasks:
        print("No tasks with yougile_task_id found in local DB.")
        return

    print(f"Found {len(tasks)} local tasks with Yougile IDs.")

    # Build a column-id → column-name map for all projects upfront
    all_cols_map = {}
    try:
        all_cols_raw = client._request("GET", "columns")
        all_cols = all_cols_raw if isinstance(all_cols_raw, list) else all_cols_raw.get("content", [])
        for c in all_cols:
            all_cols_map[c["id"]] = c.get("title", "")
    except Exception:
        pass

    updated = 0

    for row in tasks:
        local_id = row["id"]
        yg_id = row["yougile_task_id"]
        project_id = row["yougile_project_id"]
        current_status = row["status"]
        title = row["title"]

        if current_status == "done":
            continue

        try:
            yg_task = client.get_task(yg_id)
        except Exception as e:
            print(f"  Error fetching Yougile task {yg_id} (#{local_id}): {e}")
            continue

        column_id = yg_task.get("columnId", "")
        yg_completed = yg_task.get("completed", False)

        yg_column_name = all_cols_map.get(column_id, "")
        if not yg_column_name and project_id:
            try:
                cols = client.get_columns_by_project(project_id)
                for c in cols:
                    all_cols_map[c["id"]] = c.get("title", "")
                    if c.get("id") == column_id:
                        yg_column_name = c.get("title", "")
            except Exception:
                pass

        is_done = yg_column_name == "Готово" or yg_completed is True

        if is_done:
            update_task_status(local_id, "done")
            reason = "completed=true" if yg_completed else "колонка «Готово»"
            print(f"  ✅ #{local_id} «{title}» → done ({reason})")
            updated += 1
        else:
            print(f"  ⏭ #{local_id} «{title}» → {yg_column_name or column_id} (not done)")

    conn.close()
    print(f"\nDone! Updated {updated} tasks.")

if __name__ == "__main__":
    main()
