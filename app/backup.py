"""Nightly backups: the SQLite file, the photos, and 30-day retention (SPEC §14, T10).

Colophon has no import (D13), so this directory *is* the disaster recovery plan:
lose it and the library and every cover photo are gone for good. Three rules shape
everything below. (1) There is no `sqlite3` CLI on the build machine, so the copy
goes through the Python `sqlite3` module's backup API — which also means the copy is
taken from a live database through its WAL instead of cloning a file the server is
still writing to. (2) Every backup carries a `manifest.json`, because a directory of
opaque files is not a backup until you know what was in it. (3) Retention counts
from the timestamp in the directory name, never from file mtimes, so pruning cannot
be fooled by a restored or rsynced file and is testable without faking a clock.

The backup directory must never be web-served (T10); nothing here mounts it, and
`tests/test_backup.py` fails the build if anything ever does.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Names are fixed by contract: the timestamp in the directory name is what retention
# reads, and `manifest.json` is what `restore` refuses to run without.
BACKUP_PREFIX = "colophon-"
NAME_STAMP_FORMAT = "%Y-%m-%d-%H%M%S"
BACKUP_DB_NAME = "colophon.sqlite3"
BACKUP_PHOTOS_DIR = "photos"
MANIFEST_NAME = "manifest.json"
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# SPEC §14: nightly, 30-day retention.
DEFAULT_KEEP_DAYS = 30

# uploads.py writes to a sibling `.part` and renames, so one of those on disk is a
# half image by construction — never worth a byte of backup.
PART_SUFFIX = ".part"

# `colophon-2025-08-28-030000`, plus the `-2` collision suffix two runs in the same
# second get. Anything else in the backup directory is not ours to touch (the
# nightly log lives in that same directory).
_BACKUP_NAME_RE = re.compile(r"^colophon-(\d{4}-\d{2}-\d{2}-\d{6})(?:-\d+)?$")


class BackupError(RuntimeError):
    """Anything the CLI turns into one clear line on stderr and a non-zero exit."""


class RestoreError(BackupError):
    """A restore that would overwrite or restore something broken. Nothing was moved."""


@dataclass(frozen=True)
class BackupInfo:
    """What one backup directory holds, as recorded by its manifest.

    `broken` is not an error state of the backup call: `list_backups` reports a
    directory whose manifest never finished being written as broken, because an
    operator asking "what backups exist" needs to see the half-written one, not a
    traceback. Counts are then None rather than a reassuring zero.
    """

    name: str
    path: Path
    created_at: datetime
    alembic_revision: str | None
    book_count: int | None
    photo_row_count: int | None
    photo_files_copied: int
    file_sizes: dict[str, int] = field(default_factory=dict)
    broken: bool = False
    error: str | None = None

    @property
    def total_bytes(self) -> int:
        return sum(self.file_sizes.values())


@dataclass(frozen=True)
class RestoreInfo:
    """What a restore put where."""

    backup_name: str
    database_path: Path
    upload_dir: Path
    photos_restored: int
    replaced_existing: bool


def create_backup(
    *,
    db_path: str | Path,
    upload_dir: str | Path,
    backup_dir: str | Path,
    now: datetime | None = None,
) -> BackupInfo:
    """Write one complete backup and return what it contains.

    Every number in the manifest is read back out of the *copy*, not off the live
    database: a manifest that lies is worse than no manifest, and reading the copy
    also proves it opens and carries the schema it claims to.
    """
    source = Path(db_path).expanduser()
    if not source.is_file():
        raise BackupError(f"No database at {source}; run `alembic upgrade head` first.")

    moment = _as_utc(now)
    destination = _fresh_backup_dir(Path(backup_dir).expanduser(), moment)
    try:
        _copy_database(source, destination / BACKUP_DB_NAME)
        photo_sizes = _copy_photos(Path(upload_dir).expanduser(), destination / BACKUP_PHOTOS_DIR)
        revision, books, photos = _inspect_backup(destination / BACKUP_DB_NAME)
        file_sizes = {
            BACKUP_DB_NAME: (destination / BACKUP_DB_NAME).stat().st_size,
            **{f"{BACKUP_PHOTOS_DIR}/{name}": size for name, size in photo_sizes.items()},
        }
        info = BackupInfo(
            name=destination.name,
            path=destination,
            created_at=moment,
            alembic_revision=revision,
            book_count=books,
            photo_row_count=photos,
            photo_files_copied=len(photo_sizes),
            file_sizes=file_sizes,
        )
        _write_manifest(destination, info)
    except Exception:
        # A half-written backup is worse than none — `list` would have to guess and a
        # person might trust it. Remove it so tonight's retry starts from nothing.
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return info


def restore_backup(
    backup_path: str | Path,
    *,
    db_path: str | Path,
    upload_dir: str | Path,
    force: bool = False,
) -> RestoreInfo:
    """Restore a backup directory onto `db_path` and `upload_dir`.

    An existing destination is refused unless `force=True`: whoever types the wrong
    path to `restore` must not erase the library they were asked to rescue.

    Photos are copied in, never mirrored. Files already in the upload directory that
    this backup does not mention stay put — deleting a person's photos in the name of
    restoring them is the one mistake here that no backup can undo.

    The staged copy passes `PRAGMA integrity_check` before it is moved into place, so
    a truncated nightly file fails loudly instead of replacing a working database
    with an unusable one. The old file is unlinked, not overwritten, so a running
    uvicorn keeps serving it until the service is restarted — stop the app first.
    """
    source = Path(backup_path).expanduser()
    if not source.is_dir():
        raise RestoreError(f"No such backup directory: {source}")
    database = source / BACKUP_DB_NAME
    if not database.is_file():
        raise RestoreError(f"{source.name} has no {BACKUP_DB_NAME}; that backup is incomplete.")
    if not (source / MANIFEST_NAME).is_file():
        raise RestoreError(
            f"{source.name} has no {MANIFEST_NAME}; it never finished writing, so what it "
            "holds cannot be trusted. Refusing to restore it."
        )

    destination = Path(db_path).expanduser()
    replaced = destination.exists()
    if replaced and not force:
        raise RestoreError(
            f"{destination} already exists. Pass --force to replace it with {source.name}."
        )

    _assert_readable(database)

    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = destination.parent / f"{destination.name}.restoring"
    try:
        # A plain file copy is right here: the backup is a finished, quiesced snapshot,
        # unlike the live database `create_backup` has to read through a WAL.
        shutil.copy2(database, staged)
        _assert_readable(staged)
        staged.replace(destination)
    except Exception:
        # Refusing means refusing: no scratch file left next to somebody's library.
        staged.unlink(missing_ok=True)
        raise

    # A leftover -wal/-shm pair belongs to the database file that used to be here.
    # Applying its frames to a different file is corruption, so they go with it.
    for sidecar in (f"{destination}-wal", f"{destination}-shm"):
        Path(sidecar).unlink(missing_ok=True)

    upload = Path(upload_dir).expanduser()
    upload.mkdir(parents=True, exist_ok=True)
    restored_photos = 0
    photos_source = source / BACKUP_PHOTOS_DIR
    if photos_source.is_dir():
        for entry in sorted(photos_source.iterdir()):
            if not entry.is_file():
                continue
            shutil.copy2(entry, upload / entry.name)
            restored_photos += 1

    return RestoreInfo(
        backup_name=source.name,
        database_path=destination,
        upload_dir=upload,
        photos_restored=restored_photos,
        replaced_existing=replaced,
    )


def prune_backups(
    backup_dir: str | Path,
    *,
    keep_days: int = DEFAULT_KEEP_DAYS,
    now: datetime | None = None,
) -> list[Path]:
    """Delete backups older than `keep_days`; return the directories removed.

    Age comes from the directory name, not mtime: a restored or copied backup has a
    fresh mtime and would otherwise outlive retention forever, and no test could
    prove the rule without spoofing the filesystem clock.

    The newest backup is never deleted, however old it is. If the nightly job died
    three months ago, retention must still leave *something* behind — its purpose is
    bounding disk use, not emptying the only copy of the library.
    """
    if keep_days < 0:
        raise ValueError(f"keep_days must be 0 or more, got {keep_days}")
    directory = Path(backup_dir).expanduser()
    if not directory.is_dir():
        return []

    dated = _dated_backups(directory)
    if not dated:
        return []
    dated.sort(key=lambda item: item[0], reverse=True)

    cutoff = _as_utc(now) - timedelta(days=keep_days)
    deleted: list[Path] = []
    for index, (created, path) in enumerate(dated):
        if index == 0:
            continue
        if created < cutoff:
            shutil.rmtree(path)
            deleted.append(path)
    return sorted(deleted)


def list_backups(backup_dir: str | Path) -> list[BackupInfo]:
    """Every backup-shaped directory, newest first.

    Newest first because the top of that list is the one you would restore, and
    because a person scanning it is asking "when was the last good one?".
    """
    directory = Path(backup_dir).expanduser()
    if not directory.is_dir():
        return []
    infos = [_read_backup(path) for path in _backup_directories(directory)]
    return sorted(infos, key=lambda info: (info.created_at, info.name), reverse=True)


# ------------------------------------------------------------------- internals


def _as_utc(moment: datetime | None) -> datetime:
    """None means now; a naive datetime is taken as UTC, an aware one converted."""
    stamp = datetime.now(UTC) if moment is None else moment
    if stamp.tzinfo is None:
        return stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC)


def _backup_timestamp(name: str) -> datetime | None:
    """The timestamp encoded in a backup directory name, or None if it is not one."""
    match = _BACKUP_NAME_RE.match(name)
    if match is None:
        return None
    try:
        stamp = datetime.strptime(match.group(1), NAME_STAMP_FORMAT)
    except ValueError:
        # A name like colophon-2025-13-40-990000 is not a timestamp; leave it alone.
        return None
    return stamp.replace(tzinfo=UTC)


def _backup_directories(directory: Path) -> Iterable[Path]:
    """Only directories whose names are backup-shaped.

    `backup.log` sits in this same directory, so anything that is not a backup must
    be invisible to retention — a stray file is nobody's business but the operator's.
    """
    for entry in sorted(directory.iterdir()):
        if entry.is_dir() and _backup_timestamp(entry.name) is not None:
            yield entry


def _dated_backups(directory: Path) -> list[tuple[datetime, Path]]:
    """(timestamp, path) for every backup-shaped subdirectory."""
    dated: list[tuple[datetime, Path]] = []
    for path in _backup_directories(directory):
        stamp = _backup_timestamp(path.name)
        if stamp is not None:
            dated.append((stamp, path))
    return dated


def _fresh_backup_dir(backup_dir: Path, moment: datetime) -> Path:
    """`<backup_dir>/colophon-YYYY-MM-DD-HHMMSS/`, with a suffix if it is taken.

    The suffix is what makes the nightly script safe to run twice in a row: two runs
    inside the same second would otherwise collide with the directory the first one
    just wrote, and a backup that refuses to start is a night without a backup.
    """
    backup_dir.mkdir(parents=True, exist_ok=True)
    base = f"{BACKUP_PREFIX}{moment.strftime(NAME_STAMP_FORMAT)}"
    candidate = backup_dir / base
    collision = 2
    while candidate.exists():
        candidate = backup_dir / f"{base}-{collision}"
        collision += 1
    candidate.mkdir()
    return candidate


def _copy_database(source: Path, target: Path) -> None:
    """Copy with the Python `sqlite3` backup API, never the CLI (SPEC §14).

    Two reasons this is not `shutil.copy`. (1) A WAL database keeps committed rows in
    `*-wal`; copying the main file alone silently loses exactly those. The backup API
    reads through the WAL and writes a database that already contains them. (2) The
    server may be writing while the nightly job runs, and the API takes a consistent
    snapshot rather than a file torn mid-page.

    The source is opened read-only on purpose: an ordinary connection checkpoints and
    truncates the WAL when it closes, which would rewrite the production file during
    a backup. `as_uri()` is what makes `?mode=ro` a mode rather than part of a name.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    uri = f"{source.resolve().as_uri()}?mode=ro"
    try:
        source_conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise BackupError(f"Cannot open {source} for backup: {exc}") from exc
    try:
        target_conn = sqlite3.connect(target)
    except sqlite3.Error as exc:
        source_conn.close()
        raise BackupError(f"Cannot create {target}: {exc}") from exc
    try:
        source_conn.backup(target_conn)
    except sqlite3.Error as exc:
        raise BackupError(f"Backing up {source} failed: {exc}") from exc
    finally:
        target_conn.close()
        source_conn.close()


