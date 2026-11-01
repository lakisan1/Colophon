# Authentication and session rules (SPEC §13, T8).
#
# The rules this module owns, stated once so the rest of the app can just ask:
#   * passwords are bcrypt, verified with a constant-time comparison;
#   * a session slides 30 days from last use but dies at 90 days from creation —
#     `expires_at` is the cap, computed at login, so the per-request check is one
#     comparison and the cap cannot be slid past;
#   * failed logins are recorded per identifier ("ip:1.2.3.4" and "user:admin"),
#     five inside fifteen minutes locks both, and a success clears them;
#   * the seeded admin/admin account is detected by checking its hash against the
#     known default, which is what drives the non-hideable banner (D11).
from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

import bcrypt
from sqlalchemy import text
from sqlalchemy.orm import Session

SESSION_COOKIE = "colophon_session"
CSRF_COOKIE = "colophon_csrf"

SLIDING_WINDOW = timedelta(days=30)
ABSOLUTE_LIFETIME = timedelta(days=90)

MAX_FAILED_ATTEMPTS = 5
ATTEMPT_WINDOW = timedelta(minutes=15)

DEFAULT_PASSWORD = "admin"

# Memo for the banner check. Comparing a bcrypt hash costs ~100 ms, which is far
# too much to pay on every page render; the result is cached per (user, hash) and
# invalidated automatically when the hash changes.
_default_password_cache: dict[tuple[int, str], bool] = {}


def now_utc() -> datetime:
    return datetime.now(UTC)


def as_utc(value: str | datetime) -> datetime:
    """Parse the ISO-8601 TEXT this project stores, tolerating SQLite's space form."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text_value = value.strip().replace(" ", "T")
    if text_value.endswith("Z"):
        text_value = text_value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text_value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def to_storage(value: datetime) -> str:
    """UTC ISO-8601 with a Z suffix: unambiguous, sortable, comparable."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # A malformed stored hash is a failed login, never a stack trace.
        return False


# A throwaway hash so a login attempt against an unknown username costs the same
# bcrypt work as one against a real account — without it, response timing
# enumerates which usernames exist (SPEC §13 keeps the message vague; this keeps
# the timing vague too).
_DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(18))


# --------------------------------------------------------------------------
# sessions
# --------------------------------------------------------------------------


def create_session(
    session: Session,
    user_id: int,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
    now: datetime | None = None,
) -> tuple[str, str]:
    """Open a session row. Returns (session_id, csrf_token)."""
    moment = now or now_utc()
    session_id = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    session.execute(
        text(
            """
            INSERT INTO sessions
                (id, user_id, csrf_token, created_at, last_seen_at, expires_at, ip, user_agent)
            VALUES (:id, :user_id, :csrf, :created, :seen, :expires, :ip, :ua)
            """
        ),
        {
            "id": session_id,
            "user_id": user_id,
            "csrf": csrf_token,
            "created": to_storage(moment),
            "seen": to_storage(moment),
            # The hard cap: no sliding can push a session past creation + 90 days.
            "expires": to_storage(moment + ABSOLUTE_LIFETIME),
            "ip": ip,
            "ua": (user_agent or "")[:200] or None,
        },
    )
    session.commit()
    return session_id, csrf_token


def load_session(
    session: Session, session_id: str | None, now: datetime | None = None
) -> dict | None:
    """The session row, or None if it is missing or past the hard cap."""
    if not session_id:
        return None
    record = session.execute(
        text(
            """
            SELECT s.id, s.user_id, s.csrf_token, s.created_at, s.last_seen_at, s.expires_at,
                   u.username, u.is_admin
            FROM sessions s JOIN users u ON u.id = s.user_id
            WHERE s.id = :id
            """
        ),
        {"id": session_id},
    ).first()
    if record is None:
        return None

    row = dict(record._mapping)
    moment = now or now_utc()
    if as_utc(row["expires_at"]) <= moment:
        revoke_session(session, row["id"])
        return None
    return row


def touch_session(session: Session, row: dict, now: datetime | None = None) -> None:
    """Slide the expiry forward without ever exceeding the hard cap."""
    moment = now or now_utc()
    sliding_end = moment + SLIDING_WINDOW
    cap_end = as_utc(row["created_at"]) + ABSOLUTE_LIFETIME
    new_expiry = sliding_end if sliding_end < cap_end else cap_end

    # Only write when something actually moves, so a page view is not an update.
    if as_utc(row["last_seen_at"]) + timedelta(minutes=1) < moment:
        session.execute(
            text("UPDATE sessions SET last_seen_at = :seen, expires_at = :expires WHERE id = :id"),
            {"seen": to_storage(moment), "expires": to_storage(new_expiry), "id": row["id"]},
        )
        session.commit()
        row["last_seen_at"] = to_storage(moment)
        row["expires_at"] = to_storage(new_expiry)


def revoke_session(session: Session, session_id: str) -> None:
    session.execute(text("DELETE FROM sessions WHERE id = :id"), {"id": session_id})
    session.commit()


# --------------------------------------------------------------------------
# login throttling (T8)
# --------------------------------------------------------------------------


