# Add, edit and photos (M4). Server-rendered forms, no JavaScript required: the
# page posts values and the server answers with HTML, which keeps the whole path
# testable and keeps the app usable when a script fails to load.
#
# Two invariants worth stating because they are easy to break from a form. A book
# that is not physically here (wanted / sold / gifted / borrowed) has no room (D2
# — a room is where a copy stands). And every query is scoped to the signed-in
# owner: another user's book id answers 404, not 403, so ids do not leak existence.
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.deps import CurrentUser, current_user
from app.duplicates import find_duplicates
from app.formats import FORMAT_BY_KEY
from app.forms import read_form
from app.isbn import normalize_isbn
from app.lookup import fetch_provider_cover
from app.money import format_price
from app.prefs import load_prefs
from app.settings import settings
from app.templating import templates
from app.uploads import SLOTS, UploadError, delete_upload, read_upload, save_upload

router = APIRouter()

OWNERSHIP_STATUSES = ("owned", "wanted", "borrowed", "sold", "gifted")
READING_STATES = ("unread", "reading", "finished", "dnf")
# Statuses whose copies are not on a shelf, so the room field is dropped for them.
ABSENT_STATUSES = {"wanted", "sold", "gifted", "borrowed"}


@dataclass
class FormOutcome:
    """Parsed values plus the messages a person needs to see before saving."""

    values: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@router.get("/book/new", response_class=HTMLResponse)
