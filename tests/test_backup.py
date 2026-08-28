# Nightly backups (SPEC §14, T10; PLAN M7 verify).
#
# Backups are the one feature whose failure is invisible until the day it is needed,
# so every test here works on a throwaway library in `tmp_path` (the dev database and
# its photos are never opened) and asserts against files and rows rather than log
# lines. The centre of gravity is the circle the PLAN demands: back a seeded library
# up, *destroy* the original database and the photo file, restore, and prove the
# library is the library again — same book count, byte-identical photo, working
# SELECT, working FTS MATCH. The rest covers the two ways this goes wrong in
# practice: a WAL whose uncheckpointed rows a naive file copy silently loses, and a
# retention rule that deletes the wrong directory.
from __future__ import annotations

import io
import json
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import Engine, text

from app.backup import (
    BACKUP_DB_NAME,
    MANIFEST_NAME,
    NAME_STAMP_FORMAT,
    BackupError,
    RestoreError,
    create_backup,
    list_backups,
    prune_backups,
    restore_backup,
)
from app.main import app as served_app
from app.main import create_app
from app.seed import SeedSummary, seed
from app.settings import PROJECT_ROOT, load_settings, settings
from app.templating import STATIC_DIR
from app.tools import backup as backup_cli
from tests.alembic_runner import engine_for, run_alembic

# A title that exists exactly once in the M2 fixture, so a SELECT and an FTS MATCH
# against it cannot be satisfied by some other book.
KNOWN_TITLE = "Neuromancer"

# Fixed clock: retention has to be provable from directory names alone, never from
# whatever time the machine running the suite thinks it is.
NOW = datetime(2025, 8, 28, 3, 0, 0, tzinfo=UTC)

PHOTO_FILENAME = "book-cover-front-0123456789abcdef.jpg"

# Written by hand into a live WAL to prove an uncheckpointed page still gets copied.
WAL_MARKER = "wal-marker-5f3d8a"


@dataclass
class Library:
    """A migrated, seeded database plus one real photo file on disk."""

    db_path: Path
    upload_dir: Path
    backup_dir: Path
    engine: Engine
    summary: SeedSummary
    photo_bytes: bytes

    @property
    def photo_path(self) -> Path:
        return self.upload_dir / PHOTO_FILENAME

    def dispose(self) -> None:
        """Close every handle so the files underneath can be deleted and replaced."""
        self.engine.dispose()


@pytest.fixture()
def library(tmp_path: Path) -> Library:
    """The M2 fixture database, plus a photo that actually exists on disk.

    The seeded `photos` rows carry provider filenames only — rows with no file behind
    them, which app/routers/books.py has to cope with — so a backup test built on the
    fixture alone would pass even if the backup copied zero photos.
    """
    db_path = tmp_path / "colophon.sqlite3"
    upload_dir = tmp_path / "photos"
    backup_dir = tmp_path / "backups"
    upload_dir.mkdir()
    backup_dir.mkdir()

    run_alembic(db_path, "head")
    engine = engine_for(db_path)
    summary = seed(engine)

    photo_bytes = _jpeg_bytes()
    (upload_dir / PHOTO_FILENAME).write_bytes(photo_bytes)
    with engine.begin() as conn:
        owner = int(conn.execute(text("SELECT id FROM users WHERE username = 'demo'")).scalar_one())
        # A book with no photo row yet, so the unique (book_id, slot) constraint holds.
        book = int(
            conn.execute(
                text(
                    "SELECT id FROM books WHERE id NOT IN (SELECT book_id FROM photos)"
                    " ORDER BY id LIMIT 1"
                )
            ).scalar_one()
        )
        # upload_filename, not provider_filename: this is a file the owner put there.
        conn.execute(
            text(
                "INSERT INTO photos (book_id, owner_id, slot, upload_filename)"
                " VALUES (:book, :owner, 'front', :name)"
            ),
            {"book": book, "owner": owner, "name": PHOTO_FILENAME},
        )

    yield Library(
        db_path=db_path,
        upload_dir=upload_dir,
        backup_dir=backup_dir,
        engine=engine,
        summary=summary,
        photo_bytes=photo_bytes,
    )
    engine.dispose()


