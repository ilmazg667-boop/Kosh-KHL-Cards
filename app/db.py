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