def new_book_form(
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> HTMLResponse:
    return _render_form(request, session, user, outcome=None, book=None, lookup_query=q)


# Query parameters that may prefill the blank form. "Use this" from the lookup
# list is a plain GET carrying these, so adopting a provider hit needs no
# JavaScript and the person still reviews the values before saving.
_PREFILL_FIELDS = (
    "title",
    "subtitle",
    "authors",
    "isbn",
    "publisher",
    "publication_year",
    "page_count",
    "language",
    "cover_url",
    "series_name",
)


@router.post("/book/new", response_class=HTMLResponse)
async def create_book(
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    form: Annotated[dict, Depends(read_form)],
) -> HTMLResponse:
    outcome = _parse_form(form)
    if outcome.errors:
        return _render_form(
            request, session, user, outcome=outcome, book=None, lookup_query=None, status_code=400
        )

    # D3: duplicates warn, they never block. Save first, then say what it looked like.
    looks_duplicated = bool(
        find_duplicates(
            session, user.id, isbn=outcome.values.get("isbn"), title=outcome.values.get("title")
        )
    )
    book_id = _insert_book(session, user.id, outcome.values)
    session.commit()
    suffix = "&duplicate=1" if looks_duplicated else ""
    return RedirectResponse(url=f"/book/{book_id}/edit?saved=1{suffix}", status_code=303)


@router.get("/book/{book_id}/edit", response_class=HTMLResponse)
def edit_book_form(
    request: Request,
    book_id: int,
    user: Annotated[CurrentUser, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    saved: Annotated[str | None, Query(max_length=8)] = None,
) -> HTMLResponse:
    book = _load_book(session, user.id, book_id)
    if book is None:
        return _not_found(request)
    outcome = FormOutcome() if saved else None
    return _render_form(request, session, user, outcome=outcome, book=book, lookup_query=None)


@router.post("/book/{book_id}/edit", response_class=HTMLResponse)
async def update_book(
    request: Request,
    book_id: int,
    user: Annotated[CurrentUser, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    form: Annotated[dict, Depends(read_form)],
) -> HTMLResponse:
    book = _load_book(session, user.id, book_id)
    if book is None:
        return _not_found(request)

    outcome = _parse_form(form)
    if outcome.errors:
        return _render_form(
            request, session, user, outcome=outcome, book=book, lookup_query=None, status_code=400
        )

    looks_duplicated = bool(
        find_duplicates(
            session,
            user.id,
            isbn=outcome.values.get("isbn"),
            title=outcome.values.get("title"),
            exclude_book_id=book_id,
        )
    )
    _update_book(session, user.id, book_id, outcome.values, book)
    session.commit()
    suffix = "&duplicate=1" if looks_duplicated else ""
    return RedirectResponse(url=f"/book/{book_id}/edit?saved=1{suffix}", status_code=303)


@router.get("/book/{book_id}", response_class=HTMLResponse)
def book_page(
    request: Request,
    book_id: int,
    user: Annotated[CurrentUser, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    """The read-only record of one book. Declared after /book/new on purpose:
    FastAPI matches in registration order, and a literal path must win over
    this one before "new" is tried as an id.

    Nothing here edits: the page is a look, the actions link out. A book that
    is not this owner's answers with the same 404 as an id that never existed,
    so the status line alone never confirms that someone else has that book.
    """
    book = _load_book(session, user.id, book_id)
    if book is None:
        return _not_found(request)

    # Owner scoping happened in _load_book; these lookups hang off rows that
    # already belong to this owner's book, by primary key.
    genre_label = None
    if book["primary_genre_notation"]:
        genre_label = session.execute(
            text("SELECT label FROM genres WHERE notation = :n"),
            {"n": book["primary_genre_notation"]},
        ).scalar()
    room_name = None
    if book["room_id"]:
        room_name = session.execute(
            text("SELECT name FROM rooms WHERE id = :r"), {"r": book["room_id"]}
        ).scalar()
    # Only the open loan: SPEC §3 shelf 2 is "who has it, since when". A
    # returned loan is history and must not claim the book is still out.
    loan = session.execute(
        text(
            "SELECT borrowed_to, loaned_at, expected_return_date FROM loans"
            " WHERE book_id = :book_id AND returned_at IS NULL"
            " ORDER BY loaned_at LIMIT 1"
        ),
        {"book_id": book_id},
    ).first()
    # Only slots whose image is actually on disk are rendered (see _photo_bytes);
    # the rest are counted so the page can say so instead of showing a broken icon.
    recorded_slots = list(
        session.execute(
            text("SELECT slot FROM photos WHERE book_id = :book_id ORDER BY slot"),
            {"book_id": book_id},
        ).scalars()
    )
    photos = [slot for slot in recorded_slots if _photo_bytes(session, book_id, slot) is not None]

    prefs = load_prefs(session, user.id)
    fmt = FORMAT_BY_KEY.get(book["format"] or "")
    return templates.TemplateResponse(
        request,
        "book.html",
        {
            "user": user,
            "theme": prefs.theme,
            "page_title": book["title"],
            "book": book,
            "genre_label": genre_label,
            "room_name": room_name,
            # An unrecognised stored key is shown verbatim rather than blanked:
            # a detail page must not hide what the record says.
            "format_label": fmt.label if fmt else book["format"],
            "loan": dict(loan._mapping) if loan else None,
            "photos": photos,
            "missing_photo_files": len(recorded_slots) - len(photos),
            "series_position": _series_position_text(book["series_position"]),
            "price_text": format_price(book["price"], prefs.currency_symbol or "€"),
        },
    )


@router.post("/book/{book_id}/photos")
async def add_photo(
    book_id: int,
    user: Annotated[CurrentUser, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    slot: Annotated[str, Form(max_length=16)],
    file: Annotated[UploadFile, File()],
    crop_left: Annotated[int | None, Form()] = None,
    crop_top: Annotated[int | None, Form()] = None,
    crop_right: Annotated[int | None, Form()] = None,
    crop_bottom: Annotated[int | None, Form()] = None,
) -> RedirectResponse:
    """Upload a cover, back or spine photo and replace whatever was in that slot."""
    book = _load_book(session, user.id, book_id)
    if book is None:
        return RedirectResponse(url="/", status_code=303)

    data = await file.read()
    crop = None
    if None not in (crop_left, crop_top, crop_right, crop_bottom):
        crop = (crop_left, crop_top, crop_right, crop_bottom)

    try:
        saved = save_upload(
            data, upload_dir=settings.upload_dir, book_id=book_id, slot=slot, crop=crop
        )
    except UploadError as exc:
        session.rollback()
        reason = quote(str(exc)[:160])
        return RedirectResponse(
            url=f"/book/{book_id}/edit?photo_error=1&photo_reason={reason}", status_code=303
        )

    previous = _photo_filenames(session, book_id, slot).get("upload_filename")
    session.execute(
        text(
            """
            INSERT INTO photos (owner_id, book_id, slot, upload_filename)
            VALUES (:owner_id, :book_id, :slot, :filename)
            ON CONFLICT (book_id, slot) DO UPDATE SET upload_filename = excluded.upload_filename
            """
        ),
        {"owner_id": user.id, "book_id": book_id, "slot": slot, "filename": saved.filename},
    )
    session.commit()
    if previous:
        delete_upload(settings.upload_dir, previous)
    return RedirectResponse(url=f"/book/{book_id}/edit?photo_saved=1", status_code=303)


@router.post("/book/{book_id}/photos/{slot}/delete")
def remove_photo(
    book_id: int,
    slot: str,
    user: Annotated[CurrentUser, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> RedirectResponse:
    if slot not in SLOTS:
        return RedirectResponse(url=f"/book/{book_id}/edit", status_code=303)
    book = _load_book(session, user.id, book_id)
    if book is None:
        return RedirectResponse(url="/", status_code=303)

    filenames = _photo_filenames(session, book_id, slot)
    session.execute(
        text("DELETE FROM photos WHERE book_id = :book_id AND slot = :slot"),
        {"book_id": book_id, "slot": slot},
    )
    session.commit()
    for filename in filenames.values():
        if filename:
            delete_upload(settings.upload_dir, filename)
    return RedirectResponse(url=f"/book/{book_id}/edit", status_code=303)


@router.get("/book/{book_id}/photos/{slot}")
def get_photo(
    book_id: int,
    slot: str,
    user: Annotated[CurrentUser, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Serve a stored photo. An upload wins over a cached provider image (T6)."""
    if slot not in SLOTS or _load_book(session, user.id, book_id) is None:
        return Response(content=b"", status_code=404)

    stored = _photo_bytes(session, book_id, slot)
    if stored is None:
        return Response(content=b"", status_code=404)
    filename, data = stored
    return Response(content=data, media_type=_media_type(filename))


def _photo_bytes(session: Session, book_id: int, slot: str) -> tuple[str, bytes] | None:
    """The image bytes for one slot, or None.

    An upload wins over a cached provider image (T6). A `photos` row can exist with
    no file behind it — the M2 fixture records provider filenames, and a provider
    download can fail after the row was written — so "is there a row" is not the
    question any page should ask. Pages ask this one, which is why they never render
    a broken image.
    """
    filenames = _photo_filenames(session, book_id, slot)
    for key in ("upload_filename", "provider_filename"):
        filename = filenames.get(key)
        if not filename:
            continue
        data = read_upload(settings.upload_dir, filename)
        if data is not None:
            return filename, data
    return None


# --------------------------------------------------------------------------
# form plumbing
# --------------------------------------------------------------------------

_TEXT_FIELDS = (
    "subtitle",
    "publisher",
    "edition",
    "language",
    "genre_free_text",
    "where_bought",
    "gifted_to",
    "gift_note",
    "inscription",
    "notes",
    "series_name",
    "authors",
    "cover_url",
)
_POSITIVE_INTS = ("page_count", "height_mm", "width_mm", "thickness_mm")


def _parse_form(form: dict) -> FormOutcome:
    outcome = FormOutcome()
    values = outcome.values

    title = (form.get("title") or "").strip()
    if not title:
        outcome.errors.append("A book needs a title.")
    values["title"] = title[:300]

    for name in _TEXT_FIELDS:
        raw = (form.get(name) or "").strip()
        values[name] = raw or None

    isbn_raw = (form.get("isbn") or "").strip()
    if isbn_raw:
        normalised = normalize_isbn(isbn_raw)
        if normalised is None:
            outcome.errors.append(f"That ISBN is not valid: {isbn_raw}")
        else:
            values["isbn"] = normalised
    else:
        values["isbn"] = None

    for name in _POSITIVE_INTS:
        raw = (form.get(name) or "").strip()
        if not raw:
            values[name] = None
            continue
        try:
            parsed = int(raw)
        except ValueError:
            outcome.errors.append(
                f"{_pretty(name)} must be a whole number of millimetres or pages."
            )
            continue
        if parsed <= 0:
            outcome.errors.append(f"{_pretty(name)} must be greater than zero.")
            continue
        values[name] = parsed

    raw_year = (form.get("publication_year") or "").strip()
    values["publication_year"] = None
    if raw_year:
        try:
            year = int(raw_year)
        except ValueError:
            outcome.errors.append("Publication year must be a year, like 1984.")
        else:
            if not 0 < year < 10000:
                outcome.errors.append("Publication year must be between 1 and 9999.")
            else:
                values["publication_year"] = year

    raw_price = (form.get("price") or "").strip()
    values["price"] = None
    if raw_price:
        try:
            price = Decimal(raw_price)
        except InvalidOperation:
            outcome.errors.append("Price must be a number, like 14.90.")
        else:
            if price < 0:
                outcome.errors.append("Price cannot be negative.")
            else:
                values["price"] = price

    raw_position = (form.get("series_position") or "").strip()
    values["series_position"] = None
    if raw_position:
        try:
            position = float(raw_position)
        except ValueError:
            outcome.errors.append("Series number must be a number, like 1.5.")
        else:
            if position < 0:
                outcome.errors.append("Series number cannot be negative.")
            else:
                values["series_position"] = position
    if values["series_position"] is not None and not values.get("series_name"):
        outcome.errors.append("A series number needs a series name.")

    for name, allowed, label in (
        ("format", FORMAT_BY_KEY, "Format"),
        ("ownership_status", OWNERSHIP_STATUSES, "Ownership"),
        ("reading_state", READING_STATES, "Reading state"),
    ):
        raw = (form.get(name) or "").strip()
        if raw and raw not in allowed:
            outcome.errors.append(f"{label} is not one of: {', '.join(allowed)}.")
        else:
            values[name] = raw or None

    for name in ("is_signed", "is_sprayed_edge"):
        values[name] = bool(form.get(name))

    room_raw = (form.get("room_id") or "").strip()
    values["room_id"] = int(room_raw) if room_raw.isdigit() else None
    if values.get("ownership_status") in ABSENT_STATUSES:
        values["room_id"] = None

    values["primary_genre_notation"] = (form.get("primary_genre_notation") or "").strip() or None
    return outcome


def _last_insert_id(session: Session) -> int:
    """SQLite's rowid for the insert made on this connection.

    SQLAlchemy 2.0 refuses `inserted_primary_key` on a text() statement, and
    these inserts stay plain SQL on purpose (SPEC §15), so the id is asked for
    where SQLite keeps it.
    """
    return int(session.execute(text("SELECT last_insert_rowid()")).scalar_one())


def _pretty(name: str) -> str:
    return name.replace("_", " ").capitalize()


def _insert_book(session: Session, owner_id: int, values: dict) -> int:
    session.execute(text(_insert_sql()), _insert_params(owner_id, values))
    book_id = _last_insert_id(session)
    _replace_authors(session, owner_id, book_id, values.get("authors"))
    _apply_series(session, owner_id, book_id, values)
    _apply_provider_cover(session, owner_id, book_id, values.get("cover_url"))
    return book_id


# Form fields that are not columns on `books`: series and authors live in their
# own tables and cover_url is a download instruction, so the UPDATE must skip them.
_FORM_ONLY_KEYS = ("series_name", "authors", "cover_url")


def _update_book(
    session: Session, owner_id: int, book_id: int, values: dict, existing: dict
) -> None:
    # `ownership_status` and `reading_state` are NOT NULL: a form that did not
    # send them (a partial post, a stripped-down client) keeps what is stored
    # rather than writing NULL and failing the constraint deep in the database.
    values["ownership_status"] = (
        values.get("ownership_status") or existing.get("ownership_status") or "owned"
    )
    values["reading_state"] = (
        values.get("reading_state") or existing.get("reading_state") or "unread"
    )

    columns = [key for key in values if key not in _FORM_ONLY_KEYS]
    assignments = ", ".join(f"{column} = :{column}" for column in columns)
    session.execute(
        text(f"UPDATE books SET {assignments} WHERE id = :id"), {**values, "id": book_id}
    )
    _replace_authors(session, owner_id, book_id, values.get("authors"))
    _apply_series(session, owner_id, book_id, values)
    _apply_provider_cover(session, owner_id, book_id, values.get("cover_url"))


_INSERT_COLUMNS = (
    "owner_id",
    "title",
    "subtitle",
    "isbn",
    "publisher",
    "publication_year",
    "edition",
    "format",
    "page_count",
    "language",
    "room_id",
    "primary_genre_notation",
    "genre_free_text",
    "price",
    "where_bought",
    "ownership_status",
    "reading_state",
    "gifted_to",
    "gift_note",
    "is_sprayed_edge",
    "is_signed",
    "inscription",
    "notes",
    "height_mm",
    "width_mm",
    "thickness_mm",
)


def _insert_sql() -> str:
    columns = ", ".join(_INSERT_COLUMNS)
    placeholders = ", ".join(f":{column}" for column in _INSERT_COLUMNS)
    return f"INSERT INTO books ({columns}) VALUES ({placeholders})"


def _insert_params(owner_id: int, values: dict) -> dict:
    params = {column: values.get(column) for column in _INSERT_COLUMNS}
    params["owner_id"] = owner_id
    params["ownership_status"] = params.get("ownership_status") or "owned"
    params["reading_state"] = params.get("reading_state") or "unread"
    params["is_sprayed_edge"] = int(bool(params.get("is_sprayed_edge")))
    params["is_signed"] = int(bool(params.get("is_signed")))
    return params


def _replace_authors(session: Session, owner_id: int, book_id: int, raw: str | None) -> None:
    """Comma-separated names in, position-1-based links out (book_authors CHECK)."""
    session.execute(text("DELETE FROM book_authors WHERE book_id = :book_id"), {"book_id": book_id})
    from app.seed import sort_key

    position = 1
    for name in [part.strip() for part in (raw or "").split(",") if part.strip()]:
        author_id = session.execute(
            text("SELECT id FROM authors WHERE owner_id = :owner_id AND name = :name"),
            {"owner_id": owner_id, "name": name},
        ).scalar()
        if author_id is None:
            session.execute(
                text(
                    "INSERT INTO authors (owner_id, name, sort_key)"
                    " VALUES (:owner_id, :name, :sort_key)"
                ),
                {"owner_id": owner_id, "name": name[:200], "sort_key": sort_key(name)},
            )
            author_id = _last_insert_id(session)
        session.execute(
            text(
                "INSERT INTO book_authors (book_id, author_id, position, owner_id)"
                " VALUES (:b, :a, :p, :o)"
            ),
            {"b": book_id, "a": author_id, "p": position, "o": owner_id},
        )
        position += 1


def _apply_series(session: Session, owner_id: int, book_id: int, values: dict) -> None:
    """Series is created on demand from the form's free-text name (D9)."""
    name = (values.get("series_name") or "").strip()
    if not name:
        session.execute(
            text("UPDATE books SET series_id = NULL, series_position = NULL WHERE id = :id"),
            {"id": book_id},
        )
        return

    series_id = session.execute(
        text("SELECT id FROM series WHERE owner_id = :owner_id AND name = :name"),
        {"owner_id": owner_id, "name": name},
    ).scalar()
    if series_id is None:
        session.execute(
            text("INSERT INTO series (owner_id, name) VALUES (:owner_id, :name)"),
            {"owner_id": owner_id, "name": name[:200]},
        )
        series_id = _last_insert_id(session)
    session.execute(
        text("UPDATE books SET series_id = :series_id, series_position = :position WHERE id = :id"),
        {"series_id": series_id, "position": values.get("series_position"), "id": book_id},
    )


def _apply_provider_cover(
    session: Session, owner_id: int, book_id: int, cover_url: str | None
) -> None:
    """Cache a provider cover as the front photo. Failures are silent by design:
    a book saved without art is still a saved book."""
    if not cover_url:
        return
    data = fetch_provider_cover(cover_url)
    if data is None:
        return
    try:
        saved = save_upload(data, upload_dir=settings.upload_dir, book_id=book_id, slot="front")
    except UploadError:
        return
    session.execute(
        text(
            """
            INSERT INTO photos (owner_id, book_id, slot, provider_filename)
            VALUES (:owner_id, :book_id, 'front', :filename)
            ON CONFLICT (book_id, slot) DO UPDATE SET provider_filename = excluded.provider_filename
            """
        ),
        {"owner_id": owner_id, "book_id": book_id, "filename": saved.filename},
    )


def _load_book(session: Session, owner_id: int, book_id: int) -> dict | None:
    record = session.execute(
        text(
            """
            SELECT b.*, COALESCE(s.name, '') AS series_name
            FROM books b
            LEFT JOIN series s ON s.id = b.series_id
            WHERE b.id = :book_id AND b.owner_id = :owner_id
            """
        ),
        {"book_id": book_id, "owner_id": owner_id},
    ).first()
    if record is None:
        return None
    book = dict(record._mapping)
    book["authors"] = ", ".join(
        name
        for name in session.execute(
            text(
                "SELECT a.name FROM book_authors ba JOIN authors a ON a.id = ba.author_id"
                " WHERE ba.book_id = :book_id ORDER BY ba.position"
            ),
            {"book_id": book["id"]},
        ).scalars()
    )
    return book


def _photo_filenames(session: Session, book_id: int, slot: str) -> dict:
    record = session.execute(
        text(
            "SELECT upload_filename, provider_filename FROM photos WHERE book_id = :b AND slot = :s"
        ),
        {"b": book_id, "s": slot},
    ).first()
    return dict(record._mapping) if record else {}


def _series_position_text(position: float | None) -> str | None:
    """D9 allows decimals (1.5), but a whole position must read "2", not "2.0" —
    SQLite REAL hands back a float either way."""
    if position is None:
        return None
    number = float(position)
    return str(int(number)) if number.is_integer() else str(number)


def _render_form(
    request: Request,
    session: Session,
    user: CurrentUser,
    *,
    outcome: FormOutcome | None,
    book: dict | None,
    lookup_query: str | None,
    status_code: int = 200,
) -> HTMLResponse:
    from app.lookup import search

    values = dict(book) if book else dict(outcome.values if outcome else {})
    lookup = search(lookup_query) if lookup_query else None

    # Messages travel as query flags on the redirect, so a browser refresh does
    # not replay a POST and does not re-show a stale banner.
    notices = []
    flags = request.query_params
    if flags.get("saved"):
        notices.append("Saved.")
    if flags.get("duplicate"):
        notices.append(
            "Heads up: that ISBN or title is already on your shelf. It saved anyway — D3 warns."
        )
    if flags.get("photo_saved"):
        notices.append("Photo saved.")
    if flags.get("photo_error"):
        notices.append(
            "That photo could not be used: wrong type, too large, or a crop outside the image."
        )
    if lookup is not None and lookup.failed:
        notices.append(lookup.error or "Lookup failed.")
    genres = session.execute(
        text("SELECT notation, label FROM genres ORDER BY sort_order, label")
    ).all()
    rooms = session.execute(
        text("SELECT id, name FROM rooms WHERE owner_id = :o OR owner_id IS NULL ORDER BY name"),
        {"o": user.id},
    ).all()
    if book is None and not outcome:
        for name in _PREFILL_FIELDS:
            raw = request.query_params.get(name)
            if raw:
                values[name] = raw[:300]

    return templates.TemplateResponse(
        request,
        "book_form.html",
        {
            "user": user,
            "theme": "light",
            "page_title": ("Edit " + (values.get("title") or "book")) if book else "Add a book",
            "book": book,
            "values": values,
            "errors": outcome.errors if outcome else [],
            "warnings": notices,
            "genres": genres,
            "rooms": rooms,
            "formats": [(key, spec.label) for key, spec in FORMAT_BY_KEY.items()],
            "ownership_statuses": OWNERSHIP_STATUSES,
            "reading_states": READING_STATES,
            "slots": SLOTS,
            "lookup": lookup,
        },
        status_code=status_code,
    )


def _not_found(request: Request) -> HTMLResponse:
    return HTMLResponse(
        '<!doctype html><html lang="en"><body><p>No such book on your shelf.</p>'
        '<p><a href="/">Back to the shelf</a></p></body></html>',
        status_code=404,
    )


def _media_type(filename: str) -> str:
    suffix = filename.rsplit(".", 1)[-1].lower()
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
    }.get(suffix, "application/octet-stream")
