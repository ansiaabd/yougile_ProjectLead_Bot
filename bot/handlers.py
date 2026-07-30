import re
import logging
from datetime import datetime
from telegram import Update, InputFile

logger = logging.getLogger(__name__)
from telegram.ext import (
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ConversationHandler,
    ContextTypes,
)

from db.crud import (
    add_task, list_tasks, get_task, update_task_status, delete_task,
    list_overdue, list_pending_approval, register_user, get_user_by_username,
    get_user, get_user_role, set_user_role, list_users, delete_user,
    update_task_field, get_task_by_yougile_id,
    set_user_yougile, get_yougile_user_id,
)
from bot.messages import (
    HELP_TEXT, TASK_ADDED, TASK_DONE, TASK_NOT_FOUND, TASK_DELETED,
    NO_TASKS, INVALID_ID, NO_OVERDUE, INVALID_DATE,
    ASK_TITLE, ASK_DESCRIPTION, ASK_DEADLINE, ASK_ASSIGNEE,
    CANCELLED, SKIPPED_DESC, REGISTERED,
    DONE_APPROVED, DONE_REJECTED,
    DONE_AWAITING_COMMENT, DONE_COMMENT_SAVED,
    DONE_REQUESTED_WITH_DETAILS,
    NO_PENDING, NO_USERS, USER_REMOVED, USER_REMOVE_DENIED, USER_NOT_FOUND, ADMIN_ONLY,
    USER_PROMOTED, USER_DEMOTED, USER_ALREADY_ADMIN, MODERATOR_ONLY,
    NEW_TASK_NOTIFICATION, DONE_WHICH_TASK, DONE_NO_ACTIVE,
)
from bot.keyboards import task_actions_keyboard, approval_keyboard, user_picker_keyboard, menu_keyboard, project_picker_keyboard
from utils.date_parser import parse_deadline, format_datetime_ru
from config import ADMIN_ID
from yougile.client import YougileClient, YougileError

yougile = YougileClient()


def _get_role(user_id: int) -> str:
    if user_id == ADMIN_ID:
        return "admin"
    return get_user_role(user_id)


def _can_approve(user_id: int, task: dict) -> bool:
    return user_id == ADMIN_ID or task.get("created_by") == user_id


def _split_deadline(text: str):
    """Find a deadline at the end of text. Returns (content_before, deadline_str) or (None, None)."""
    words = text.split()
    for n in range(min(len(words), 10), 0, -1):
        candidate = " ".join(words[-n:])
        d = parse_deadline(candidate)
        if d:
            content = " ".join(words[:-n]) if n < len(words) else ""
            content = content.rstrip("/,; ")
            return content, d
    return None, None


async def notify_assignee(context: ContextTypes.DEFAULT_TYPE, task_id: int, title: str, deadline: str, assignee: str, assignee_id: int, description: str = ""):
    if not assignee_id:
        return
    text = NEW_TASK_NOTIFICATION.format(id=task_id, title=title, description=description or "—", deadline=deadline, assignee=assignee)
    try:
        await context.bot.send_message(
            chat_id=assignee_id, text=text, parse_mode="HTML",
            reply_markup=task_actions_keyboard(task_id, "active", viewer_id=assignee_id, assignee_id=assignee_id),
        )
    except Exception:
        pass


PROJECT, ASSIGNEE, TITLE, DESCRIPTION, DEADLINE = range(5)

# Cache for Yougile projects list (refreshed on /add)
_yougile_projects_cache: list[dict] = []


# ── Start / Registration ─────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id == ADMIN_ID:
        await update.message.reply_text(
            "👋 Привет, администратор!\n" + HELP_TEXT
        )
    else:
        register_user(user.id, user.username or "", user.full_name or user.first_name)
        role = get_user_role(user.id)
        prefix = "🛡 " if role == "moderator" else ""
        await update.message.reply_text(
            prefix + REGISTERED.format(name=user.full_name or user.first_name)
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📌 <b>Быстрое меню</b>",
        parse_mode="HTML",
        reply_markup=menu_keyboard(),
    )


# ── Add task (inline + conversation) ─────────────────────

async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    global _yougile_projects_cache

    # Refresh projects cache
    try:
        _yougile_projects_cache = yougile.get_projects()
    except YougileError:
        _yougile_projects_cache = []

    # Remove trigger word "задача" or "/add" or "add"
    trigger = None
    for t in ("задача", "/add", "add"):
        if text.lower().startswith(t):
            trigger = t
            break
    if trigger:
        text = text[len(trigger):].strip()

    creator_id = update.effective_user.id

    content, deadline = _split_deadline(text)
    if content and deadline:
        assignee_raw = ""
        assignee_id = None
        title = content
        description = ""

        if "@" in content:
            at_idx = content.rfind("@")
            before_at = content[:at_idx].strip()
            assignee_raw = content[at_idx:].strip()
            user_data = get_user_by_username(assignee_raw)
            if user_data:
                assignee_id = user_data["user_id"]
            content = before_at

        slash_idx = content.find("/")
        if slash_idx != -1:
            title = content[:slash_idx].strip()
            description = content[slash_idx+1:].strip()
        else:
            title = content

        task_id = add_task(title, assignee_raw, deadline, description, assignee_id, creator_id)
        desc_text = f"📋 {description}" if description else ""
        await update.message.reply_text(
            TASK_ADDED.format(id=task_id, title=title, deadline=format_datetime_ru(deadline), assignee=assignee_raw, description=desc_text),
            reply_markup=task_actions_keyboard(task_id, "active", viewer_id=creator_id, assignee_id=assignee_id or 0),
        )
        await notify_assignee(context, task_id, title, format_datetime_ru(deadline), assignee_raw, assignee_id, description)
        return ConversationHandler.END

    context.user_data.clear()
    return await _ask_project(update, context)


