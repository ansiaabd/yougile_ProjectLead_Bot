"""
One-time notification: notify assignees of all currently overdue tasks
with instructions to change deadline or mark as done.
Run on VPS: /home/admino/ProjectLead_Bot/venv/bin/python notify_overdue.py
"""
import sys
import os
import asyncio
sys.path.insert(0, os.path.dirname(__file__))

from datetime import datetime
from telegram.ext import Application
from config import BOT_TOKEN
from scheduler.tasks import send_overdue_notifications


async def main():
    app = Application.builder().token(BOT_TOKEN).build()
    await app.initialize()
    await app.start()

    count = await send_overdue_notifications(app)
    print(f"Sent {count} notification(s) to assignees of overdue tasks.")

    await app.stop()
    await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
