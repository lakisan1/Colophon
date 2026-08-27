# Engine and session factory for the single SQLite file. WAL mode and foreign
# keys are set per connection because SQLite pragmas apply per connection, not
# per database file (T2: WAL, single uvicorn worker).
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.settings import settings


class Base(DeclarativeBase):
    """Declarative base for M1's tables; Alembic autogenerate reads Base.metadata."""


engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def get_session() -> Iterator[Session]:
    """FastAPI-friendly session: one request scope, always closed."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