def identifiers_for(*, ip: str | None, username: str) -> list[str]:
    """Rate limiting is keyed on both, so spraying one account from many IPs and
    spraying many accounts from one IP each hit a limit."""
    keys = [f"user:{username.casefold()}"]
    if ip:
        keys.append(f"ip:{ip}")
    return keys


def is_locked_out(session: Session, identifiers: list[str], now: datetime | None = None) -> bool:
    """Locked if ANY single identifier has too many failures in the window.

    Checked one key at a time on purpose: counting rows matching any of the keys
    would count the same attempt once per key, so five failures against
    "user:admin" from one IP would read as ten and lock at three attempts.
    """
    moment = now or now_utc()
    window_start = to_storage(moment - ATTEMPT_WINDOW)
    for identifier in identifiers:
        failures = int(
            session.execute(
                text(
                    "SELECT count(*) FROM login_attempts"
                    " WHERE identifier = :identifier AND failed_at > :window_start"
                ),
                {"identifier": identifier, "window_start": window_start},
            ).scalar_one()
        )
        if failures >= MAX_FAILED_ATTEMPTS:
            return True
    return False


def record_failure(session: Session, identifiers: list[str], now: datetime | None = None) -> None:
    moment = now or now_utc()
    for identifier in identifiers:
        session.execute(
            text(
                "INSERT INTO login_attempts (identifier, failed_at)"
                " VALUES (:identifier, :failed_at)"
            ),
            {"identifier": identifier, "failed_at": to_storage(moment)},
        )
    # Rows older than the window can no longer lock anybody out (is_locked_out
    # counts only failed_at > window start), so dropping them on every failure
    # keeps the table bounded under credential spraying without a cron job.
    session.execute(
        text("DELETE FROM login_attempts WHERE failed_at <= :cutoff"),
        {"cutoff": to_storage(moment - ATTEMPT_WINDOW)},
    )
    session.commit()


def clear_failures(session: Session, identifiers: list[str]) -> None:
    """A successful login resets the counter, and so does the unlock CLI (T8)."""
    for identifier in identifiers:
        session.execute(
            text("DELETE FROM login_attempts WHERE identifier = :identifier"),
            {"identifier": identifier},
        )
    session.commit()


# --------------------------------------------------------------------------
# accounts
# --------------------------------------------------------------------------


def find_user(session: Session, username: str) -> dict | None:
    record = session.execute(
        text("SELECT id, username, password_hash, is_admin FROM users WHERE username = :username"),
        {"username": username},
    ).first()
    return dict(record._mapping) if record else None


def uses_default_credentials(session: Session, user_id: int) -> bool:
    """True while this account's password is still a known default (D11).

    Both defaults count: admin/admin (the documented bootstrap pair) and
    demo/demo (the account `make seed` writes). A public-facing instance must
    not run on either, so the banner watches both with one memoised check.
    """
    record = session.execute(
        text("SELECT password_hash FROM users WHERE id = :id"), {"id": user_id}
    ).first()
    if record is None:
        return False
    password_hash = record[0]
    cache_key = (user_id, password_hash)
    cached = _default_password_cache.get(cache_key)
    if cached is None:
        from app.seed_data import DEMO_PASSWORD

        cached = any(
            verify_password(candidate, password_hash)
            for candidate in (DEFAULT_PASSWORD, DEMO_PASSWORD)
        )
        if len(_default_password_cache) > 8:
            _default_password_cache.clear()
        _default_password_cache[cache_key] = cached
    return cached


def set_password(session: Session, user_id: int, new_password: str) -> None:
    session.execute(
        text("UPDATE users SET password_hash = :hash WHERE id = :id"),
        {"hash": hash_password(new_password), "id": user_id},
    )
    session.commit()
    _default_password_cache.clear()


def drop_sessions_for_user(session: Session, user_id: int) -> int:
    """Used after a password change: old cookies must stop working."""
    result = session.execute(text("DELETE FROM sessions WHERE user_id = :id"), {"id": user_id})
    session.commit()
    return int(result.rowcount or 0)


def create_user(session: Session, username: str, password: str) -> int:
    """Create a plain (non-admin) account and return its id."""
    session.execute(
        text("INSERT INTO users (username, password_hash, is_admin) VALUES (:u, :h, 0)"),
        {"u": username, "h": hash_password(password)},
    )
    user_id = int(session.execute(text("SELECT last_insert_rowid()")).scalar_one())
    session.commit()
    return user_id


def delete_user(session: Session, user_id: int, username: str) -> None:
    """Permanently delete an account and everything it owns.

    The owner tables all carry ON DELETE CASCADE on owner_id, so deleting the
    user row removes their books (and photos/author/series/rooms/settings/loans
    link rows). The photo files on disk are removed first, and the account's
    login-throttle history is dropped so a re-created username starts clean.
    """
    from app.settings import settings
    from app.uploads import delete_upload

    filenames = session.execute(
        text("SELECT upload_filename, provider_filename FROM photos WHERE owner_id = :id"),
        {"id": user_id},
    ).all()
    for upload_filename, provider_filename in filenames:
        for filename in (upload_filename, provider_filename):
            if filename:
                delete_upload(settings.upload_dir, filename)
    session.execute(
        text("DELETE FROM login_attempts WHERE identifier = :i"),
        {"i": f"user:{username.casefold()}"},
    )
    session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
    session.commit()
