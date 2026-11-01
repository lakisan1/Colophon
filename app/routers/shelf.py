# The four shelves, search and the dashboard.
#
# One route per shelf sharing a single context builder, because the difference
# between them is a scope and a template — not a second implementation of zoom
# resolution, filtering and preference loading. The keys every page shell needs
# (theme, user, nav) are no longer this router's job at all: app/page.py owns
# them, and `_context` below only adds what the shelf family itself renders.
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
from app.page import load_prefs_into_state, page_context, page_prefs
from app.prefs import persist_zoom
from app.shelf import build_shelf, normalise_zoom
from app.shelf_store import (
    author_options,
    borrowed_rows,
    genre_options,
    gone_rows,
    room_options,
    shelf_rows,
    tag_options,
    year_options,
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
    chosen = _resolve_zoom(request, session, user.id, zoom)
    filters = parse_filters(request.query_params, scope="library")

    # After adding a book the form redirects here with a flag; turn that into a
    # clear "Saved." confirmation instead of a bare redirect back to the shelf.
    notice = None
    if request.query_params.get("saved") == "1":
        notice = "Saved."
        if request.query_params.get("duplicate") == "1":
            notice += " It looked like a duplicate of a book already on the shelf."

    # The library search can also cover the gone (sold+gifted) or borrowed
    # shelf via the `status` filter; each shelf has its own row shape and
    # template, so switch to those when selected.
    # A global search (the "Also search gone & borrowed" checkbox) shows every
    # book as a spine, whatever its shelf, instead of switching to a table.
    if filters.search_all:
        sections = build_shelf(shelf_rows(session, user.id, filters), chosen)
        rows = None
        template = "shelf.html"
    elif filters.status == "gone":
        rows = gone_rows(session, user.id, filters)
        template = "gone.html"
        sections = None
    elif filters.status == "borrowed":
        rows = borrowed_rows(session, user.id, filters)
        template = "borrowed.html"
        sections = None
    else:
        sections = build_shelf(shelf_rows(session, user.id, filters), chosen)
        rows = None
        template = "shelf.html"

    return templates.TemplateResponse(
        request,
        template,
        _context(
            request,
            session,
            user,
            filters=filters,
            zoom=chosen,
            sections=sections,
            rows=rows,
            active_nav="library",
            page_title="Colophon — My library",
            shelf_path="/",
            notice=notice,
        ),
    )


@router.get("/wishlist", response_class=HTMLResponse)
def wishlist_page(
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    zoom: Annotated[str | None, Query(max_length=8)] = None,
) -> HTMLResponse:
    chosen = _resolve_zoom(request, session, user.id, zoom)
    filters = parse_filters(request.query_params, scope="wishlist")
    sections = build_shelf(shelf_rows(session, user.id, filters), chosen)

    return templates.TemplateResponse(
        request,
        "shelf.html",
        _context(
            request,
            session,
            user,
            filters=filters,
            zoom=chosen,
            sections=sections,
            active_nav="wishlist",
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
    filters = parse_filters(request.query_params, scope="borrowed")

    return templates.TemplateResponse(
        request,
        "borrowed.html",
        _context(
            request,
            session,
            user,
            filters=filters,
            rows=borrowed_rows(session, user.id, filters),
            active_nav="borrowed",
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
    filters = parse_filters(request.query_params, scope="gone")

    return templates.TemplateResponse(
        request,
        "gone.html",
        _context(
            request,
            session,
            user,
            filters=filters,
            rows=_prices(gone_rows(session, user.id, filters), page_prefs(request).currency_symbol),
            active_nav="gone",
            page_title="Colophon — Gone",
            shelf_path="/gone",
        ),
    )


@router.get("/stats", response_class=HTMLResponse)
def stats_page(
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    stats = dashboard(session, user.id)
    return templates.TemplateResponse(
        request,
        "stats.html",
        page_context(
            request,
            user,
            page_title="Colophon — Statistics",
            active_nav="stats",
            stats=stats,
            value_text=format_price(stats.value, page_prefs(request).currency_symbol or "€"),
            reading_states=READING_STATES,
        ),
    )


# ---------------------------------------------------------------- shared context


def _resolve_zoom(request: Request, session: Session, user_id: int, zoom: str | None) -> str:
    prefs = page_prefs(request)
    # No query parameter means "whatever this user last chose" (A2), not the
    # built-in default; an unrecognised value is ignored outright so a typo in a
    # shared link cannot overwrite the stored choice.
    recognised = zoom.strip().upper() if zoom and zoom.strip() in ZOOM_VALUES else None
    chosen = normalise_zoom(recognised if recognised is not None else prefs.zoom_preset)
    if recognised is not None and chosen != prefs.zoom_preset:
        # Persisting on a GET is a deliberate shortcut: cosmetic, scoped to this
        # reader's own view; the CSRF-protected form is /settings.
        persist_zoom(session, user_id, chosen)
        # The request cached its prefs before that write; re-read so anything this
        # same request renders afterwards shows the zoom that was just stored.
        load_prefs_into_state(request, session, user_id)
    return chosen


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
    *,
    filters: Filters,
    active_nav: str,
    page_title: str,
    shelf_path: str,
    zoom: str | None = None,
    sections: list | None = None,
    rows: list[dict] | None = None,
    include_all: bool = False,
    searched: bool = False,
    notice: str | None = None,
) -> dict:
    """The shelf pages' own keys, merged over the common ones from page_context."""
    context = page_context(
        request,
        user,
        page_title=page_title,
        active_nav=active_nav,
        filters=filters,
        filter_query=filters.as_query(),
        has_filters=filters.is_active(),
        sections=sections or [],
        rows=rows or [],
        book_count=(
            sum(len(section.spines) for section in sections)
            if sections is not None
            else len(rows or [])
        ),
        shelf_path=shelf_path,
        reading_states=READING_STATES,
        rooms=room_options(session, user.id),
        authors=author_options(session, user.id),
        tags=tag_options(session, user.id),
        genres=genre_options(session, user.id),
        years=year_options(session, user.id),
        include_all=include_all,
        searched=searched,
        notice=notice,
    )
    if zoom is not None:
        # A shelf page renders with the zoom resolved for *this* request (query
        # parameter wins, then the stored preset), not merely the stored preset.
        context["zoom"] = zoom
    return context