async def _ask_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _yougile_projects_cache:
        await update.message.reply_text(
            "📁 Выберите проект:",
            reply_markup=project_picker_keyboard(_yougile_projects_cache),
        )
        return PROJECT
    # Yougile недоступен — создаём локальную задачу
    context.user_data["yougile_project_id"] = ""
    context.user_data["yougile_project_title"] = ""
    await update.message.reply_text(
        "⚠️ Yougile недоступен. Задача будет создана локально.",
    )
    return await _ask_assignee(update, context)


async def project_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "project_none":
        context.user_data["yougile_project_id"] = ""
        context.user_data["yougile_project_title"] = ""
        await query.edit_message_text("📄 Локальная задача (без Yougile)", parse_mode="HTML")
    elif data.startswith("project_"):
        project_id = data[len("project_"):]
        project = next((p for p in _yougile_projects_cache if p["id"] == project_id), None)
        if project:
            context.user_data["yougile_project_id"] = project_id
            context.user_data["yougile_project_title"] = project.get("title", "")
            await query.edit_message_text(f"📁 Проект: <b>{project['title']}</b>", parse_mode="HTML")

    users = list_users()
    if users:
        await query.message.reply_text(
            "👤 Выберите исполнителя:",
            reply_markup=user_picker_keyboard(users, update.effective_user.id),
        )
    else:
        await query.message.reply_text(ASK_ASSIGNEE)
    return ASSIGNEE


async def _ask_assignee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = list_users()
    if users:
        await update.message.reply_text(
            "👤 Выберите исполнителя:",
            reply_markup=user_picker_keyboard(users, update.effective_user.id),
        )
    else:
        await update.message.reply_text(ASK_ASSIGNEE)
    return ASSIGNEE


def _title_prompt(context: ContextTypes.DEFAULT_TYPE) -> str:
    project_title = context.user_data.get("yougile_project_title", "")
    if project_title:
        return f"📝 Введите название задачи\n<b>{project_title}</b>"
    return ASK_TITLE


async def assignee_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("awaiting_done_id"):
        return await _handle_awaiting_done(update, context)
    if re.match(r"(?i)^(выполнено|сделано|готово)$", update.message.text.strip()):
        return await done_natural(update, context)
    assignee_raw = update.message.text.strip()
    user_data = get_user_by_username(assignee_raw)
    context.user_data["assignee_id"] = user_data["user_id"] if user_data else None
    context.user_data["assignee_raw"] = assignee_raw
    await update.message.reply_text(_title_prompt(context), parse_mode="HTML")
    return TITLE


async def assignee_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if data == "assignee_self":
        user = update.effective_user
        context.user_data["assignee_id"] = user.id
        context.user_data["assignee_raw"] = user.full_name or user.first_name or f"ID {user.id}"
    elif data.startswith("assignee_"):
        uid = int(data.split("_")[1])
        u = get_user(uid)
        if u:
            context.user_data["assignee_id"] = uid
            context.user_data["assignee_raw"] = u.get("full_name") or u.get("username") or f"ID {uid}"

    await query.edit_message_text(f"✅ Исполнитель: {context.user_data['assignee_raw']}")
    await query.message.reply_text(_title_prompt(context), parse_mode="HTML")
    return TITLE


def _prepend_project(title: str, context: ContextTypes.DEFAULT_TYPE) -> str:
    project_title = context.user_data.get("yougile_project_title", "")
    return f"{project_title} {title}" if project_title else title


async def add_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("awaiting_done_id"):
        return await _handle_awaiting_done(update, context)
    if re.match(r"(?i)^(выполнено|сделано|готово)$", update.message.text.strip()):
        return await done_natural(update, context)
    text = update.message.text.strip()

    content, deadline = _split_deadline(text)
    if content and deadline:
        slash_idx = content.find("/")
        if slash_idx != -1:
            title = content[:slash_idx].strip()
            context.user_data["description"] = content[slash_idx+1:].strip()
        else:
            title = content
            context.user_data["description"] = ""
        context.user_data["title"] = _prepend_project(title, context)
        context.user_data["deadline"] = deadline
        return await _finish_task(update, context)

    slash_idx = text.find("/")
    if slash_idx != -1:
        title = text[:slash_idx].strip()
        context.user_data["title"] = _prepend_project(title, context)
        context.user_data["description"] = text[slash_idx+1:].strip()
        await update.message.reply_text(ASK_DEADLINE)
        return DEADLINE

    context.user_data["title"] = _prepend_project(text, context)
    await update.message.reply_text(ASK_DESCRIPTION)
    return DESCRIPTION


async def skip_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["description"] = ""
    await update.message.reply_text(SKIPPED_DESC)
    await update.message.reply_text(ASK_DEADLINE)
    return DEADLINE


async def add_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("awaiting_done_id"):
        return await _handle_awaiting_done(update, context)
    if re.match(r"(?i)^(выполнено|сделано|готово)$", update.message.text.strip()):
        return await done_natural(update, context)
    text = update.message.text.strip()

    content, deadline = _split_deadline(text)
    if content and deadline:
        context.user_data["description"] = content
        context.user_data["deadline"] = deadline
        return await _finish_task(update, context)

    context.user_data["description"] = text
    await update.message.reply_text(ASK_DEADLINE)
    return DEADLINE


