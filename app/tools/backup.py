"""Nightly backups from the command line (SPEC §14, T10).

    python -m app.tools.backup create
    python -m app.tools.backup list
    python -m app.tools.backup prune --keep-days 30
    python -m app.tools.backup restore colophon-2025-08-01-030000 --force

Paths default to the environment the app itself reads (COLOPHON_DB_PATH,
COLOPHON_UPLOAD_DIR, COLOPHON_BACKUP_DIR), so a cron line or timer needs no
arguments; the flags exist for pointing a restore at a laptop. One fact per line and
exit 0 only on success, so `scripts/nightly-backup.sh` can alert on the status alone.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from app.backup import (
    BACKUP_DB_NAME,
    DEFAULT_KEEP_DAYS,
    BackupError,
    create_backup,
    list_backups,
    prune_backups,
    restore_backup,
)
from app.settings import settings


def _database_file() -> Path:
    """The SQLite file behind settings.database_url — the URL is the one source."""
    prefix = "sqlite:///"
    url = settings.database_url
    return Path(url[len(prefix) :] if url.startswith(prefix) else url)


def create(
    *,
    db_path: Path,
    upload_dir: Path,
    backup_dir: Path,
    log: Callable[[str], None] = print,
) -> int:
    """Write one dated backup of the database and every photo file."""
    info = create_backup(
        db_path=db_path,
        upload_dir=upload_dir,
        backup_dir=backup_dir,
    )
    log(f"Backup written to {info.path}")
    log(f"  created at       {info.created_at:%Y-%m-%dT%H:%M:%SZ}")
    log(f"  database         {BACKUP_DB_NAME} ({info.file_sizes[BACKUP_DB_NAME]} bytes)")
    log(f"  alembic revision {info.alembic_revision or 'unknown'}")
    log(f"  books            {info.book_count}")
    log(f"  photo rows       {info.photo_row_count}")
    log(f"  photo files      {info.photo_files_copied} copied")
    log(f"  backup total     {info.total_bytes} bytes")
    return 0


def ls(*, backup_dir: Path, log: Callable[[str], None] = print) -> int:
    """List backups, newest first; a half-written one is reported, not fatal."""
    infos = list_backups(backup_dir)
    if not infos:
        log(f"No backups in {backup_dir}")
        return 0
    for info in infos:
        if info.broken:
            log(f"{info.name}  BROKEN  {info.error}")
            continue
        log(
            f"{info.name}  rev {info.alembic_revision or '?'}  "
            f"{info.book_count} books  {info.photo_row_count} photo rows  "
            f"{info.photo_files_copied} photo files  {info.total_bytes} bytes"
        )
    broken = sum(1 for info in infos if info.broken)
    log(f"{len(infos)} backup(s) in {backup_dir}" + (f", {broken} broken" if broken else ""))
    return 0


def prune(*, backup_dir: Path, keep_days: int, log: Callable[[str], None] = print) -> int:
    """Delete backups older than `keep_days`, keeping at least the newest."""
    deleted = prune_backups(backup_dir, keep_days=keep_days)
    for path in deleted:
        log(f"Deleted {path}")
    log(f"Removed {len(deleted)} backup(s) older than {keep_days} day(s) from {backup_dir}")
    return 0


def restore(
    backup: str,
    *,
    db_path: Path,
    upload_dir: Path,
    backup_dir: Path,
    force: bool,
    log: Callable[[str], None] = print,
) -> int:
    """Restore a backup onto the app's database and upload directories."""
    source = _resolve(backup, backup_dir=backup_dir)
    result = restore_backup(
        source,
        db_path=db_path,
        upload_dir=upload_dir,
        force=force,
    )
    log(f"Restored {result.backup_name}")
    log(
        f"  database  {result.database_path}"
        + (" (replaced an existing database)" if result.replaced_existing else "")
    )
    log(f"  photos    {result.photos_restored} file(s) into {result.upload_dir}")
    # A running uvicorn holds pooled connections to the file it replaced, so the new
    # one is only picked up when the process starts again. Say so instead of implying
    # the swap is live.
    log("Done. Restart the service so it opens the restored file.")
    return 0


def _resolve(backup: str, *, backup_dir: Path) -> Path:
    """Accept a path or just a directory name inside the backup directory."""
    candidate = Path(backup).expanduser()
    if candidate.is_dir():
        return candidate
    inside = backup_dir / backup
    if inside.is_dir():
        return inside
    raise BackupError(f"No such backup: {backup} (looked in {backup_dir})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Back up and restore Colophon (SPEC §14, T10).")
    # Every subcommand takes the same three path overrides, so a restore onto another
    # machine is one flag, not an edit to the environment.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--db-path", help="SQLite file to back up or restore (default: app settings)."
    )
    common.add_argument("--upload-dir", help="Photo directory (default: app settings).")
    common.add_argument("--backup-dir", help="Backup directory (default: app settings).")

    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("create", parents=[common], help="Write a dated backup.")
    subcommands.add_parser("list", parents=[common], help="Show backups, newest first.")
    prune_parser = subcommands.add_parser("prune", parents=[common], help="Delete old backups.")
    prune_parser.add_argument("--keep-days", type=int, default=DEFAULT_KEEP_DAYS)
    restore_parser = subcommands.add_parser("restore", parents=[common], help="Restore a backup.")
    restore_parser.add_argument("backup", help="Backup directory name or path.")
    restore_parser.add_argument(
        "--force", action="store_true", help="Replace an existing database file."
    )

    args = parser.parse_args(argv)
    # Settings stay in settings.py: the CLI resolves the three paths once and passes
    # them down, so no function here has to know where a default came from.
    db_path = Path(args.db_path).expanduser() if args.db_path else _database_file()
    upload_dir = (
        Path(args.upload_dir).expanduser() if args.upload_dir else Path(settings.upload_dir)
    )
    backup_dir = Path(args.backup_dir or settings.backup_dir)

    try:
        if args.command == "create":
            return create(db_path=db_path, upload_dir=upload_dir, backup_dir=backup_dir)
        if args.command == "list":
            return ls(backup_dir=backup_dir)
        if args.command == "prune":
            return prune(backup_dir=backup_dir, keep_days=args.keep_days)
        return restore(
            args.backup,
            db_path=db_path,
            upload_dir=upload_dir,
            backup_dir=backup_dir,
            force=args.force,
        )
    except BackupError as exc:
        # One line on stderr, non-zero exit: that is what a timer alerts on.
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
