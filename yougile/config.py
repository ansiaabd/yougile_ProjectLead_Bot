import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()  # root .env
load_dotenv(Path(__file__).parent / ".env")  # yougile/.env

YOUGILE_COMPANY_ID: str = os.getenv("YOUGILE_COMPANY_ID", "")
YOUGILE_API_KEY: str = os.getenv("YOUGILE_API_KEY", "")
YOUGILE_WEBHOOK_SECRET: str = os.getenv("YOUGILE_WEBHOOK_SECRET", "")
