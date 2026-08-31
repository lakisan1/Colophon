# Add, edit and photos (M4). Server-rendered forms, no JavaScript required: the
# page posts values and the server answers with HTML, which keeps the whole path
# testable and keeps the app usable when a script fails to load.
#
# Two invariants worth stating because they are easy to break from a form. A book
# that is not physically here (wanted / sold / gifted / borrowed) has no room (D2
# — a room is where a copy stands). And every query is scoped to the signed-in
# owner: another user's book id answers 404, not 403, so ids do not leak existence.
from __future__ import annotations

import random
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
from app.page import page_context, page_prefs
from app.settings import settings
from app.shelf import CLOTH_HUES, cloth_hue
from app.templating import templates
from app.uploads import SLOTS, UploadError, delete_upload, read_upload, save_upload

router = APIRouter()

OWNERSHIP_STATUSES = ("owned", "wanted", "borrowed", "sold", "gifted")
READING_STATES = ("unread", "reading", "finished", "dnf")
# The five-point condition scale, mirroring the dealer's words. The words are
# the point: a bare stored 3 tells nobody anything, so the UI renders the word
# next to the number, and the form and the page must never drift apart.
CONDITION_RATINGS = (1, 2, 3, 4, 5)
CONDITION_LABELS = {1: "Poor", 2: "Fair", 3: "Good", 4: "Very good", 5: "As new"}
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