def _jpeg_bytes() -> bytes:
    """A real JPEG: "restored byte-identical" has to be proven against image bytes."""
    image = Image.new("RGB", (64, 84), (178, 34, 52))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def counts(engine: Engine) -> tuple[int, int]:
    """(books, photo rows) straight out of the file."""
    with engine.connect() as conn:
        books = int(conn.execute(text("SELECT count(*) FROM books")).scalar_one())
        photos = int(conn.execute(text("SELECT count(*) FROM photos")).scalar_one())
    return books, photos


def book_id(engine: Engine, title: str) -> int:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id FROM books WHERE title = :t"), {"t": title})
        return int(rows.scalar_one())


def fts_rowids(engine: Engine, term: str) -> set[int]:
    """Rowids the FTS index matches, as a phrase query."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT rowid FROM books_fts WHERE books_fts MATCH :term"),
            {"term": f'"{term}"'},
        )
        return {int(row[0]) for row in rows}


def make_dated_backup(backup_dir: Path, *, days_ago: int) -> Path:
    """A backup-shaped directory as it would look after `days_ago` nights."""
    created = NOW - timedelta(days=days_ago)
    path = backup_dir / f"colophon-{created.strftime(NAME_STAMP_FORMAT)}"
    path.mkdir(parents=True)
    (path / BACKUP_DB_NAME).write_bytes(b"a placeholder for a finished nightly file")
    (path / MANIFEST_NAME).write_text(
        json.dumps(
            {
                "created_at": created.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "alembic_revision": "0003",
                "book_count": 10,
                "photo_row_count": 0,
                "photo_files_copied": 0,
                "files": {BACKUP_DB_NAME: 41},
            }
        ),
        encoding="utf-8",
    )
    return path


def source_revision(db_path: Path) -> str:
    """The revision recorded inside a database file, read with plain sqlite3."""
    connection = sqlite3.connect(db_path)
    try:
        return str(connection.execute("SELECT version_num FROM alembic_version").fetchone()[0])
    finally:
        connection.close()


# ------------------------------------------------------------------- the circle


def test_the_full_circle_backup_destroy_restore(library: Library) -> None:
    """PLAN M7: back up, destroy the originals, restore, and nothing was lost."""
    books_before, photos_before = counts(library.engine)
    assert books_before == library.summary.books
    known_id = book_id(library.engine, KNOWN_TITLE)
    library.dispose()

    info = create_backup(
        db_path=library.db_path,
        upload_dir=library.upload_dir,
        backup_dir=library.backup_dir,
        now=NOW,
    )
    assert info.book_count == books_before
    assert info.photo_row_count == photos_before
    assert info.photo_files_copied == 1

    # Destroy the originals the way a failed disk or an `rm -rf data/` would.
    library.db_path.unlink()
    library.photo_path.unlink()
    assert not library.db_path.exists()
    assert not library.photo_path.exists()

    restored = restore_backup(
        info.path,
        db_path=library.db_path,
        upload_dir=library.upload_dir,
        force=True,
    )
    assert restored.photos_restored == 1
    assert restored.replaced_existing is False
    assert library.db_path.is_file()

    # The photo is back, and it is the same file byte for byte.
    assert library.photo_path.is_file()
    assert library.photo_path.read_bytes() == library.photo_bytes

    engine = engine_for(library.db_path)
    try:
        assert counts(engine) == (books_before, photos_before)
        assert book_id(engine, KNOWN_TITLE) == known_id
        # Search still works: the FTS index came along inside the same file.
        assert known_id in fts_rowids(engine, KNOWN_TITLE)
    finally:
        engine.dispose()


def test_the_restored_file_keeps_its_fts_index_and_working_triggers(library: Library) -> None:
    """`books_fts` is a virtual table plus triggers in the file; both must come back."""
    info = create_backup(
        db_path=library.db_path,
        upload_dir=library.upload_dir,
        backup_dir=library.backup_dir,
        now=NOW,
    )
    library.dispose()
    library.db_path.unlink()
    restore_backup(
        info.path,
        db_path=library.db_path,
        upload_dir=library.upload_dir,
        force=True,
    )

    engine = engine_for(library.db_path)
    try:
        with engine.connect() as conn:
            objects = {
                str(row[0]): str(row[1])
                for row in conn.execute(text("SELECT name, type FROM sqlite_master"))
            }
        assert objects.get("books_fts") == "table"
        triggers = {
            name
            for name, kind in objects.items()
            if kind == "trigger" and name.startswith("books_fts")
        }
        assert triggers == {
            "books_fts_after_insert",
            "books_fts_after_update",
            "books_fts_after_delete",
        }

        # Present is not the same as live: a book written after the restore has to be
        # searchable, which only happens if the triggers survived with the file.
        with engine.begin() as conn:
            owner = int(
                conn.execute(text("SELECT id FROM users WHERE username = 'demo'")).scalar_one()
            )
            conn.execute(
                text(
                    "INSERT INTO books (owner_id, title, ownership_status, reading_state)"
                    " VALUES (:o, :t, 'owned', 'unread')"
                ),
                {"o": owner, "t": "Zzyzx Probe Book"},
            )
        assert book_id(engine, "Zzyzx Probe Book") in fts_rowids(engine, "zzyzx")
    finally:
        engine.dispose()


def test_restore_refuses_an_existing_database_without_force(library: Library) -> None:
    """The mistake this guards against is restoring onto the library you meant to keep."""
    info = create_backup(
        db_path=library.db_path,
        upload_dir=library.upload_dir,
        backup_dir=library.backup_dir,
        now=NOW,
    )
    library.dispose()
    database_before = library.db_path.read_bytes()
    library.photo_path.unlink()

    with pytest.raises(RestoreError, match="already exists"):
        restore_backup(
            info.path,
            db_path=library.db_path,
            upload_dir=library.upload_dir,
        )

    # Refusing means refusing: nothing moved, not even partially.
    assert library.db_path.read_bytes() == database_before
    assert not library.photo_path.exists()

    restore_backup(
        info.path,
        db_path=library.db_path,
        upload_dir=library.upload_dir,
        force=True,
    )
    assert library.photo_path.read_bytes() == library.photo_bytes


def test_a_forced_restore_discards_the_sidecar_wal_of_the_file_it_replaced(
    library: Library,
) -> None:
    """A leftover `*-wal`/`*-shm` pair belongs to the database that used to be here.

    Applying its frames to a different file is corruption, so they leave with the file
    they came from. SQLite ignores an invalid WAL header, so what is observable — and
    what this promises — is that the stale pair is gone and the file reads clean.
    """
    info = create_backup(
        db_path=library.db_path,
        upload_dir=library.upload_dir,
        backup_dir=library.backup_dir,
        now=NOW,
    )
    library.dispose()
    stale_wal = Path(f"{library.db_path}-wal")
    stale_shm = Path(f"{library.db_path}-shm")
    stale_wal.write_bytes(b"left behind by a crash")
    stale_shm.write_bytes(b"left behind by a crash")

    restore_backup(
        info.path,
        db_path=library.db_path,
        upload_dir=library.upload_dir,
        force=True,
    )

    assert not stale_wal.exists()
    assert not stale_shm.exists()
    engine = engine_for(library.db_path)
    try:
        assert counts(engine)[0] == library.summary.books
    finally:
        engine.dispose()


def test_restore_refuses_a_half_written_backup(tmp_path: Path) -> None:
    """A directory whose manifest never finished writing cannot be trusted."""
    broken = tmp_path / "colophon-2025-08-01-030000"
    broken.mkdir()
    (broken / BACKUP_DB_NAME).write_bytes(b"truncated")

    with pytest.raises(RestoreError, match=MANIFEST_NAME):
        restore_backup(
            broken,
            db_path=tmp_path / "target.sqlite3",
            upload_dir=tmp_path / "uploads",
        )
    assert not (tmp_path / "target.sqlite3").exists()


def test_restore_refuses_a_database_that_fails_integrity_check(tmp_path: Path) -> None:
    """A backup truncated by a full disk must not become somebody's library.

    The staged copy is checked before it replaces anything, so when this fires the
    destination is still the working database and nothing was left behind.
    """
    broken = tmp_path / "colophon-2025-08-01-030000"
    broken.mkdir()
    (broken / BACKUP_DB_NAME).write_bytes(b"truncated by a full disk " * 200)
    (broken / MANIFEST_NAME).write_text(
        json.dumps(
            {
                "created_at": "2025-08-01T03:00:00Z",
                "alembic_revision": "0003",
                "book_count": 210,
                "photo_row_count": 12,
                "photo_files_copied": 0,
                "files": {BACKUP_DB_NAME: 4800},
            }
        ),
        encoding="utf-8",
    )
    target = tmp_path / "target.sqlite3"
    working = b"the library that still works"
    target.write_bytes(working)

    # force=True: this is the operator who *is* trying to overwrite a live database,
    # and the integrity check is the last thing standing between them and an empty one.
    with pytest.raises(RestoreError, match=r"integrity_check|cannot be"):
        restore_backup(broken, db_path=target, upload_dir=tmp_path / "uploads", force=True)

    assert target.read_bytes() == working
    assert not Path(f"{target}.restoring").exists()


def test_restore_reports_a_missing_backup_directory(tmp_path: Path) -> None:
    with pytest.raises(RestoreError, match="No such backup"):
        restore_backup(
            tmp_path / "colophon-1999-01-01-000000",
            db_path=tmp_path / "target.sqlite3",
            upload_dir=tmp_path / "uploads",
        )


# ---------------------------------------------------------------------- the WAL


def test_an_uncheckpointed_wal_is_backed_up_and_left_alone(tmp_path: Path) -> None:
    """Committed rows live in `*-wal` until a checkpoint, and the copy must have them.

    Colophon's database is always in WAL mode (app/db.py), so a plain file copy of the
    main `.sqlite3` loses exactly the rows written since the last checkpoint — the
    very data a nightly backup exists to protect. This holds a WAL full of committed
    data open and checks both directions: the copy carries the marker, and the source
    was not checkpointed on the way past it.
    """
    db_path = tmp_path / "library.sqlite3"
    run_alembic(db_path, "head")

    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        "INSERT INTO users (username, password_hash) VALUES (?, ?)", (WAL_MARKER, "hash")
    )
    connection.commit()

    wal = Path(f"{db_path}-wal")
    assert wal.is_file() and wal.stat().st_size > 0, "the WAL should hold the committed row"
    # The honest precondition: that row is not in the main file yet.
    assert WAL_MARKER.encode() not in db_path.read_bytes()

    info = create_backup(
        db_path=db_path,
        upload_dir=tmp_path / "photos",
        backup_dir=tmp_path / "backups",
        now=NOW,
    )

    # The copy carries the uncheckpointed row, as bytes and as a queryable row.
    assert WAL_MARKER.encode() in (info.path / BACKUP_DB_NAME).read_bytes()
    # The source was not checkpointed: a checkpoint would have written that page into
    # the main file, which a read-only connection cannot do (SPEC §14).
    assert WAL_MARKER.encode() not in db_path.read_bytes()
    assert wal.is_file() and wal.stat().st_size > 0

    backup_conn = sqlite3.connect(info.path / BACKUP_DB_NAME)
    try:
        found = backup_conn.execute(
            "SELECT count(*) FROM users WHERE username = ?", (WAL_MARKER,)
        ).fetchone()[0]
    finally:
        backup_conn.close()
    assert found == 1

    # And the source is undamaged: still WAL mode, still seeing its own row.
    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert (
        connection.execute(
            "SELECT count(*) FROM users WHERE username = ?", (WAL_MARKER,)
        ).fetchone()[0]
        == 1
    )
    connection.close()


def test_no_sqlite_cli_is_ever_invoked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, library: Library
) -> None:
    """SPEC §14: the build machine has no `sqlite3` binary, so nothing may shell out.

    Nothing here spawns a process today, but a future "just use .dump()" edit would
    pass every other test in this file and then fail on the server, so the absence of
    a subprocess is asserted rather than assumed.
    """
    library.dispose()

    def no_processes(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("backups must use the Python sqlite3 module, not a CLI")

    monkeypatch.setattr(subprocess, "Popen", no_processes)
    monkeypatch.setattr(subprocess, "run", no_processes)
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    info = create_backup(
        db_path=library.db_path,
        upload_dir=library.upload_dir,
        backup_dir=library.backup_dir,
        now=NOW,
    )
    restored = restore_backup(
        info.path,
        db_path=tmp_path / "elsewhere.sqlite3",
        upload_dir=tmp_path / "elsewhere-photos",
    )
    assert counts(engine_for(restored.database_path))[0] == library.summary.books


# -------------------------------------------------------------------- the manifest


def test_the_manifest_records_the_file_it_describes(library: Library) -> None:
    """A backup you cannot describe is not a backup: revision, counts, per-file sizes."""
    info = create_backup(
        db_path=library.db_path,
        upload_dir=library.upload_dir,
        backup_dir=library.backup_dir,
        now=NOW,
    )
    # The revision is read out of the backed-up file, not out of the migration scripts.
    assert info.alembic_revision == source_revision(library.db_path)

    payload = json.loads((info.path / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert payload["created_at"] == "2025-08-28T03:00:00Z"
    assert payload["alembic_revision"] == source_revision(library.db_path)
    assert payload["book_count"] == library.summary.books
    assert payload["photo_row_count"] == library.summary.photos + 1
    assert payload["photo_files_copied"] == 1
    # Per-file sizes for everything the backup wrote, so a short file is detectable
    # without opening it.
    assert payload["files"][BACKUP_DB_NAME] == (info.path / BACKUP_DB_NAME).stat().st_size
    assert payload["files"][f"photos/{PHOTO_FILENAME}"] == len(library.photo_bytes)
    assert info.total_bytes == sum(payload["files"].values())


def test_every_photo_file_on_disk_is_copied_and_half_writes_are_not(library: Library) -> None:
    """The photos directory is copied as it is, minus the `.part` files by definition.

    uploads.py writes to a sibling `.part` and renames, so one of those on disk is a
    half image; a file that no `photos` row points at is still copied, because a row
    can be lost while the picture is not (D13: there is no re-import).
    """
    orphan = library.upload_dir / "orphan-front-ffee.jpg"
    orphan.write_bytes(b"an image whose row was deleted")
    half = library.upload_dir / "book9-front-aaaa.part"
    half.write_bytes(b"half a jpeg")

    info = create_backup(
        db_path=library.db_path,
        upload_dir=library.upload_dir,
        backup_dir=library.backup_dir,
        now=NOW,
    )

    assert info.photo_files_copied == 2
    copied = sorted(p.name for p in (info.path / "photos").iterdir())
    assert copied == sorted([PHOTO_FILENAME, orphan.name])
    assert (info.path / "photos" / orphan.name).read_bytes() == orphan.read_bytes()


def test_the_photos_directory_exists_even_when_there_are_no_photos(tmp_path: Path) -> None:
    """Otherwise an empty-photo backup looks identical to a truncated one."""
    db_path = tmp_path / "library.sqlite3"
    run_alembic(db_path, "head")

    info = create_backup(
        db_path=db_path,
        upload_dir=tmp_path / "photos",
        backup_dir=tmp_path / "backups",
        now=NOW,
    )

    assert (info.path / "photos").is_dir()
    assert info.photo_files_copied == 0


def test_two_runs_in_the_same_second_both_land(library: Library) -> None:
    """The nightly script must be safe to run twice; a collision is not an excuse."""
    library.dispose()
    first = create_backup(
        db_path=library.db_path,
        upload_dir=library.upload_dir,
        backup_dir=library.backup_dir,
        now=NOW,
    )
    second = create_backup(
        db_path=library.db_path,
        upload_dir=library.upload_dir,
        backup_dir=library.backup_dir,
        now=NOW,
    )

    assert first.path != second.path
    assert second.path.is_dir()
    # Both are still listable backups, and the suffixed name parses as one.
    assert [info.name for info in list_backups(library.backup_dir)] == [second.name, first.name]
    assert all(not info.broken for info in list_backups(library.backup_dir))


def test_a_missing_database_is_a_clear_error(tmp_path: Path) -> None:
    """The nightly job's most likely failure is a path nobody configured."""
    with pytest.raises(BackupError, match="No database"):
        create_backup(
            db_path=tmp_path / "nope.sqlite3",
            upload_dir=tmp_path / "photos",
            backup_dir=tmp_path / "backups",
            now=NOW,
        )


