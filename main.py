from telegram import BotCommand
from telegram.ext import Application

from config import BOT_TOKEN
from db.crud import init_db
from bot.handlers import get_handlers
from scheduler.tasks import check_overdue
from yougile.config import YOUGILE_API_KEY
from yougile.webhook import WebhookServer, set_app


async def _post_init(app: Application):
    commands = [
        BotCommand("add", "Добавить задачу"),
        BotCommand("list", "Мои активные задачи"),
        BotCommand("done", "Отметить выполненной"),
        BotCommand("delete", "Удалить задачу"),
        BotCommand("overdue", "Просроченные задачи"),
        BotCommand("pending", "На подтверждении"),
        BotCommand("take", "Взять задачу в работу"),
        BotCommand("menu", "Быстрое меню"),
        BotCommand("setup_project", "Создать доски Yougile в проектах"),
        BotCommand("setup_webhooks", "Настроить webhook Yougile"),
        BotCommand("cancel", "Отменить создание"),
        BotCommand("help", "Все команды"),
    ]
    await app.bot.set_my_commands(commands)


def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN not set")

    init_db()

    app = Application.builder().token(BOT_TOKEN).post_init(_post_init).build()

    for handler in get_handlers():
        app.add_handler(handler)

    app.job_queue.run_repeating(check_overdue, interval=60, first=10)

    # Start webhook server for Yougile → Telegram sync
    if YOUGILE_API_KEY:
        set_app(app)
        webhook_server = WebhookServer()
        webhook_server.start()
        print("🌐 Yougile webhook server started on :8787", flush=True)
    else:
        print("ℹ️  Yougile API key not set — webhook server disabled", flush=True)

    print("🤖 Bot started. Press Ctrl+C to stop.", flush=True)
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