async def _handle_awaiting_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        task_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text(INVALID_ID)
        return
    task = get_task(task_id)
    if not task:
        await update.message.reply_text(TASK_NOT_FOUND.format(id=task_id))
        return
    context.user_data.pop("awaiting_done_id", None)
    user_id = update.effective_user.id

    if _can_approve(user_id, task):
        update_task_status(task_id, "done")
        _sync_yougile_done(task)
        await update.message.reply_text(TASK_DONE.format(id=task_id))
    else:
        update_task_status(task_id, "pending_approval")
        _sync_yougile_review(task, update.effective_user)
        await update.message.reply_text(TASK_DONE.format(id=task_id))
        context.user_data["awaiting_done_comment"] = task_id
        await update.message.reply_text(DONE_AWAITING_COMMENT)


async def _finish_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = context.user_data["title"]
    deadline = context.user_data["deadline"]
    description = context.user_data.get("description", "")
    assignee_raw = context.user_data["assignee_raw"]
    assignee_id = context.user_data.get("assignee_id")
    creator_id = update.effective_user.id
    yougile_project_id = context.user_data.get("yougile_project_id", "")

    project_title = context.user_data.get("yougile_project_title", "")
    task_id = add_task(
        title, assignee_raw, deadline, description,
        assignee_id, creator_id,
        project_name=project_title,
    )

    # Create task in Yougile
    yougile_task_id = ""
    if yougile_project_id:
        try:
            # Ensure columns exist before creating task
            yougile.ensure_project_columns(yougile_project_id)
            assigned_ids = []
            assignee_warning = ""
            if assignee_id:
                yid = get_yougile_user_id(assignee_id)
                if yid:
                    assigned_ids.append(yid)
                elif creator_id and get_yougile_user_id(creator_id):
                    assigned_ids.append(get_yougile_user_id(creator_id))
                else:
                    tg_user = get_user(assignee_id)
                    if tg_user:
                        yougile_users = yougile.get_users()
                        email = f"{tg_user.get('username', '')}@t.me"
                        match = next((u for u in yougile_users if email in u.get("email", "")), None)
                        if match:
                            assigned_ids.append(match["id"])
                    if not assigned_ids:
                        assignee_warning = (
                            "\n\n⚠️ <b>Исполнитель не назначен в Yougile.</b>\n"
                            "Исполнителю нужно привязать аккаунт:\n"
                            "<code>/link_email your@email.com</code>"
                        )

            deadline_ms = None
            if deadline:
                try:
                    dt = datetime.strptime(deadline, "%Y-%m-%d %H:%M")
                    deadline_ms = int(dt.timestamp() * 1000)
                except ValueError:
                    pass

            yg_title = f"{title} #{task_id}" if "#" not in title else title
            yougile_task_id = yougile.create_task_in_project(
                project_id=yougile_project_id,
                title=yg_title,
                description=description,
                assignee_yougile_ids=assigned_ids if assigned_ids else None,
                deadline_ms=deadline_ms,
            )
            update_task_field(task_id, "yougile_project_id", yougile_project_id)
            update_task_field(task_id, "yougile_task_id", yougile_task_id)
        except YougileError as e:
            logger.warning("Yougile create failed: %s", e)
            assignee_warning = (assignee_warning or "") + f"\n\n⚠️ <b>Ошибка Yougile:</b> {e}"

    desc_text = f"📋 {description}" if description else ""
    msg = TASK_ADDED.format(id=task_id, title=title, deadline=format_datetime_ru(deadline), assignee=assignee_raw, description=desc_text)
    if assignee_warning:
        msg += assignee_warning
    await update.message.reply_text(msg, reply_markup=task_actions_keyboard(task_id, "active", viewer_id=creator_id, assignee_id=assignee_id or 0))
    await notify_assignee(context, task_id, title, format_datetime_ru(deadline), assignee_raw, assignee_id, description)
    context.user_data.clear()
    return ConversationHandler.END


async def add_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if re.match(r"(?i)^(выполнено|сделано|готово)$", update.message.text.strip()):
        return await done_natural(update, context)
    deadline_raw = update.message.text.strip()
    deadline = parse_deadline(deadline_raw)
    if not deadline:
        await update.message.reply_text(INVALID_DATE)
        return DEADLINE
    context.user_data["deadline"] = deadline
    return await _finish_task(update, context)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(CANCELLED)
    context.user_data.clear()
    return ConversationHandler.END


# ── Yougile sync helpers ─────────────────────────────────

def _sync_yougile_move(task: dict, board_name: str):
    yid = task.get("yougile_task_id", "")
    pid = task.get("yougile_project_id", "")
    if yid and pid:
        try:
            yougile.move_to_column(yid, pid, board_name)
        except YougileError as e:
            logger.warning("Yougile move '%s' failed for task %s: %s", board_name, task["id"], e)


def _sync_yougile_review(task: dict, user=None):
    """Move task to 'На проверке' board + add comment."""
    _sync_yougile_move(task, "На проверке")
    yid = task.get("yougile_task_id", "")
    if yid:
        try:
            username = user.username if user else ""
            comment = task.get("done_comment", "")
            yougile.add_done_comment(yid, comment=comment, telegram_username=username)
        except YougileError as e:
            logger.warning("Yougile comment failed: %s", e)


