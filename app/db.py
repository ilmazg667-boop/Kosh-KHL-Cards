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
                'users': {'last_name': "VARCHAR(128) NOT NULL DEFAULT ''"},
                'promo_codes': {'reward_xp': 'INTEGER NOT NULL DEFAULT 0', 'reward_premium': 'BOOLEAN NOT NULL DEFAULT 0'},
                'promo_redemptions': {'xp_awarded': 'INTEGER NOT NULL DEFAULT 0', 'premium_awarded': 'BOOLEAN NOT NULL DEFAULT 0'},
                'matches': {'result_sent': 'BOOLEAN NOT NULL DEFAULT 0'},
            }.items():
                result = await conn.exec_driver_sql(f'PRAGMA table_info({table})')
                existing = {row[1] for row in result.fetchall()}
                for name, definition in columns.items():
                    if name not in existing:
                        await conn.exec_driver_sql(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
        else:
            for statement in (
                'ALTER TABLE users ADD COLUMN IF NOT EXISTS last_name VARCHAR(128) NOT NULL DEFAULT \'\'',
                'ALTER TABLE promo_codes ADD COLUMN IF NOT EXISTS reward_xp INTEGER NOT NULL DEFAULT 0',
                'ALTER TABLE promo_codes ADD COLUMN IF NOT EXISTS reward_premium BOOLEAN NOT NULL DEFAULT FALSE',
                'ALTER TABLE promo_redemptions ADD COLUMN IF NOT EXISTS xp_awarded INTEGER NOT NULL DEFAULT 0',
                'ALTER TABLE promo_redemptions ADD COLUMN IF NOT EXISTS premium_awarded BOOLEAN NOT NULL DEFAULT FALSE',
                'ALTER TABLE matches ADD COLUMN IF NOT EXISTS result_sent BOOLEAN NOT NULL DEFAULT FALSE',
            ):
                await conn.exec_driver_sql(statement)