def test_an_unmigrated_database_is_refused_rather_than_reported_as_empty(
    tmp_path: Path,
) -> None:
    """`books: 0` from a file that has no tables would read as an empty library."""
    stray = tmp_path / "stray.sqlite3"
    sqlite3.connect(stray).close()

    with pytest.raises(BackupError, match="not a Colophon database"):
        create_backup(
            db_path=stray,
            upload_dir=tmp_path / "photos",
            backup_dir=tmp_path / "backups",
            now=NOW,
        )
    # The refused run leaves nothing behind for `list` to misreport.
    assert list(list_backups(tmp_path / "backups")) == []


# -------------------------------------------------------------------- retention


def test_retention_prunes_exactly_the_backup_older_than_the_window(tmp_path: Path) -> None:
    """Three backups dated 45, 20 and 1 days ago: only the 45-day one goes."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    oldest = make_dated_backup(backup_dir, days_ago=45)
    middle = make_dated_backup(backup_dir, days_ago=20)
    newest = make_dated_backup(backup_dir, days_ago=1)

    deleted = prune_backups(backup_dir, keep_days=30, now=NOW)

    assert deleted == [oldest]
    assert not oldest.exists()
    assert middle.is_dir()
    assert newest.is_dir()


def test_the_newest_backup_survives_however_old_it_is(tmp_path: Path) -> None:
    """Retention bounds disk use; it never empties the only copy of the library.

    If the nightly job died a year ago, deleting the last good backup is the one
    action that turns a stale backup into no backup.
    """
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    alone = make_dated_backup(backup_dir, days_ago=400)

    assert prune_backups(backup_dir, keep_days=30, now=NOW) == []
    assert alone.is_dir()


def test_prune_never_touches_anything_that_is_not_a_backup_directory(tmp_path: Path) -> None:
    """The nightly log lives in this same directory, along with anything the
    operator left there. Names that are not backup-shaped are invisible."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    stale = make_dated_backup(backup_dir, days_ago=90)
    log = backup_dir / "backup.log"
    log.write_text("nightly\n", encoding="utf-8")
    not_a_stamp = backup_dir / "colophon-tuesday"
    not_a_stamp.mkdir()
    a_file_named_like_one = backup_dir / "colophon-2025-01-01-000000"
    a_file_named_like_one.write_bytes(b"not a directory")

    deleted = prune_backups(backup_dir, keep_days=30, now=NOW)

    # The single real backup is the newest one, so it stays; the rest is untouched.
    assert deleted == []
    assert stale.is_dir()
    assert log.read_text(encoding="utf-8") == "nightly\n"
    assert not_a_stamp.is_dir()
    assert a_file_named_like_one.is_file()


