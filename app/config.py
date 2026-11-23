import os
from dotenv import load_dotenv
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./kosh.db")
# Railway provides postgres:// or postgresql://; the app uses SQLAlchemy async.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql+asyncpg://" + DATABASE_URL[len("postgres://"):]
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = "postgresql+asyncpg://" + DATABASE_URL[len("postgresql://"):]
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "1205576607").split(",") if x.strip().isdigit()} | {8745667675, 1603657074, 5068548765}

BATTLEPASS_PREMIUM_PRICE = 750