def _sync_yougile_approve(task: dict, approved: bool, moderator=None):
    """On approve: move to 'Готово' + mark completed. On reject: back to 'В работе'."""
    if approved:
        _sync_yougile_move(task, "Готово")
        yid = task.get("yougile_task_id", "")
        if yid:
            try:
                yougile.set_completed(yid, True)
            except YougileError as e:
                logger.warning("Yougile complete failed: %s", e)
        try:
            username = moderator.username if moderator else ""
            yougile.add_approval_comment(yid, approved=True, moderator=username)
        except YougileError as e:
            logger.warning("Yougile approval comment failed: %s", e)
    else:
        _sync_yougile_move(task, "В работе")
        yid = task.get("yougile_task_id", "")
        if yid:
            try:
                username = moderator.username if moderator else ""
                yougile.add_approval_comment(yid, approved=False, moderator=username)
            except YougileError as e:
                logger.warning("Yougile reject comment failed: %s", e)


def _sync_yougile_done(task: dict):
    """Direct done (admin/creator) - move to 'Готово'."""
    _sync_yougile_move(task, "Готово")
    yid = task.get("yougile_task_id", "")
    if yid:
        try:
            yougile.set_completed(yid, True)
        except YougileError as e:
            logger.warning("Yougile complete failed: %s", e)


def _sync_yougile_take(task: dict):
    """Move task to 'В работе'."""
    _sync_yougile_move(task, "В работе")


# ── Webhook setup ────────────────────────────────────────