@router.get("/book/lookup")
async def book_lookup(isbn: str = ""):
    """Look up an ISBN on Open Library (free API) and return its metadata as JSON.

    Used by the add/edit form: typing an ISBN prefills title, authors, publisher,
    publication year, page count and language from Open Library.
    """
    from app.lookup import isbn_lookup

    candidate = isbn_lookup(isbn) if isbn else None
    if candidate is None:
        return {"found": False}
    return {
        "found": True,
        "title": candidate.title,
        "authors": list(candidate.authors),
        "publisher": candidate.publisher or "",
        "publication_year": candidate.published_year or "",
        "page_count": candidate.page_count or "",
        "language": candidate.language or "",
    }


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

    question = _oversize_question(form, outcome.values)
    if question:
        # Nothing has been written yet. The form comes back with the same
        # values, the question, and the hidden confirmation that turns the
        # next press of this very Save button into the save.
        outcome.warnings.append(question)
        return _render_form(
            request,
            session,
            user,
            outcome=outcome,
            book=None,
            lookup_query=None,
            confirm_oversize=True,
        )

    # D3: duplicates warn, they never block. Save first, then say what it looked like.
    looks_duplicated = bool(
        find_duplicates(
            session, user.id, isbn=outcome.values.get("isbn"), title=outcome.values.get("title")
        )
    )
    _insert_book(session, user.id, outcome.values)
    session.commit()
    suffix = "&duplicate=1" if looks_duplicated else ""
    return RedirectResponse(url=f"/?saved=1{suffix}", status_code=303)


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

    question = _oversize_question(form, outcome.values)
    if question:
        # The stored row is left exactly as it was until the same submit
        # returns with confirm_oversize=1.
        outcome.warnings.append(question)
        return _render_form(
            request,
            session,
            user,
            outcome=outcome,
            book=book,
            lookup_query=None,
            confirm_oversize=True,
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

    prefs = page_prefs(request)
    fmt = FORMAT_BY_KEY.get(book["format"] or "")
    return templates.TemplateResponse(
        request,
        "book.html",
        page_context(
            request,
            user,
            page_title=book["title"],
            book=book,
            genre_label=genre_label,
            room_name=room_name,
            # An unrecognised stored key is shown verbatim rather than blanked:
            # a detail page must not hide what the record says.
            format_label=fmt.label if fmt else book["format"],
            condition_label=CONDITION_LABELS.get(book["condition_rating"]),
            loan=dict(loan._mapping) if loan else None,
            photos=photos,
            missing_photo_files=len(recorded_slots) - len(photos),
            series_position=_series_position_text(book["series_position"]),
            price_text=format_price(book["price"], prefs.currency_symbol or "€"),
        ),
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


async def _delete_book(
    request: Request,
    book_id: int,
    user: Annotated[CurrentUser, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    form: Annotated[dict, Depends(read_form)],
) -> HTMLResponse:
    """Delete one book from the database.

    Deleting is destructive, so the signed-in account's password must be
    confirmed first; a wrong password re-renders the edit form with an error
    and nothing is deleted.
    """
    book = _load_book(session, user.id, book_id)
    if book is None:
        return _not_found(request)

    from app.auth import find_user, verify_password

    submitted = (form.get("password") or "").strip()
    account = find_user(session, user.username)
    errors = []
    if form.get("confirm_delete") != "1":
        errors.append("Tick the box to confirm you really want to delete this book.")
    if account is None or not verify_password(submitted, account["password_hash"]):
        errors.append(
            "Wrong password — the book was not deleted. Try again with your account password."
        )
    if errors:
        outcome = FormOutcome()
        outcome.errors.extend(errors)
        return _render_form(
            request,
            session,
            user,
            outcome=outcome,
            book=book,
            lookup_query=None,
            status_code=400,
        )

    # Remove stored photo files before their rows (FK cascade) go away.
    filenames = session.execute(
        text("SELECT upload_filename FROM photos WHERE book_id = :book_id"),
        {"book_id": book_id},
    ).scalars()
    for filename in filenames:
        if filename:
            delete_upload(settings.upload_dir, filename)

    session.execute(text("DELETE FROM books WHERE id = :book_id"), {"book_id": book_id})
    session.commit()
    return RedirectResponse(url="/", status_code=303)


@router.post("/book/{book_id}/delete")
async def delete_book(
    request: Request,
    book_id: int,
    user: Annotated[CurrentUser, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    form: Annotated[dict, Depends(read_form)],
) -> HTMLResponse:
    """Delete one book (singular path)."""
    return await _delete_book(request, book_id, user, session, form)


@router.post("/books/{book_id}/delete")
async def delete_book_plural(
    request: Request,
    book_id: int,
    user: Annotated[CurrentUser, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    form: Annotated[dict, Depends(read_form)],
) -> HTMLResponse:
    """Delete one book (plural path alias, so a path mismatch never 404s)."""
    return await _delete_book(request, book_id, user, session, form)


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
    "illustrator",
    "translator",
    "editor",
    "original_title",
    "original_language",
    "series_name",
    "authors",
    "tags",
    "cover_url",
)
_POSITIVE_INTS = ("page_count", "height_mm", "width_mm", "thickness_mm")

# The first real book entered into this app was typed in at 700 mm wide: a
# 70 mm paperback measured in centimetres but written in millimetres. SPEC §6
# draws every spine at its true relative size, so a wrong dimension silently
# distorts the whole shelf — which is why anything over this limit gets asked
# about instead of being written. It is a confirmation step, never a refusal:
# some books really are this big (boxed sets, oversized folios).
MAX_PLAUSIBLE_MM = 500
_DIMENSION_FIELDS = ("width_mm", "height_mm", "thickness_mm")


def _oversize_question(form: dict, values: dict) -> str | None:
    """One sentence asking about implausibly large dimensions, or None.

    A warning, not an error: the values are valid and the second submit — the
    one carrying confirm_oversize=1, which the re-rendered form holds in a
    hidden field — writes them. Negative and non-numeric dimensions never get
    here; _parse_form has already rejected those as errors, and waiving a
    warning waives nothing for them.
    """
    if (form.get("confirm_oversize") or "") == "1":
        return None
    parts = [
        f"{_pretty(name[: -len('_mm')])} {values[name]} mm"
        for name in _DIMENSION_FIELDS
        if isinstance(values.get(name), int) and values[name] > MAX_PLAUSIBLE_MM
    ]
    if not parts:
        return None
    verb = "is" if len(parts) == 1 else "are"
    return (
        " and ".join(parts)
        + f" {verb} larger than a book usually is — press Save again to save it anyway."
    )


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

    # Print year is the year this physical copy came off the press, which can
    # differ from the edition's publication year (a 1972 printing of a 1960
    # book). Empty means unknown (NULL); the range mirrors publication year.
    raw_print = (form.get("print_year") or "").strip()
    if raw_print:
        try:
            print_year = int(raw_print)
        except ValueError:
            outcome.errors.append("Print year must be a number.")
        else:
            if not 1 <= print_year <= 9999:
                outcome.errors.append("Print year must be between 1 and 9999.")
            else:
                values["print_year"] = print_year

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

    # Condition is a scale, not a choice, so it is parsed like the bounded
    # numbers above rather than an allowed-value list. Empty means unrated
    # (NULL); a rating outside 1..5 or a non-number is an error naming the
    # scale, never a silent clamp — a wrong rating is worse than no rating.
    raw_condition = (form.get("condition_rating") or "").strip()
    values["condition_rating"] = None
    if raw_condition:
        try:
            rating = int(raw_condition)
        except ValueError:
            outcome.errors.append("Condition is a rating from 1 to 5 — pick one.")
        else:
            if rating not in CONDITION_RATINGS:
                outcome.errors.append("Condition is a rating from 1 (Poor) to 5 (As new).")
            else:
                values["condition_rating"] = rating

    raw_hue = (form.get("cloth_hue") or "").strip()
    values["cloth_hue"] = None
    if raw_hue:
        try:
            hue = int(raw_hue)
        except ValueError:
            outcome.errors.append("Spine colour must be one of the offered swatches.")
        else:
            if hue not in CLOTH_HUES:
                outcome.errors.append("Spine colour must be one of the offered swatches.")
            else:
                values["cloth_hue"] = hue

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


def _random_dimensions() -> dict:
    """Plausible millimetres for a book the person did not measure. Height is
    the tall axis the shelf draws, width the page width, thickness the spine."""
    return {
        "height_mm": random.randint(190, 310),
        "width_mm": random.randint(130, 260),
        "thickness_mm": random.randint(8, 60),
    }


def _inherit_series_look(session: Session, owner_id: int, values: dict) -> None:
    """Fill a series book's blank appearance from the first book in that series.

    A series reads as one matching set, so the second and later books with no
    measured dimensions and no chosen colour take the size and colour of the
    first one. A series' very first book has no template and falls through to
    the random defaults below.
    """
    name = (values.get("series_name") or "").strip()
    if not name:
        return
    template = session.execute(
        text(
            """
            SELECT b.height_mm, b.width_mm, b.thickness_mm, b.cloth_hue,
                   COALESCE(a.sort_key, 'unknown') AS author_sort_key
            FROM books b
            JOIN series s ON s.id = b.series_id
            LEFT JOIN book_authors ba ON ba.book_id = b.id AND ba.position = 1
            LEFT JOIN authors a ON a.id = ba.author_id
            WHERE s.owner_id = :owner_id AND s.name = :name
              AND (b.height_mm IS NOT NULL OR b.thickness_mm IS NOT NULL
                   OR b.cloth_hue IS NOT NULL)
            ORDER BY b.id ASC
            LIMIT 1
            """
        ),
        {"owner_id": owner_id, "name": name},
    ).first()
    if template is None:
        return
    for column in ("height_mm", "width_mm", "thickness_mm"):
        if values.get(column) is None:
            values[column] = getattr(template, column, None)
    if values.get("cloth_hue") is None:
        # A series book with an explicit hue keeps it; otherwise match the colour
        # the first book actually renders (its author-derived hue).
        values["cloth_hue"] = (
            template.cloth_hue
            if template.cloth_hue is not None
            else cloth_hue(template.author_sort_key)
        )


def _insert_book(session: Session, owner_id: int, values: dict) -> int:
    # A book added without measured dimensions gets a random plausible size so
    # the shelf does not draw a collapsed default (SPEC §6 draws true sizes);
    # the same goes for colour when none was chosen.
    # A series book with a blank appearance inherits the look of an earlier book
    # in the same series; only a series' first book (or a book outside a series)
    # falls back to the random defaults.
    if values.get("series_name"):
        _inherit_series_look(session, owner_id, values)
    if any(values.get(name) is None for name in ("height_mm", "width_mm", "thickness_mm")):
        for name, value in _random_dimensions().items():
            if values.get(name) is None:
                values[name] = value
        if values.get("cloth_hue") is None:
            values["cloth_hue"] = random.choice(CLOTH_HUES)
    session.execute(text(_insert_sql()), _insert_params(owner_id, values))
    book_id = _last_insert_id(session)
    _replace_authors(session, owner_id, book_id, values.get("authors"))
    _replace_tags(session, owner_id, book_id, values.get("tags"))
    _apply_series(session, owner_id, book_id, values)
    _apply_provider_cover(session, owner_id, book_id, values.get("cover_url"))
    return book_id


# Form fields that are not columns on `books`: series and authors live in their
# own tables and cover_url is a download instruction, so the UPDATE must skip them.
_FORM_ONLY_KEYS = ("series_name", "authors", "tags", "cover_url")


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
    _replace_tags(session, owner_id, book_id, values.get("tags"))
    _apply_series(session, owner_id, book_id, values)
    _apply_provider_cover(session, owner_id, book_id, values.get("cover_url"))


_INSERT_COLUMNS = (
    "owner_id",
    "title",
    "subtitle",
    "isbn",
    "publisher",
    "publication_year",
    "print_year",
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
    "illustrator",
    "translator",
    "editor",
    "original_title",
    "original_language",
    "height_mm",
    "width_mm",
    "thickness_mm",
    "cloth_hue",
    "condition_rating",
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
    # SQLite cannot bind Decimal (e.g. price) or other non-primitive Python
    # types, so coerce only Decimal values to float before the INSERT.
    return {
        key: (None if value is None else float(value) if isinstance(value, Decimal) else value)
        for key, value in params.items()
    }


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


def _replace_tags(session: Session, owner_id: int, book_id: int, raw: str | None) -> None:
    """Comma-separated tag names in, deduped book_tags rows out (PK strips ties)."""
    session.execute(text("DELETE FROM book_tags WHERE book_id = :book_id"), {"book_id": book_id})
    for name in [part.strip() for part in (raw or "").split(",") if part.strip()]:
        tag_id = session.execute(
            text("SELECT id FROM tags WHERE owner_id = :owner_id AND name = :name"),
            {"owner_id": owner_id, "name": name},
        ).scalar()
        if tag_id is None:
            session.execute(
                text("INSERT INTO tags (owner_id, name) VALUES (:owner_id, :name)"),
                {"owner_id": owner_id, "name": name[:200]},
            )
            tag_id = _last_insert_id(session)
        session.execute(
            text("INSERT OR IGNORE INTO book_tags (book_id, tag_id, owner_id) VALUES (:b, :t, :o)"),
            {"b": book_id, "t": tag_id, "o": owner_id},
        )


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
    book["tags"] = ", ".join(
        session.execute(
            text(
                "SELECT t.name FROM book_tags bt JOIN tags t ON t.id = bt.tag_id"
                " WHERE bt.book_id = :book_id ORDER BY t.name"
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
    confirm_oversize: bool = False,
) -> HTMLResponse:
    from app.lookup import search

    values = dict(book) if book else {}
    if outcome:
        # On a re-render what was just typed wins over what is stored: the
        # warning asks about "Height 900 mm", so the field must show 900 —
        # otherwise the page confirms a value it does not display.
        values.update(outcome.values)
    lookup = (
        search(lookup_query, include_google=page_prefs(request).google_books)
        if lookup_query
        else None
    )

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
    # outcome.warnings holds the oversize question; it renders in the same
    # notices list because it reads the same way — information, not rejection.
    warnings = [*notices, *(outcome.warnings if outcome else [])]
    genres = session.execute(
        text("SELECT notation, label FROM genres ORDER BY sort_order, label")
    ).all()
    rooms = session.execute(
        text("SELECT id, name FROM rooms WHERE owner_id = :o OR owner_id IS NULL ORDER BY name"),
        {"o": user.id},
    ).all()
    # The Series field offers the reader's existing series as a dropdown but still
    # accepts a new name: _apply_series reuses a matching series or creates one.
    series_names = list(
        session.execute(
            text("SELECT DISTINCT name FROM series WHERE owner_id = :o ORDER BY name"),
            {"o": user.id},
        ).scalars()
    )
    # Authors and Illustrator get the same drop-down-or-type-new behaviour as
    # Series: offer the reader's existing values as suggestions but still accept
    # a new name (a new author/illustrator is created on save).
    author_names = list(
        session.execute(
            text("SELECT name FROM authors WHERE owner_id = :o ORDER BY sort_key"),
            {"o": user.id},
        ).scalars()
    )
    illustrator_names = list(
        session.execute(
            text(
                "SELECT DISTINCT illustrator FROM books"
                " WHERE owner_id = :o AND illustrator IS NOT NULL AND illustrator != ''"
                " ORDER BY illustrator"
            ),
            {"o": user.id},
        ).scalars()
    )
    # Translator and Editor use the same pick-or-type-new behaviour.
    translator_names = list(
        session.execute(
            text(
                "SELECT DISTINCT translator FROM books"
                " WHERE owner_id = :o AND translator IS NOT NULL AND translator != ''"
                " ORDER BY translator"
            ),
            {"o": user.id},
        ).scalars()
    )
    editor_names = list(
        session.execute(
            text(
                "SELECT DISTINCT editor FROM books"
                " WHERE owner_id = :o AND editor IS NOT NULL AND editor != ''"
                " ORDER BY editor"
            ),
            {"o": user.id},
        ).scalars()
    )
    tag_names = list(
        session.execute(
            text("SELECT name FROM tags WHERE owner_id = :o ORDER BY name"),
            {"o": user.id},
        ).scalars()
    )
    # The free-text genre gets the same pick-or-type-new behaviour as the rest:
    # offer what the reader has already written but accept a fresh value.
    genre_free_names = list(
        session.execute(
            text(
                "SELECT DISTINCT genre_free_text FROM books"
                " WHERE owner_id = :o AND genre_free_text IS NOT NULL AND genre_free_text != ''"
                " ORDER BY genre_free_text"
            ),
            {"o": user.id},
        ).scalars()
    )
    if book is None and not outcome:
        for name in _PREFILL_FIELDS:
            raw = request.query_params.get(name)
            if raw:
                values[name] = raw[:300]

    # Which slots have a row, and which have bytes behind it. They differ: a
    # provider download can fail after the row was written, and then an <img> for
    # that slot is a broken icon on the page where someone is trying to fix photos.
    photos: dict[str, dict[str, bool]] = {}
    if book:
        recorded = set(
            session.execute(
                text("SELECT slot FROM photos WHERE book_id = :book_id"), {"book_id": book["id"]}
            ).scalars()
        )
        photos = {
            slot: {
                "recorded": slot in recorded,
                "bytes": _photo_bytes(session, book["id"], slot) is not None,
            }
            for slot in SLOTS
        }

    return templates.TemplateResponse(
        request,
        "book_form.html",
        # The theme used to be hard-coded "light" here, which is how a user with
        # dark in their settings row got a light edit page. It now travels with
        # the rest of the shared context from app/page.py, so this router cannot
        # drop it (or pick up a second, divergent definition of it).
        page_context(
            request,
            user,
            page_title=("Edit " + (values.get("title") or "book")) if book else "Add a book",
            photos=photos,
            book=book,
            values=values,
            errors=outcome.errors if outcome else [],
            warnings=warnings,
            # The template turns this into the hidden field that makes the
            # second submit the confirmation; the number is handed to the
            # optional JS so the rule itself stays only in this module.
            confirm_oversize=confirm_oversize,
            max_plausible_mm=MAX_PLAUSIBLE_MM,
            genres=genres,
            rooms=rooms,
            series_names=series_names,
            author_names=author_names,
            illustrator_names=illustrator_names,
            translator_names=translator_names,
            editor_names=editor_names,
            tag_names=tag_names,
            genre_free_names=genre_free_names,
            formats=[(key, spec.label) for key, spec in FORMAT_BY_KEY.items()],
            ownership_statuses=OWNERSHIP_STATUSES,
            reading_states=READING_STATES,
            condition_ratings=[(n, CONDITION_LABELS[n]) for n in CONDITION_RATINGS],
            slots=SLOTS,
            lookup=lookup,
        ),
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
