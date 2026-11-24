from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import event
from .config import DATABASE_URL

connect_args = {'check_same_thread': False} if DATABASE_URL.startswith('sqlite') else {}
engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True, connect_args=connect_args)
if DATABASE_URL.startswith('sqlite'):
    @event.listens_for(engine.sync_engine, 'connect')
    def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()
Session = async_sessionmaker(engine, expire_on_commit=False)
class Base(DeclarativeBase):
    pass
async def init_db():
    from . import models  # register tables
    async with engine.begin() as conn:
        if DATABASE_URL.startswith('sqlite'):
            await conn.exec_driver_sql('PRAGMA foreign_keys=ON')
        await conn.run_sync(Base.metadata.create_all)
        # Lightweight additive migrations for existing databases.
        if DATABASE_URL.startswith('sqlite'):
            for table, columns in {
                'users': {'last_name': "VARCHAR(128) NOT NULL DEFAULT ''", 'team_name': "VARCHAR(40) NOT NULL DEFAULT 'Моя команда'"},
                'promo_codes': {'reward_xp': 'INTEGER NOT NULL DEFAULT 0', 'reward_premium': 'BOOLEAN NOT NULL DEFAULT 0'},
                'promo_redemptions': {'xp_awarded': 'INTEGER NOT NULL DEFAULT 0', 'premium_awarded': 'BOOLEAN NOT NULL DEFAULT 0'},
                'matches': {'result_sent': 'BOOLEAN NOT NULL DEFAULT 0'},
                'daily_tournaments': {'title': "VARCHAR(100) NOT NULL DEFAULT ''", 'ends_at': 'DATETIME'},
                'trade_offers': {'sender_coins': 'INTEGER NOT NULL DEFAULT 0', 'sender_gems': 'INTEGER NOT NULL DEFAULT 0', 'receiver_coins': 'INTEGER NOT NULL DEFAULT 0', 'receiver_gems': 'INTEGER NOT NULL DEFAULT 0', 'sender_confirmed': 'BOOLEAN NOT NULL DEFAULT 0', 'receiver_confirmed': 'BOOLEAN NOT NULL DEFAULT 0'},
            }.items():
                result = await conn.exec_driver_sql(f'PRAGMA table_info({table})')
                existing = {row[1] for row in result.fetchall()}
                for name, definition in columns.items():
                    if name not in existing:
                        await conn.exec_driver_sql(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
                        if table == 'matches' and name == 'result_sent':
                            await conn.exec_driver_sql("UPDATE matches SET result_sent=1 WHERE status='completed' AND finished_at < datetime('now','-1 hour')")
        else:
            for statement in (
                'ALTER TABLE users ADD COLUMN IF NOT EXISTS last_name VARCHAR(128) NOT NULL DEFAULT \'\'',
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS team_name VARCHAR(40) NOT NULL DEFAULT 'Моя команда'",
                'ALTER TABLE promo_codes ADD COLUMN IF NOT EXISTS reward_xp INTEGER NOT NULL DEFAULT 0',
                'ALTER TABLE promo_codes ADD COLUMN IF NOT EXISTS reward_premium BOOLEAN NOT NULL DEFAULT FALSE',
                'ALTER TABLE promo_redemptions ADD COLUMN IF NOT EXISTS xp_awarded INTEGER NOT NULL DEFAULT 0',
                'ALTER TABLE promo_redemptions ADD COLUMN IF NOT EXISTS premium_awarded BOOLEAN NOT NULL DEFAULT FALSE',
                'ALTER TABLE matches ADD COLUMN IF NOT EXISTS result_sent BOOLEAN NOT NULL DEFAULT FALSE',
                "ALTER TABLE daily_tournaments ADD COLUMN IF NOT EXISTS title VARCHAR(100) NOT NULL DEFAULT ''",
                'ALTER TABLE daily_tournaments ADD COLUMN IF NOT EXISTS ends_at TIMESTAMPTZ',
                'ALTER TABLE trade_offers ADD COLUMN IF NOT EXISTS sender_coins INTEGER NOT NULL DEFAULT 0',
                'ALTER TABLE trade_offers ADD COLUMN IF NOT EXISTS sender_gems INTEGER NOT NULL DEFAULT 0',
                'ALTER TABLE trade_offers ADD COLUMN IF NOT EXISTS receiver_coins INTEGER NOT NULL DEFAULT 0',
                'ALTER TABLE trade_offers ADD COLUMN IF NOT EXISTS receiver_gems INTEGER NOT NULL DEFAULT 0',
                'ALTER TABLE trade_offers ADD COLUMN IF NOT EXISTS sender_confirmed BOOLEAN NOT NULL DEFAULT FALSE',
                'ALTER TABLE trade_offers ADD COLUMN IF NOT EXISTS receiver_confirmed BOOLEAN NOT NULL DEFAULT FALSE',
                "UPDATE matches SET result_sent=TRUE WHERE status='completed' AND finished_at < NOW() - INTERVAL '1 hour'"
            ):
                await conn.exec_driver_sql(statement)