def test_prune_of_an_absent_directory_is_nothing(tmp_path: Path) -> None:
    assert prune_backups(tmp_path / "never-created", keep_days=30, now=NOW) == []


def test_prune_refuses_a_negative_window(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="keep_days"):
        prune_backups(tmp_path, keep_days=-1, now=NOW)


# ------------------------------------------------------------------- what is there


def test_a_backup_without_a_manifest_is_reported_broken_not_fatal(tmp_path: Path) -> None:
    """A nightly run killed mid-write leaves exactly this directory behind."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    good = make_dated_backup(backup_dir, days_ago=1)
    half = make_dated_backup(backup_dir, days_ago=2)
    (half / MANIFEST_NAME).unlink()
    corrupt = make_dated_backup(backup_dir, days_ago=3)
    (corrupt / MANIFEST_NAME).write_text("{ not json", encoding="utf-8")
    db_removed = make_dated_backup(backup_dir, days_ago=4)
    (db_removed / BACKUP_DB_NAME).unlink()

    infos = list_backups(backup_dir)

    assert [info.name for info in infos] == [
        good.name,
        half.name,
        corrupt.name,
        db_removed.name,
    ]
    assert not infos[0].broken
    assert infos[0].book_count == 10
    for broken_info in infos[1:]:
        assert broken_info.broken
        assert broken_info.error
        # The name still says when it was taken, which is what an operator needs.
        assert broken_info.created_at.tzinfo is not None
    # Each flavour of broken says which one it is: no manifest, an unreadable
    # manifest, and a manifest whose database never made it to disk.
    assert MANIFEST_NAME in infos[1].error
    assert "unreadable" in infos[2].error
    assert BACKUP_DB_NAME in infos[3].error
    # Only a manifest that could not be parsed loses its counts: the last directory is
    # broken for a different reason, and its numbers are still worth reading.
    assert (infos[1].book_count, infos[2].book_count) == (None, None)
    assert infos[3].book_count == 10


def test_listing_an_absent_or_empty_directory_is_empty(tmp_path: Path) -> None:
    assert list_backups(tmp_path / "never-created") == []
    empty = tmp_path / "empty"
    empty.mkdir()
    assert list_backups(empty) == []
    # A directory of other people's files is not a list of backups either.
    (empty / "notes.txt").write_text("hi", encoding="utf-8")
    assert list_backups(empty) == []


# -------------------------------------------------------- never served over HTTP


def test_no_route_or_static_mount_serves_the_backup_directory() -> None:
    """T10: the backup directory must never be web-served.

    A backup is the whole library plus every photo, readable by anyone who can reach a
    URL, so this is not a nicety. The check goes through the served app's own routes:
    no path may name a backup location, and no static mount may sit on the backups or
    anywhere above them — mounting `data/` would serve `data/backups/…` for free.
    """
    backup_dir = Path(settings.backup_dir).resolve()

    for route in served_app.routes:
        path = str(getattr(route, "path", ""))
        assert "backup" not in path.lower(), f"{route!r} exposes a backup path"

        directory = getattr(getattr(route, "app", None), "directory", None)
        if directory is None:
            continue
        mounted = Path(str(directory)).resolve()
        assert mounted != backup_dir
        assert backup_dir not in mounted.parents, f"mount {mounted} serves {backup_dir}"
        assert mounted not in backup_dir.parents


def test_the_default_backup_dir_is_not_inside_the_served_static_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`app/static` is mounted at /static, so a backup inside it is a download link."""
    monkeypatch.delenv("COLOPHON_BACKUP_DIR", raising=False)
    default = Path(load_settings().backup_dir).resolve()

    assert default == (PROJECT_ROOT / "data" / "backups").resolve()
    assert STATIC_DIR.resolve() not in default.parents
    assert default not in STATIC_DIR.resolve().parents


