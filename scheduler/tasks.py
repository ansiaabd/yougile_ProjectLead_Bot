import logging
from datetime import datetime

from telegram.ext import ContextTypes
from db.crud import get_connection, update_task_status
from config import ADMIN_ID

logger = logging.getLogger(__name__)


async def check_overdue(context: ContextTypes.DEFAULT_TYPE):
    conn = get_connection()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    rows = conn.execute(
        "SELECT * FROM tasks WHERE status = 'active' AND deadline <= ?",
        (now,),
    ).fetchall()
    conn.close()

    for row in rows:
        task = dict(row)
        update_task_status(task["id"], "overdue")

        text = (
            f"⚠️ <b>Просрочена задача #{task['id']}</b>\n"
            f"Название: {task['title']}\n"
            f"Дедлайн: {task['deadline']}\n"
            f"Исполнитель: {task['assignee']}"
        )

        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID, text=text, parse_mode="HTML",
            )
        except Exception as e:
            logger.warning("Failed to notify admin: %s", e)

        if task.get("assignee_id"):
            try:
                await context.bot.send_message(
                    chat_id=task["assignee_id"], text=text, parse_mode="HTML",
                )
            except Exception as e:
                logger.warning("Failed to notify assignee %s: %s", task["assignee_id"], e)
