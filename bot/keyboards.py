from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def task_actions_keyboard(task_id: int, status: str = "") -> InlineKeyboardMarkup:
    buttons = []
    if status == "active":
        buttons.append(InlineKeyboardButton("▶️ Взять в работу", callback_data=f"take_{task_id}"))
    buttons.append(InlineKeyboardButton("✅ Выполнено", callback_data=f"done_{task_id}"))
    buttons.append(InlineKeyboardButton("🗑 Удалить", callback_data=f"delete_{task_id}"))
    return InlineKeyboardMarkup([buttons])


def approval_keyboard(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Подтвердить", callback_data=f"approve_{task_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{task_id}"),
        ]
    ])


def done_actions_keyboard(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Комментарий", callback_data=f"done_comment_{task_id}"),
         InlineKeyboardButton("📎 Файл", callback_data=f"done_file_{task_id}")],
        [InlineKeyboardButton("📤 Отправить", callback_data=f"done_send_{task_id}")],
    ])


def menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Мои задачи", callback_data="menu_list")],
        [InlineKeyboardButton("✅ Выполнено", callback_data="menu_done")],
        [InlineKeyboardButton("⏰ Просрочки", callback_data="menu_overdue")],
        [InlineKeyboardButton("📖 Все команды", callback_data="menu_help")],
    ])


def user_picker_keyboard(users: list[dict], current_user_id: int) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("👤 Себя", callback_data="assignee_self")],
    ]
    for u in users:
        if u["user_id"] == current_user_id:
            continue
        label = u.get("full_name") or u.get("username") or f"ID {u['user_id']}"
        if u.get("username"):
            label += f" (@{u['username']})"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"assignee_{u['user_id']}")])
    return InlineKeyboardMarkup(keyboard)


def project_picker_keyboard(projects: list[dict]) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("📄 Без проекта (локальная)", callback_data="project_none")],
    ]
    for p in projects:
        title = p.get("title", "Без названия")
        keyboard.append([InlineKeyboardButton(title, callback_data=f"project_{p['id']}")])
    return InlineKeyboardMarkup(keyboard)
