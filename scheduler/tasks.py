import logging
from datetime import datetime

from telegram.ext import ContextTypes
from telegram import InlineKeyboardMarkup
from db.crud import get_connection, update_task_field, update_task_status
from config import ADMIN_ID
from bot.keyboards import overdue_keyboard

logger = logging.getLogger(__name__)


async def check_overdue(context: ContextTypes.DEFAULT_TYPE):
    conn = get_connection()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    rows = conn.execute(
        "SELECT * FROM tasks WHERE status = 'active' AND deadline != '' AND deadline <= ?",
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
            f"Исполнитель: {task['assignee']}\n\n"
            f"Пожалуйста, измените срок задачи или закройте её как выполненную."
        )
        kb = overdue_keyboard(task["id"])

        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID, text=text, parse_mode="HTML",
                reply_markup=kb,
            )
        except Exception as e:
            logger.warning("Failed to notify admin: %s", e)

        if task.get("assignee_id"):
            try:
                await context.bot.send_message(
                    chat_id=task["assignee_id"], text=text, parse_mode="HTML",
                    reply_markup=kb,
                )
            except Exception as e:
                logger.warning("Failed to notify assignee %s: %s", task["assignee_id"], e)


async def send_overdue_notifications(app, chat_id: int = 0):
    """Send notifications to all assignees of currently overdue tasks.
    Used for one-time batch notification."""
    from datetime import datetime as dt
    conn = get_connection()
    now = dt.now().strftime("%Y-%m-%d %H:%M")
    rows = conn.execute(
        "SELECT * FROM tasks WHERE status = 'overdue' AND deadline != '' AND deadline <= ?",
        (now,),
    ).fetchall()
    conn.close()

    if not rows:
        logger.info("No overdue tasks to notify.")
        return 0

    count = 0
    for row in rows:
        task = dict(row)
        text = (
            f"⚠️ <b>Просрочена задача #{task['id']}</b>\n"
            f"Название: {task['title']}\n"
            f"Дедлайн: {task['deadline']}\n"
            f"Исполнитель: {task['assignee']}\n\n"
            f"Пожалуйста, измените срок задачи или закройте её как выполненную."
        )
        kb = overdue_keyboard(task["id"])
        recipients = {ADMIN_ID}
        if task.get("assignee_id"):
            recipients.add(task["assignee_id"])
        for cid in recipients:
            if chat_id and cid != chat_id:
                continue
            try:
                await app.bot.send_message(
                    chat_id=cid, text=text, parse_mode="HTML",
                    reply_markup=kb,
                )
                count += 1
            except Exception as e:
                logger.warning("Failed to notify %s: %s", cid, e)
    return count
