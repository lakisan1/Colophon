# Environment-driven configuration gathered in one place so secrets and file paths
# never hide inside feature code. Every knob is an environment variable; the only
# hardcoded value is an obviously-named dev fallback for SECRET_KEY, never a secret.
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Relative paths are resolved against the project root, not the current working
# directory, so `make dev` and `alembic upgrade head` open the same file from
# anywhere.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Used only when COLOPHON_SECRET_KEY is unset, i.e. local development. Shipped
# value is public, so it is a loud placeholder rather than a real signing key.
DEV_FALLBACK_SECRET_KEY = "colophon-dev-insecure-secret-change-me"


def _env_flag(name: str) -> bool:
    """A flag env var is on only when it is exactly '1' — anything else is off."""
    return os.environ.get(name, "").strip() == "1"


@dataclass
class Settings:
    """Mutable dataclass so tests can flip fields via monkeypatch.setattr."""

    secret_key: str
    database_url: str
    upload_dir: str
    backup_dir: str
    dev_auth_bypass: bool
    google_books_key: str | None
    cookie_secure: bool


def load_settings() -> Settings:
    """Read every COLOPHON_* env var fresh; called at import and by alembic env.py."""
    # SPEC §14 and D13 put the SQLite file and the photos under ./data, which is also
    # the directory the Docker volume is mounted on. Defaulting them anywhere else means
    # a container rebuild silently loses the library, so the default is a data-safety
    # choice rather than a stylistic one.
    db_path = _project_path(os.environ.get("COLOPHON_DB_PATH", "data/colophon.sqlite3"))
    upload_dir = _project_path(os.environ.get("COLOPHON_UPLOAD_DIR", "data/photos"))
    # SPEC §14/T10: nightly backups of both of the above, 30-day retention, and the
    # directory they land in must never be web-served. It sits next to the data it
    # copies so one volume mount carries the library and its recovery plan.
    backup_dir = _project_path(os.environ.get("COLOPHON_BACKUP_DIR", "data/backups"))
    return Settings(
        secret_key=os.environ.get("COLOPHON_SECRET_KEY", DEV_FALLBACK_SECRET_KEY),
        database_url=f"sqlite:///{db_path}",
        upload_dir=str(upload_dir),
        backup_dir=str(backup_dir),
        dev_auth_bypass=_env_flag("COLOPHON_DEV_AUTH_BYPASS"),
        # T5 names this variable literally, without the COLOPHON_ prefix.
        google_books_key=os.environ.get("GOOGLE_BOOKS_KEY") or None,
        cookie_secure=_env_flag("COLOPHON_COOKIE_SECURE"),
    )


def _project_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


settings = load_settings()