async def setup_webhooks_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text(ADMIN_ONLY)
        return
    try:
        from yougile.config import YOUGILE_WEBHOOK_SECRET
        base_url = context.args[0] if context.args else ""
        if not base_url:
            await update.message.reply_text(
                "❌ Укажите публичный URL сервера.\n"
                "Пример: /setup_webhooks https://your-domain.ngrok-free.app"
            )
            return
        webhook_url = f"{base_url.rstrip('/')}/webhook"
        yougile_client = YougileClient()

        # Delete old webhooks
        for h in yougile_client.list_webhooks():
            try:
                yougile_client.delete_webhook(h["id"])
            except YougileError:
                pass

        events = ["task-created", "task-moved", "task-renamed", "task-updated"]
        created = 0
        for event in events:
            try:
                yougile_client.create_webhook(webhook_url, event)
                created += 1
            except YougileError as e:
                await update.message.reply_text(f"❌ Ошибка при создании webhook {event}: {e}")
                return
        await update.message.reply_text(
            f"✅ Создано {created} webhook-ов в Yougile.\n"
            f"URL: {webhook_url}\n"
            f"События: {', '.join(events)}"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")


# ── Project setup ─────────────────────────────────────────

async def setup_project_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text(ADMIN_ONLY)
        return
    try:
        tasks_text = "📁 Настраиваю колонки для проектов...\n"
        if not _yougile_projects_cache:
            _yougile_projects_cache.extend(yougile.get_projects())

        for p in _yougile_projects_cache:
            mapping = yougile.ensure_project_columns(p["id"])
            cols = ', '.join(mapping.keys())
            tasks_text += f"\n<b>{p['title']}</b>: {cols}\n"

        await update.message.reply_text(tasks_text, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")


async def create_project_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text(ADMIN_ONLY)
        return
    name = ' '.join(context.args).strip()
    if not name:
        context.user_data["awaiting_project_name"] = True
        await update.message.reply_text("📁 Напишите название проекта:")
        return
    await _create_project_internal(update, name)


async def _create_project_internal(update: Update, name: str):
    try:
        admin_yid = get_yougile_user_id(update.effective_user.id)
        project_kwargs = {'title': name}
        if admin_yid:
            project_kwargs['users'] = {admin_yid: 'admin'}
        project = yougile._request('POST', 'projects', project_kwargs)
        pid = project['id']
        board = yougile._request('POST', 'boards', {'title': 'Доска', 'projectId': pid})
        bid = board['id']
        for col_name in ['Задачи', 'В работе', 'На проверке', 'Готово']:
            yougile._request('POST', 'columns', {'title': col_name, 'boardId': bid})
        await update.message.reply_text(
            f"✅ Проект <b>{name}</b> создан!\n"
            f"Колонки: Задачи → В работе → На проверке → Готово",
            parse_mode="HTML",
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")


# ── Link email (привязать email к Telegram) ─────────────

async def link_email_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    try:
        email = context.args[0].strip().lower()
    except (IndexError, ValueError):
        await update.message.reply_text(
            "❌ Укажите email: /link_email you@example.com\n"
            "Email должен совпадать с email в Yougile."
        )
        return

    if "@" not in email:
        await update.message.reply_text("❌ Некорректный email")
        return

    # Ensure user is registered in local DB
    existing = get_user(user_id)
    if not existing:
        register_user(user_id, user.username or "", user.full_name or "")

    yougile_users = yougile.get_users()
    match = next((u for u in yougile_users if u.get("email", "").lower() == email), None)
    if not match:
        await update.message.reply_text(
            f"❌ Пользователь с email <b>{email}</b> не найден в Yougile.\n"
            "Убедитесь, что вы используете тот же email, что в вашем аккаунте Yougile.",
            parse_mode="HTML",
        )
        return

    set_user_yougile(user_id, match["id"], email)
    await update.message.reply_text(
        f"✅ Email <b>{email}</b> привязан к вашему Telegram аккаунту!\n"
        f"Теперь при назначении вас исполнителем задачи, бот укажет вас в Yougile.",
        parse_mode="HTML",
    )


# ── Take task (взять в работу) ──────────────────────────

async def _take_task(update: Update, task_id: int) -> bool:
    """Shared take-logic. Returns True on success."""
    task = get_task(task_id)
    if not task:
        await update.message.reply_text(TASK_NOT_FOUND.format(id=task_id))
        return False
    user_id = update.effective_user.id
    if task.get("assignee_id") and task["assignee_id"] != user_id:
        await update.message.reply_text("❌ Вы не являетесь исполнителем этой задачи.")
        return False
    if task["status"] != "active":
        await update.message.reply_text("❌ Можно взять в работу только активную задачу.")
        return False
    _sync_yougile_take(task)
    await update.message.reply_text(f"🔄 Задача #{task_id} <b>{task['title']}</b> — взята в работу!", parse_mode="HTML")
    return True


async def take_task_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        task_id = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("❌ Укажите ID задачи: /take <id>")
        return
    await _take_task(update, task_id)


# ── List ─────────────────────────────────────────────────

async def list_tasks_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    role = _get_role(user_id)
    show_all = role == "admin" and context.args and context.args[0] == "all"

    if show_all:
        tasks = list_tasks(include_done=False)
    elif role == "admin":
        tasks = list_tasks(include_done=False)
    else:
        tasks = list_tasks(include_done=False, user_id=user_id, role=role)

    if not tasks:
        await update.message.reply_text(NO_TASKS)
        return

    import html as html_mod
    lines = []
    for t in tasks:
        status_icon = "🟢" if t["status"] == "active" else "🔴"
        desc_clean = html_mod.escape(t["description"]) if t["description"] else ""
        desc = f" - {desc_clean}" if desc_clean else ""
        lines.append(
            f"{status_icon} #{t['id']} <b>{html_mod.escape(t['title'])}</b>{desc}\n"
            f"   ⏰ {t['deadline']} | 👤 {html_mod.escape(t['assignee'])}"
        )

    full = "\n\n".join(lines)
    if len(full) <= 4000:
        await update.message.reply_text(full, parse_mode="HTML")
    else:
        chunk = ""
        for line in lines:
            if len(chunk) + len(line) + 2 > 4000:
                await update.message.reply_text(chunk.strip(), parse_mode="HTML")
                chunk = ""
            chunk += line + "\n\n"
        if chunk.strip():
            await update.message.reply_text(chunk.strip(), parse_mode="HTML")


# ── Done (with approval) ─────────────────────────────────

async def done_task_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        task_id = int(context.args[0])
    except IndexError:
        context.user_data["awaiting_done_id"] = True
        await update.message.reply_text(
            "📋 Напишите ID задачи, которую выполнили:"
        )
        return
    except ValueError:
        await update.message.reply_text(INVALID_ID)
        return

    task = get_task(task_id)
    if not task:
        await update.message.reply_text(TASK_NOT_FOUND.format(id=task_id))
        return

    user_id = update.effective_user.id

    if _can_approve(user_id, task):
        update_task_status(task_id, "done")
        _sync_yougile_done(task)
        await update.message.reply_text(TASK_DONE.format(id=task_id))
    else:
        update_task_status(task_id, "pending_approval")
        _sync_yougile_review(task, update.effective_user)
        await update.message.reply_text(TASK_DONE.format(id=task_id))
        context.user_data["awaiting_done_comment"] = task_id
        await update.message.reply_text(DONE_AWAITING_COMMENT)


# ── Reschedule ────────────────────────────────────

async def reschedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        task_id = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("❌ Использование: /reschedule <id> <новый срок>\nНапример: /reschedule 42 завтра 18:00")
        return

    task = get_task(task_id)
    if not task:
        await update.message.reply_text(TASK_NOT_FOUND.format(id=task_id))
        return

    if len(context.args) >= 2:
        deadline_raw = " ".join(context.args[1:])
        deadline = parse_deadline(deadline_raw)
        if deadline:
            update_task_field(task_id, "deadline", deadline)
            update_task_status(task_id, "active")
            if task.get("yougile_task_id"):
                from yougile.client import YougileClient
                try:
                    from datetime import datetime as dt
                    dt_obj = dt.strptime(deadline, "%Y-%m-%d %H:%M")
                    ms = int(dt_obj.timestamp() * 1000)
                    YougileClient().update_task(task["yougile_task_id"], deadline={"deadline": ms, "withTime": True})
                except Exception:
                    pass
            await update.message.reply_text(
                f"✅ Срок задачи #{task_id} изменён на {format_datetime_ru(deadline)}",
                parse_mode="HTML",
            )
            return
        else:
            await update.message.reply_text(
                "❌ Не удалось распознать дату. Попробуйте ещё раз, например: /reschedule 42 завтра 18:00",
            )
            return

    context.user_data["reschedule_task_id"] = task_id
    await update.message.reply_text(
        f"📅 Напишите новый срок для задачи #{task_id} «{task['title']}»\n"
        f"Например: <b>завтра в 18:00</b> или <b>15.07</b>",
        parse_mode="HTML",
    )


async def _notify_approvers(context: ContextTypes.DEFAULT_TYPE, task: dict):
    task_id = task["id"]
    comment_html = f"\n💬 {task.get('done_comment', '')}" if task.get('done_comment') else ""
    file_info = ""
    if task.get("done_file_id"):
        ftype = "📎 Файл" if task["done_file_type"] == "document" else "🖼 Фото"
        file_info = f"\n{ftype} прикреплён"
    text = DONE_REQUESTED_WITH_DETAILS.format(
        id=task_id, title=task["title"],
        comment_html=comment_html, file_info=file_info,
    )

    async def _send(chat_id):
        if task.get("done_file_id"):
            kwargs = dict(
                chat_id=chat_id, caption=text,
                reply_markup=approval_keyboard(task_id),
            )
            fid = task["done_file_id"]
            if task["done_file_type"] == "document":
                await context.bot.send_document(document=fid, **kwargs)
            else:
                await context.bot.send_photo(photo=fid, **kwargs)
        else:
            await context.bot.send_message(
                chat_id=chat_id, text=text,
                reply_markup=approval_keyboard(task_id),
            )

    notified = set()
    if task.get("created_by"):
        try:
            await _send(task["created_by"])
            notified.add(task["created_by"])
        except Exception:
            pass

    assignee_id = task.get("assignee_id")
    if ADMIN_ID not in notified:
        try:
            await _send(ADMIN_ID)
        except Exception:
            pass


# ── Delete ───────────────────────────────────────────────

async def delete_task_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        try:
            task_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text(INVALID_ID)
            return
        task = get_task(task_id)
        if not task:
            await update.message.reply_text(TASK_NOT_FOUND.format(id=task_id))
            return
        yid = task.get("yougile_task_id", "")
        if yid:
            try:
                from yougile.client import YougileClient
                YougileClient().delete_task(yid)
            except Exception:
                pass
        delete_task(task_id)
        await update.message.reply_text(TASK_DELETED.format(id=task_id))
        return

    context.user_data["awaiting_delete_id"] = True
    await update.message.reply_text("🗑 Укажите ID задачи для удаления:")


async def number_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("awaiting_delete_id"):
        try:
            task_id = int(update.message.text.strip())
        except ValueError:
            return
        task = get_task(task_id)
        if not task:
            await update.message.reply_text(TASK_NOT_FOUND.format(id=task_id))
            return
        context.user_data.pop("awaiting_delete_id", None)
        yid = task.get("yougile_task_id", "")
        if yid:
            try:
                from yougile.client import YougileClient
                YougileClient().delete_task(yid)
            except Exception:
                pass
        delete_task(task_id)
        await update.message.reply_text(TASK_DELETED.format(id=task_id))
        return

    if not context.user_data.get("awaiting_done_id"):
        return
    try:
        task_id = int(update.message.text.strip())
    except ValueError:
        return
    task = get_task(task_id)
    if not task:
        await update.message.reply_text(TASK_NOT_FOUND.format(id=task_id))
        return
    context.user_data.pop("awaiting_done_id", None)
    await _request_done_approval(update, context, task)


# ── Users management (admin) ─────────────────────────────

async def users_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text(ADMIN_ONLY)
        return
    users = list_users()
    if not users:
        await update.message.reply_text(NO_USERS)
        return
    lines = []
    for u in users:
        name = u["full_name"] or u["username"] or f"ID {u['user_id']}"
        created = u["created_at"][:10]
        role_badge = "⭐ " if u["role"] == "admin" else "🛡 " if u["role"] == "moderator" else ""
        lines.append(f"{role_badge}🆔 <code>{u['user_id']}</code> — {name} (@{u['username']}) — {u['role']} — с {created}")
    await update.message.reply_text(
        "📋 <b>Зарегистрированные пользователи:</b>\n\n" + "\n".join(lines),
        parse_mode="HTML",
    )


async def removeuser_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text(ADMIN_ONLY)
        return
    try:
        target_id = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("❌ Укажите ID пользователя: /removeuser <id>")
        return
    if target_id == ADMIN_ID:
        await update.message.reply_text(USER_REMOVE_DENIED)
        return
    if not get_user(target_id):
        await update.message.reply_text(USER_NOT_FOUND.format(user_id=target_id))
        return
    delete_user(target_id)
    await update.message.reply_text(USER_REMOVED.format(user_id=target_id))


# ── Promote / Demote (admin) ─────────────────────────────

async def promote_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text(ADMIN_ONLY)
        return
    try:
        target_id = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("❌ Укажите ID пользователя: /promote <id>")
        return
    if target_id == ADMIN_ID:
        await update.message.reply_text(USER_ALREADY_ADMIN)
        return
    if not get_user(target_id):
        await update.message.reply_text(USER_NOT_FOUND.format(user_id=target_id))
        return
    set_user_role(target_id, "moderator")
    await update.message.reply_text(USER_PROMOTED.format(user_id=target_id))
    try:
        await context.bot.send_message(
            chat_id=target_id,
            text="🛡 Вам назначена роль модератора! Теперь вы можете создавать задачи и подтверждать выполнение.",
        )
    except Exception:
        pass


async def demote_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text(ADMIN_ONLY)
        return
    try:
        target_id = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("❌ Укажите ID пользователя: /demote <id>")
        return
    if target_id == ADMIN_ID:
        await update.message.reply_text(USER_ALREADY_ADMIN)
        return
    if not get_user(target_id):
        await update.message.reply_text(USER_NOT_FOUND.format(user_id=target_id))
        return
    set_user_role(target_id, "user")
    await update.message.reply_text(USER_DEMOTED.format(user_id=target_id))
    try:
        await context.bot.send_message(
            chat_id=target_id,
            text="🔄 Ваша роль модератора отозвана.",
        )
    except Exception:
        pass


# ── Overdue / Pending ────────────────────────────────────

async def overdue_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    role = _get_role(user_id)
    tasks = list_overdue(user_id=None if role == "admin" else user_id, role=role)
    if not tasks:
        await update.message.reply_text(NO_OVERDUE)
        return
    lines = []
    for t in tasks:
        lines.append(
            f"🔴 #{t['id']} <b>{t['title']}</b>\n   ⏰ {t['deadline']} | 👤 {t['assignee']}"
        )
    await update.message.reply_text(
        "⚠️ <b>Просроченные задачи:</b>\n\n" + "\n\n".join(lines),
        parse_mode="HTML",
    )


async def pending_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    role = _get_role(user_id)
    if role not in ("admin", "moderator"):
        await update.message.reply_text(MODERATOR_ONLY)
        return
    tasks = list_pending_approval(user_id=user_id, role=role)
    if not tasks:
        await update.message.reply_text(NO_PENDING)
        return
    lines = []
    for t in tasks:
        lines.append(
            f"⏳ #{t['id']} <b>{t['title']}</b>\n   👤 {t['assignee']} | ⏰ {t['deadline']}"
        )
    await update.message.reply_text(
        "📬 <b>Задачи на подтверждении:</b>\n\n" + "\n\n".join(lines),
        parse_mode="HTML",
    )


# ── Natural language done (выполнено / сделано) ──────────

async def done_natural(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    role = _get_role(user_id)
    tasks = list_tasks(include_done=False, user_id=user_id, role=role)

    if not tasks:
        await update.message.reply_text(DONE_NO_ACTIVE)
        return

    if len(tasks) == 1:
        task = tasks[0]
        await _request_done_approval(update, context, task)
    else:
        lines = [f"#{t['id']} — {t['title']} (⏰ {t['deadline']})" for t in tasks]
        header = DONE_WHICH_TASK
        max_len = 4000
        chunks = []
        current = header
        for line in lines:
            candidate = current + "\n" + line
            if len(candidate) > max_len:
                chunks.append(current)
                current = line
            else:
                current = candidate
        chunks.append(current)
        for chunk in chunks:
            await update.message.reply_text(chunk)
        context.user_data["awaiting_done_id"] = True

    return


# Merged into number_handler


async def _request_done_approval(update: Update, context: ContextTypes.DEFAULT_TYPE, task: dict):
    task_id = task["id"]
    user_id = update.effective_user.id

    if _can_approve(user_id, task):
        update_task_status(task_id, "done")
        _sync_yougile_done(task)
        await update.message.reply_text(TASK_DONE.format(id=task_id))
    else:
        update_task_status(task_id, "pending_approval")
        _sync_yougile_review(task, update.effective_user)
        await update.message.reply_text(TASK_DONE.format(id=task_id))
        context.user_data["awaiting_done_comment"] = task_id
        await update.message.reply_text(DONE_AWAITING_COMMENT)
        await _notify_approvers(context, task)


# ── Callback (buttons) ───────────────────────────────────

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task_id = context.user_data.pop("reschedule_task_id", None)
    if task_id:
        deadline_raw = update.message.text.strip()
        deadline = parse_deadline(deadline_raw)
        if not deadline:
            await update.message.reply_text(
                "❌ Не удалось распознать дату. Попробуйте ещё раз, например: <b>завтра 18:00</b> или <b>15.07.2026</b>",
                parse_mode="HTML",
            )
            context.user_data["reschedule_task_id"] = task_id
            return
        update_task_field(task_id, "deadline", deadline)
        update_task_status(task_id, "active")
        task = get_task(task_id)
        if task and task.get("yougile_task_id"):
            from yougile.client import YougileClient
            try:
                from datetime import datetime as dt
                dt_obj = dt.strptime(deadline, "%Y-%m-%d %H:%M")
                ms = int(dt_obj.timestamp() * 1000)
                YougileClient().update_task(task["yougile_task_id"], deadline={"deadline": ms, "withTime": True})
            except Exception:
                pass
        await update.message.reply_text(
            f"✅ Срок задачи #{task_id} изменён на {format_datetime_ru(deadline)}",
            parse_mode="HTML",
        )
        return

    if context.user_data.get("awaiting_project_name"):
        context.user_data.pop("awaiting_project_name", None)
        name = update.message.text.strip()
        if name:
            await _create_project_internal(update, name)
        return
    await done_comment_handler(update, context)


async def done_comment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task_id = context.user_data.pop("awaiting_done_comment", None)
    if not task_id:
        return
    task = get_task(task_id)
    if not task or not task.get("yougile_task_id"):
        await update.message.reply_text("✅ Готово!")
        return

    yougile_task_id = task["yougile_task_id"]
    text = ""
    if update.message.text:
        text = update.message.text.strip()
    elif update.message.caption:
        text = update.message.caption.strip()

    file_name = None
    if update.message.photo:
        file_name = f"Фото (id: {update.message.photo[-1].file_id})"
        if not text:
            text = "📷 Отправлено фото"
    elif update.message.document:
        doc = update.message.document
        file_name = doc.file_name or f"Файл (id: {doc.file_id})"
        if not text:
            text = f"📎 Отправлен файл: {file_name}"

    if text:
        try:
            yougile.send_message(yougile_task_id, text)
        except Exception as e:
            logger.error(f"Yougile send_message failed: {e}")

    await update.message.reply_text(DONE_COMMENT_SAVED)


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    user_id = update.effective_user.id

    if data.startswith("take_"):
        task_id = int(data.split("_")[1])
        task = get_task(task_id)
        if not task:
            await query.edit_message_text(TASK_NOT_FOUND.format(id=task_id))
            return
        if task.get("assignee_id") and task["assignee_id"] != user_id:
            await query.answer("❌ Вы не являетесь исполнителем этой задачи.", show_alert=True)
            return
        if task["status"] != "active":
            await query.answer("❌ Задача уже в работе или выполнена.", show_alert=True)
            return
        _sync_yougile_take(task)
        await query.edit_message_text(f"🔄 Задача #{task_id} <b>{task['title']}</b> — взята в работу!", parse_mode="HTML")
        return
    elif data.startswith("done_"):
        task_id = int(data.split("_")[1])
        task = get_task(task_id)
        if not task:
            await query.edit_message_text(TASK_NOT_FOUND.format(id=task_id))
            return
        if _can_approve(user_id, task):
            update_task_status(task_id, "done")
            _sync_yougile_done(task)
            await query.edit_message_text(TASK_DONE.format(id=task_id))
        else:
            update_task_status(task_id, "pending_approval")
            _sync_yougile_review(task, update.effective_user)
            await query.edit_message_text(TASK_DONE.format(id=task_id))
            context.user_data["awaiting_done_comment"] = task_id
            await query.message.reply_text(DONE_AWAITING_COMMENT)

    elif data.startswith("reschedule_"):
        task_id = int(data.split("_")[1])
        task = get_task(task_id)
        if not task:
            await query.edit_message_text(TASK_NOT_FOUND.format(id=task_id))
            return
        context.user_data["reschedule_task_id"] = task_id
        await query.edit_message_text(
            f"📅 Напишите новый срок для задачи #{task_id} «{task['title']}»\n"
            f"Например: <b>завтра в 18:00</b> или <b>15.07</b>",
            parse_mode="HTML",
        )
        return

    elif data.startswith("approve_"):
        task_id = int(data.split("_")[1])
        task = get_task(task_id)
        if not task:
            await query.edit_message_text(TASK_NOT_FOUND.format(id=task_id))
            return
        if not _can_approve(user_id, task):
            await query.edit_message_text("❌ Только создатель задачи или администратор может подтверждать.")
            return
        update_task_status(task_id, "done")
        await query.edit_message_text(DONE_APPROVED.format(id=task_id))
        _sync_yougile_approve(task, approved=True, moderator=update.effective_user)
        if task.get("assignee_id"):
            try:
                await context.bot.send_message(
                    chat_id=task["assignee_id"],
                    text=DONE_APPROVED.format(id=task_id),
                )
            except Exception:
                pass

    elif data.startswith("reject_"):
        task_id = int(data.split("_")[1])
        task = get_task(task_id)
        if not task:
            await query.edit_message_text(TASK_NOT_FOUND.format(id=task_id))
            return
        if not _can_approve(user_id, task):
            await query.edit_message_text("❌ Только создатель задачи или администратор может отклонять.")
            return
        update_task_status(task_id, "active")
        await query.edit_message_text(DONE_REJECTED.format(id=task_id))
        _sync_yougile_approve(task, approved=False, moderator=update.effective_user)
        if task.get("assignee_id"):
            try:
                await context.bot.send_message(
                    chat_id=task["assignee_id"],
                    text=DONE_REJECTED.format(id=task_id),
                )
            except Exception:
                pass

    elif data.startswith("delete_"):
        task_id = int(data.split("_")[1])
        task = get_task(task_id)
        if not task:
            await query.edit_message_text(TASK_NOT_FOUND.format(id=task_id))
            return
        yid = task.get("yougile_task_id", "")
        if yid:
            try:
                from yougile.client import YougileClient
                YougileClient().delete_task(yid)
            except Exception:
                pass
        delete_task(task_id)
        await query.edit_message_text(TASK_DELETED.format(id=task_id))

    # Menu buttons
    elif data == "menu_list":
        await query.answer()
        await list_tasks_handler(update, context)
    elif data == "menu_done":
        await query.answer()
        context.user_data["awaiting_done_id"] = True
        await query.message.reply_text("📋 Напишите ID задачи (например: 3):")
    elif data == "menu_overdue":
        await query.answer()
        await overdue_handler(update, context)
    elif data == "menu_help":
        await query.answer()
        await query.message.reply_text(HELP_TEXT)


# ── Handlers list ───────────────────────────────────────

def get_handlers():
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("add", add_start),
            MessageHandler(filters.Regex(r"(?i)\bзадача\b"), add_start),
        ],
        states={
            PROJECT: [
                CallbackQueryHandler(project_callback, pattern=r"^project_"),
            ],
            ASSIGNEE: [
                CallbackQueryHandler(assignee_callback, pattern=r"^assignee_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, assignee_text),
            ],
            TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_title)],
            DESCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_description),
                CommandHandler("skip", skip_description),
            ],
            DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_deadline)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    return [
        CommandHandler("start", start),
        CommandHandler("help", help_command),
        CommandHandler("menu", menu_command),
        CommandHandler("take", take_task_handler),
        conv_handler,
        CommandHandler("list", list_tasks_handler),
        CommandHandler("done", done_task_handler),
        CommandHandler("delete", delete_task_handler),
        CommandHandler("reschedule", reschedule_command),
        CommandHandler("overdue", overdue_handler),
        CommandHandler("pending", pending_handler),
        CommandHandler("users", users_handler),
        CommandHandler("removeuser", removeuser_handler),
        CommandHandler("promote", promote_handler),
        CommandHandler("demote", demote_handler),
        CommandHandler("setup_webhooks", setup_webhooks_handler),
        CommandHandler("setup_project", setup_project_handler),
        CommandHandler("create_project", create_project_handler),
        CommandHandler("link_email", link_email_handler),
        MessageHandler(filters.Regex(r"(?i)^(выполнено|сделано|готово)$") & ~filters.COMMAND, done_natural),
        MessageHandler(filters.Regex(r"^\d+$") & ~filters.COMMAND, number_handler),
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler),
        MessageHandler(filters.PHOTO, done_comment_handler),
        MessageHandler(filters.Document.ALL, done_comment_handler),
        CallbackQueryHandler(button_callback),
    ]