@pytest.mark.parametrize(
    "url",
    [
        "/backups/manifest.json",
        "/backup/colophon.sqlite3",
        "/data/backups/colophon-2025-08-28-030000/colophon.sqlite3",
        "/static/../../data/backups/manifest.json",
        "/photos/../../backups/colophon.sqlite3",
    ],
)
def test_plausible_backup_urls_are_not_served(url: str) -> None:
    """What the route walk above means in practice: 404, not the file."""
    response = TestClient(create_app()).get(url)

    assert response.status_code == 404


# ------------------------------------------------------------------------ the CLI


def test_the_cli_runs_the_whole_job_and_refuses_what_it_should(
    library: Library, capsys: pytest.CaptureFixture[str]
) -> None:
    """The timer calls `python -m app.tools.backup`, so the CLI is tested end to end."""
    library.dispose()
    books_before = library.summary.books
    args = [
        "--db-path",
        str(library.db_path),
        "--upload-dir",
        str(library.upload_dir),
        "--backup-dir",
        str(library.backup_dir),
    ]

    assert backup_cli.main(["create", *args]) == 0
    created = capsys.readouterr().out
    assert "Backup written to" in created
    assert f"books            {books_before}" in created

    backups = list_backups(library.backup_dir)
    assert len(backups) == 1
    name = backups[0].name

    assert backup_cli.main(["list", "--backup-dir", str(library.backup_dir)]) == 0
    listing = capsys.readouterr().out
    assert name in listing
    assert f"{books_before} books" in listing

    # Refuses to overwrite the database it was not told to overwrite: exit 1, one line.
    assert backup_cli.main(["restore", name, *args]) == 1
    error = capsys.readouterr().err
    assert "already exists" in error

    # Destroy, then restore by name through the CLI — the recovery an operator does.
    library.db_path.unlink()
    library.photo_path.unlink()
    assert backup_cli.main(["restore", name, "--force", *args]) == 0
    assert library.photo_path.read_bytes() == library.photo_bytes

    engine = engine_for(library.db_path)
    try:
        assert counts(engine)[0] == books_before
    finally:
        engine.dispose()

    assert backup_cli.main(["prune", "--backup-dir", str(library.backup_dir)]) == 0
    assert "Removed 0 backup(s) older than 30 day(s)" in capsys.readouterr().out

    assert backup_cli.main(["restore", "colophon-1971-01-01-000000", "--force", *args]) == 1
    assert "No such backup" in capsys.readouterr().err
