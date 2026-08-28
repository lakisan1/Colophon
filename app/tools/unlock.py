"""Unlock an account, or set its password, from the server itself (T8).

Rate limiting locks an account after five failures, which is exactly the wrong
behaviour when the person typing is the owner of the machine. This tool is the way
out: it clears the failure counters for a username (and any IP that failed) and can
set a new password in the same breath.

    python -m app.tools.unlock --username admin
    python -m app.tools.unlock --username admin --password 'something long'
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import text

from app.auth import (
    clear_failures,
    drop_sessions_for_user,
    find_user,
    identifiers_for,
    set_password,
)
from app.db import SessionLocal


def unlock(username: str, *, password: str | None, log=print) -> int:
    """Clear throttling for `username`; optionally set a new password.

    Returns a process exit code: 0 on success, 2 when the account does not exist.
    """
    with SessionLocal() as session:
        user = find_user(session, username)
        if user is None:
            log(f"No such user: {username}")
            return 2

        # Failures are stored under "user:<name>" and under each IP that tried it.
        ip_keys = [
            row[0]
            for row in session.execute(
                text("SELECT DISTINCT identifier FROM login_attempts WHERE identifier LIKE 'ip:%'")
            )
        ]
        clear_failures(session, identifiers_for(ip=None, username=username) + ip_keys)
        log(f"Cleared failed-attempt counters for {username}.")

        if password:
            set_password(session, int(user["id"]), password)
            dropped = drop_sessions_for_user(session, int(user["id"]))
            log(f"Password updated; {dropped} active session(s) ended.")
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Unlock a Colophon account (T8).")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", help="Set this password while unlocking.")
    args = parser.parse_args(argv)
    return unlock(args.username, password=args.password)


if __name__ == "__main__":
    sys.exit(main())
