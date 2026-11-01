# Engine and session factory for the single SQLite file. WAL mode and foreign
# keys are set per connection because SQLite pragmas apply per connection, not
# per database file (T2: WAL, single uvicorn worker).
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.settings import settings


class Base(DeclarativeBase):
    """Declarative base for the app's tables; Alembic autogenerate reads Base.metadata."""


def _ensure_sqlite_dir(url: str) -> None:
    """SQLite fails with a bare "unable to open database file" when the parent
    directory is missing, which is a confusing first run on a fresh clone."""
    prefix = "sqlite:///"
    if url.startswith(prefix):
        path = Path(url[len(prefix) :])
        if path.name and str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_dir(settings.database_url)
engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    # The FTS reindex triggers materialise temp b-trees; past a size threshold
    # SQLite spills them to a temp FILE, and in a container or sandbox with an
    # unwritable/oddly-behaving tmp that spill dies as a bare "unable to open
    # database file" mid-transaction. A personal library's temp sets are tiny,
    # so keeping them in memory is both faster and one less moving part.
    cursor.execute("PRAGMA temp_store=MEMORY")
    cursor.close()


def get_session() -> Iterator[Session]:
    """FastAPI-friendly session: one request scope, always closed."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
