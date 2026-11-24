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
        # One-time pack-price rebalance: triple only untouched defaults, preserving admin overrides.
        if DATABASE_URL.startswith('sqlite'):
            await conn.exec_driver_sql("UPDATE pack_configs SET price=price*3 WHERE (name='Обычный' AND price=2000) OR (name='Премиум' AND price=6000) OR (name='Элитный' AND price=14000) OR (name='Мифический' AND price=32000) OR (name='Легендарный' AND price=120) OR (name='Хроматический' AND price=300)")
        if DATABASE_URL.startswith('sqlite'):
            for table, columns in {
                'users': {'last_name': "VARCHAR(128) NOT NULL DEFAULT ''", 'team_name': "VARCHAR(40) NOT NULL DEFAULT 'Моя команда'"},
                'promo_codes': {'reward_xp': 'INTEGER NOT NULL DEFAULT 0', 'reward_premium': 'BOOLEAN NOT NULL DEFAULT 0'},
                'promo_redemptions': {'xp_awarded': 'INTEGER NOT NULL DEFAULT 0', 'premium_awarded': 'BOOLEAN NOT NULL DEFAULT 0'},
                'matches': {'result_sent': 'BOOLEAN NOT NULL DEFAULT 0'},
                'battlepass_progress': {'xp_curve': 'BOOLEAN NOT NULL DEFAULT 0'},
                'daily_tournaments': {'title': "VARCHAR(100) NOT NULL DEFAULT ''", 'ends_at': 'DATETIME'},
                'tournament_entries': {'games': 'INTEGER NOT NULL DEFAULT 0'},
                'trade_offers': {'sender_coins': 'INTEGER NOT NULL DEFAULT 0', 'sender_gems': 'INTEGER NOT NULL DEFAULT 0', 'receiver_coins': 'INTEGER NOT NULL DEFAULT 0', 'receiver_gems': 'INTEGER NOT NULL DEFAULT 0', 'sender_confirmed': 'BOOLEAN NOT NULL DEFAULT 0', 'receiver_confirmed': 'BOOLEAN NOT NULL DEFAULT 0', 'expires_at': 'DATETIME', 'sender_message_id': 'INTEGER NOT NULL DEFAULT 0', 'receiver_message_id': 'INTEGER NOT NULL DEFAULT 0'},
                'cards': {'rating_boosted': 'BOOLEAN NOT NULL DEFAULT 0'},
            }.items():
                result = await conn.exec_driver_sql(f'PRAGMA table_info({table})')
                existing = {row[1] for row in result.fetchall()}
                for name, definition in columns.items():
                    if name not in existing:
                        await conn.exec_driver_sql(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
                        if table == 'matches' and name == 'result_sent':
                            await conn.exec_driver_sql("UPDATE matches SET result_sent=1 WHERE status='completed' AND finished_at < datetime('now','-1 hour')")
            await conn.exec_driver_sql("UPDATE tournament_entries SET games=(SELECT COUNT(*) FROM matches m JOIN daily_tournaments t ON t.id=tournament_entries.tournament_id WHERE m.status='completed' AND m.created_at>=t.created_at AND m.created_at<=COALESCE(t.rewarded_at,CURRENT_TIMESTAMP) AND (m.home_user_id=tournament_entries.user_id OR m.away_user_id=tournament_entries.user_id)) WHERE games=0")
            await conn.exec_driver_sql("UPDATE cards SET ovr=MIN(ovr+2,100), rating_boosted=1 WHERE league='КХЛ' AND rating_boosted=0 AND NOT (player_name='Семён Кошелев' AND ovr IN (95,99))")
            await conn.exec_driver_sql("UPDATE trade_offers SET expires_at=CASE WHEN status='pending' THEN datetime(created_at,'+2 minutes') ELSE datetime(created_at,'+5 minutes') END WHERE status IN ('pending','negotiating') AND expires_at IS NULL")
        else:
            await conn.exec_driver_sql("UPDATE pack_configs SET price=price*3 WHERE (name='Обычный' AND price=2000) OR (name='Премиум' AND price=6000) OR (name='Элитный' AND price=14000) OR (name='Мифический' AND price=32000) OR (name='Легендарный' AND price=120) OR (name='Хроматический' AND price=300)")
            for statement in (
                'ALTER TABLE users ADD COLUMN IF NOT EXISTS last_name VARCHAR(128) NOT NULL DEFAULT \'\'',
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS team_name VARCHAR(40) NOT NULL DEFAULT 'Моя команда'",
                'ALTER TABLE promo_codes ADD COLUMN IF NOT EXISTS reward_xp INTEGER NOT NULL DEFAULT 0',
                'ALTER TABLE promo_codes ADD COLUMN IF NOT EXISTS reward_premium BOOLEAN NOT NULL DEFAULT FALSE',
                'ALTER TABLE promo_redemptions ADD COLUMN IF NOT EXISTS xp_awarded INTEGER NOT NULL DEFAULT 0',
                'ALTER TABLE promo_redemptions ADD COLUMN IF NOT EXISTS premium_awarded BOOLEAN NOT NULL DEFAULT FALSE',
                'ALTER TABLE matches ADD COLUMN IF NOT EXISTS result_sent BOOLEAN NOT NULL DEFAULT FALSE',
                'ALTER TABLE battlepass_progress ADD COLUMN IF NOT EXISTS xp_curve BOOLEAN NOT NULL DEFAULT FALSE',
                "ALTER TABLE daily_tournaments ADD COLUMN IF NOT EXISTS title VARCHAR(100) NOT NULL DEFAULT ''",
                'ALTER TABLE daily_tournaments ADD COLUMN IF NOT EXISTS ends_at TIMESTAMPTZ',
                'ALTER TABLE tournament_entries ADD COLUMN IF NOT EXISTS games INTEGER NOT NULL DEFAULT 0',
                'ALTER TABLE trade_offers ADD COLUMN IF NOT EXISTS sender_coins INTEGER NOT NULL DEFAULT 0',
                'ALTER TABLE trade_offers ADD COLUMN IF NOT EXISTS sender_gems INTEGER NOT NULL DEFAULT 0',
                'ALTER TABLE trade_offers ADD COLUMN IF NOT EXISTS receiver_coins INTEGER NOT NULL DEFAULT 0',
                'ALTER TABLE trade_offers ADD COLUMN IF NOT EXISTS receiver_gems INTEGER NOT NULL DEFAULT 0',
                'ALTER TABLE trade_offers ADD COLUMN IF NOT EXISTS sender_confirmed BOOLEAN NOT NULL DEFAULT FALSE',
                'ALTER TABLE trade_offers ADD COLUMN IF NOT EXISTS receiver_confirmed BOOLEAN NOT NULL DEFAULT FALSE',
                'ALTER TABLE trade_offers ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ',
                'ALTER TABLE trade_offers ADD COLUMN IF NOT EXISTS sender_message_id INTEGER NOT NULL DEFAULT 0',
                'ALTER TABLE trade_offers ADD COLUMN IF NOT EXISTS receiver_message_id INTEGER NOT NULL DEFAULT 0',
                'ALTER TABLE cards ADD COLUMN IF NOT EXISTS rating_boosted BOOLEAN NOT NULL DEFAULT FALSE',
                "UPDATE matches SET result_sent=TRUE WHERE status='completed' AND finished_at < NOW() - INTERVAL '1 hour'"
            ):
                await conn.exec_driver_sql(statement)
            await conn.exec_driver_sql("UPDATE tournament_entries e SET games=(SELECT COUNT(*) FROM matches m JOIN daily_tournaments t ON t.id=e.tournament_id WHERE m.status='completed' AND m.created_at>=t.created_at AND m.created_at<=COALESCE(t.rewarded_at,NOW()) AND (m.home_user_id=e.user_id OR m.away_user_id=e.user_id)) WHERE e.games=0")
            await conn.exec_driver_sql("UPDATE cards SET ovr=LEAST(ovr+2,100), rating_boosted=TRUE WHERE league='КХЛ' AND rating_boosted=FALSE AND NOT (player_name='Семён Кошелев' AND ovr IN (95,99))")
            await conn.exec_driver_sql("UPDATE trade_offers SET expires_at=CASE WHEN status='pending' THEN created_at + INTERVAL '2 minutes' ELSE created_at + INTERVAL '5 minutes' END WHERE status IN ('pending','negotiating') AND expires_at IS NULL")
