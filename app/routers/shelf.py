# The four shelves, search and the dashboard (M6).
#
# One route per shelf sharing a single context builder, because the difference
# between them is a scope and a template — not a second implementation of zoom
# resolution, filtering and preference loading.
#
# SPEC §3: shelf 1 is what you have, shelf 2 what is on loan, shelf 3 what is gone
# (sold and gifted together, with a filter that distinguishes them), shelf 4 the
# wishlist. SPEC §9: all filtering is server-side, and the author filter shows that
# author's books across every genre section — which is exactly what a shelf grouped
# by genre needs. Rooms are filters, not pages (D7).
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db import get_session
from app.deps import CurrentUser, current_user
from app.filters import READING_STATES, Filters, parse_filters
from app.money import format_price
from app.prefs import load_prefs, persist_zoom
from app.shelf import build_shelf, normalise_zoom
from app.shelf_store import (
    author_options,
    borrowed_rows,
    genre_options,
    gone_rows,
    room_options,
    shelf_rows,
)
from app.stats import dashboard
from app.templating import templates

router = APIRouter()

ZOOM_VALUES = {"s", "m", "l", "S", "M", "L"}


@router.get("/", response_class=HTMLResponse)
def shelf_page(
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    zoom: Annotated[str | None, Query(max_length=8)] = None,
) -> HTMLResponse:
    prefs, chosen = _resolve_zoom(session, user.id, zoom)
    filters = parse_filters(request.query_params, scope="library")
    sections = build_shelf(shelf_rows(session, user.id, filters), chosen)

    return templates.TemplateResponse(
        request,
        "shelf.html",
        _context(
            request,
            session,
            user,
            prefs,
            filters=filters,
            zoom=chosen,
            sections=sections,
            active_shelf="library",
            page_title="Colophon — My library",
            shelf_path="/",
        ),
    )


@router.get("/wishlist", response_class=HTMLResponse)
def wishlist_page(
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    zoom: Annotated[str | None, Query(max_length=8)] = None,
) -> HTMLResponse:
    prefs, chosen = _resolve_zoom(session, user.id, zoom)
    filters = parse_filters(request.query_params, scope="wishlist")
    sections = build_shelf(shelf_rows(session, user.id, filters), chosen)

    return templates.TemplateResponse(
        request,
        "shelf.html",
        _context(
            request,
            session,
            user,
            prefs,
            filters=filters,
            zoom=chosen,
            sections=sections,
            active_shelf="wishlist",
            page_title="Colophon — Wishlist",
            shelf_path="/wishlist",
        ),
    )


@router.get("/borrowed", response_class=HTMLResponse)
def borrowed_page(
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    prefs = load_prefs(session, user.id)
    filters = parse_filters(request.query_params, scope="borrowed")

    return templates.TemplateResponse(
        request,
        "borrowed.html",
        _context(
            request,
            session,
            user,
            prefs,
            filters=filters,
            zoom=None,
            sections=None,
            rows=borrowed_rows(session, user.id, filters),
            active_shelf="borrowed",
            page_title="Colophon — Borrowed",
            shelf_path="/borrowed",
        ),
    )


@router.get("/gone", response_class=HTMLResponse)
def gone_page(
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    prefs = load_prefs(session, user.id)
    filters = parse_filters(request.query_params, scope="gone")

    return templates.TemplateResponse(
        request,
        "gone.html",
        _context(
            request,
            session,
            user,
            prefs,
            filters=filters,
            zoom=None,
            sections=None,
            rows=_prices(gone_rows(session, user.id, filters), prefs.currency_symbol or "€"),
            active_shelf="gone",
            page_title="Colophon — Gone",
            shelf_path="/gone",
        ),
    )


@router.get("/search", response_class=HTMLResponse)
def search_page(
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    all_shelves: Annotated[str | None, Query(alias="all", max_length=4)] = None,
    zoom: Annotated[str | None, Query(max_length=8)] = None,
) -> HTMLResponse:
    """Search the library and the wishlist.

    SPEC §3: shelves 2 and 3 (borrowed, gone) join in only through an explicit
    toggle, off by default — a hit pointing at a book you no longer have is noise
    unless you asked for it.
    """
    prefs, chosen = _resolve_zoom(session, user.id, zoom)
    include_all = (all_shelves or "").strip() in ("1", "true", "on")
    scope = "search_all" if include_all else "search"
    filters = parse_filters(request.query_params, scope=scope)

    # No query and no filters means an empty result set rather than "everything",
    # so the page never pretends a blank box is a search.
    rows = shelf_rows(session, user.id, filters) if (filters.q or filters.is_active()) else []
    sections = build_shelf(rows, chosen)

    return templates.TemplateResponse(
        request,
        "search.html",
        _context(
            request,
            session,
            user,
            prefs,
            filters=filters,
            zoom=chosen,
            sections=sections,
            active_shelf="search",
            page_title="Colophon — Search",
            shelf_path="/search",
            include_all=include_all,
            searched=bool(filters.q),
        ),
    )


@router.get("/stats", response_class=HTMLResponse)
def stats_page(
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    prefs = load_prefs(session, user.id)
    return templates.TemplateResponse(
        request,
        "stats.html",
        {
            "user": user,
            "theme": prefs.theme,
            "page_title": "Colophon — Statistics",
            "stats": dashboard(session, user.id),
            "active_shelf": "stats",
            "reading_states": READING_STATES,
        },
    )


# ---------------------------------------------------------------- shared context


def _resolve_zoom(session: Session, user_id: int, zoom: str | None) -> tuple[object, str]:
    prefs = load_prefs(session, user_id)
    # No query parameter means "whatever this user last chose" (A2), not the
    # built-in default; an unrecognised value is ignored outright so a typo in a
    # shared link cannot overwrite the stored choice.
    recognised = zoom.strip().upper() if zoom and zoom.strip() in ZOOM_VALUES else None
    chosen = normalise_zoom(recognised if recognised is not None else prefs.zoom_preset)
    if recognised is not None and chosen != prefs.zoom_preset:
        # Persisting on a GET is a deliberate shortcut: cosmetic, scoped to this
        # reader's own view; the CSRF-protected form is /settings.
        persist_zoom(session, user_id, chosen)
    return prefs, chosen


def _prices(rows: list[dict], symbol: str) -> list[dict]:
    """Prices are stored as plain numbers (SPEC §10); the display form is added here
    so the template never invents formatting of its own."""
    for row in rows:
        row["price_text"] = format_price(row.get("price"), symbol)
    return rows


def _context(
    request: Request,
    session: Session,
    user: CurrentUser,
    prefs,
    *,
    filters: Filters,
    zoom: str | None,
    sections,
    active_shelf: str,
    page_title: str,
    shelf_path: str,
    rows: list[dict] | None = None,
    include_all: bool = False,
    searched: bool = False,
) -> dict:
    return {
        "user": user,
        "theme": prefs.theme,
        "page_title": page_title,
        "zoom": zoom,
        "sections": sections or [],
        "rows": rows or [],
        "book_count": (
            sum(len(section.spines) for section in sections)
            if sections is not None
            else len(rows or [])
        ),
        "filters": filters,
        "filter_query": filters.as_query(),
        "has_filters": filters.is_active(),
        "active_shelf": active_shelf,
        "shelf_path": shelf_path,
        "reading_states": READING_STATES,
        "rooms": room_options(session, user.id),
        "authors": author_options(session, user.id),
        "genres": genre_options(session, user.id),
        "include_all": include_all,
        "searched": searched,
        "currency": prefs.currency_symbol or "€",
    }
