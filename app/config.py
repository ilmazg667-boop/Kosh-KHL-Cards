import os
from dotenv import load_dotenv
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./kosh.db")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "1205576607").split(",") if x.strip().isdigit()} | {8745667675}

BATTLEPASS_PREMIUM_PRICE = max(1, int(os.getenv('BATTLEPASS_PREMIUM_PRICE', '100')))