def _copy_photos(upload_dir: Path, target_dir: Path) -> dict[str, int]:
    """Copy every photo file that exists on disk; return name -> byte size.

    The directory is created even when there is nothing to copy, so restoring a
    backup with no photos is not mistaken for a truncated one. Files keep their own
    names, one level deep: uploads are flat by design (uploads.py refuses any name
    containing a slash), so the backup mirrors that and cannot smuggle a path out.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    sizes: dict[str, int] = {}
    if not upload_dir.is_dir():
        return sizes
    for entry in sorted(upload_dir.iterdir()):
        if not entry.is_file() or entry.name.endswith(PART_SUFFIX):
            continue
        shutil.copy2(entry, target_dir / entry.name)
        sizes[entry.name] = entry.stat().st_size
    return sizes


def _inspect_backup(database: Path) -> tuple[str | None, int, int]:
    """(alembic revision, book count, photo row count) of a finished backup file."""
    try:
        connection = sqlite3.connect(database)
    except sqlite3.Error as exc:
        raise BackupError(f"Cannot read back {database}: {exc}") from exc
    try:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        missing = [name for name in ("books", "photos") if name not in tables]
        if missing:
            # Backing up an unmigrated file and reporting "0 books" would look like an
            # empty library rather than a broken job. Fail the run instead.
            raise BackupError(
                f"{database} is not a Colophon database (no {', '.join(missing)} table); "
                "run `alembic upgrade head`."
            )
        revision = None
        if "alembic_version" in tables:
            row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
            revision = None if row is None else str(row[0])
        books = int(connection.execute("SELECT count(*) FROM books").fetchone()[0])
        photos = int(connection.execute("SELECT count(*) FROM photos").fetchone()[0])
    finally:
        connection.close()
    return revision, books, photos


def _assert_readable(database: Path) -> None:
    """Refuse a file SQLite itself does not accept."""
    try:
        connection = sqlite3.connect(database)
    except sqlite3.Error as exc:
        raise RestoreError(f"{database.name} cannot be opened: {exc}") from exc
    try:
        row = connection.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.Error as exc:
        raise RestoreError(f"{database.name} cannot be read: {exc}") from exc
    finally:
        connection.close()
    verdict = "" if row is None else str(row[0])
    if verdict != "ok":
        raise RestoreError(f"{database.name} fails SQLite integrity_check ({verdict}).")


def _write_manifest(destination: Path, info: BackupInfo) -> None:
    payload = {
        "created_at": info.created_at.strftime(TIMESTAMP_FORMAT),
        "alembic_revision": info.alembic_revision,
        "book_count": info.book_count,
        "photo_row_count": info.photo_row_count,
        "photo_files_copied": info.photo_files_copied,
        "files": info.file_sizes,
    }
    (destination / MANIFEST_NAME).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _read_backup(directory: Path) -> BackupInfo:
    """One backup directory, trusting the name when the manifest cannot be read."""
    stamp = _backup_timestamp(directory.name)
    assert stamp is not None  # callers filter through _backup_directories
    created_at = stamp
    error: str | None = None
    revision: str | None = None
    books: int | None = None
    photos: int | None = None
    photo_files = 0
    sizes: dict[str, int] = {}
    try:
        payload = json.loads((directory / MANIFEST_NAME).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"{MANIFEST_NAME} is not a JSON object")
        created_at = _parse_timestamp(payload.get("created_at")) or stamp
        revision = payload.get("alembic_revision")
        books = int(payload["book_count"])
        photos = int(payload["photo_row_count"])
        photo_files = int(payload["photo_files_copied"])
        sizes = {str(name): int(size) for name, size in dict(payload["files"]).items()}
        if not (directory / BACKUP_DB_NAME).is_file():
            error = f"no {BACKUP_DB_NAME} in the backup directory"
    except FileNotFoundError:
        error = f"no {MANIFEST_NAME} (the nightly run never finished writing)"
    except (OSError, ValueError, TypeError, KeyError) as exc:
        error = f"unreadable {MANIFEST_NAME}: {exc}"
    return BackupInfo(
        name=directory.name,
        path=directory,
        created_at=created_at,
        alembic_revision=revision,
        book_count=books,
        photo_row_count=photos,
        photo_files_copied=photo_files,
        file_sizes=sizes,
        broken=error is not None,
        error=error,
    )


def _parse_timestamp(raw: object) -> datetime | None:
    """Manifest timestamps are UTC with a trailing Z; tolerate an offset too."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
