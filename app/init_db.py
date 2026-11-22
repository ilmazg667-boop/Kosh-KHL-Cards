"""One-shot database bootstrap used by Docker Compose."""
import asyncio
from .db import init_db, Session
from .seed import seed_cards
from .services import ensure_season, ensure_daily_tournament

async def main():
    await init_db()
    async with Session() as session:
        await seed_cards(session)
        await ensure_season(session)
        await ensure_daily_tournament(session)

if __name__ == "__main__":
    asyncio.run(main())
