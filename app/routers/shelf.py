# The shelf page (M3). Renders the owner's books as spines using the scale model
# in `app.shelf`; the router's own job is small: resolve zoom, load rows, order
# them. Rooms are filters, not pages (D7) — filtering itself lands in M6.
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db import get_session
from app.deps import CurrentUser, current_user
from app.prefs import load_prefs, persist_zoom
from app.shelf import build_shelf, normalise_zoom
from app.shelf_store import shelf_rows
from app.templating import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def shelf_page(
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    zoom: Annotated[str | None, Query(max_length=8)] = None,
) -> HTMLResponse:
    prefs = load_prefs(session, user.id)
    # No query parameter means "whatever this user last chose" (A2), not the
    # built-in default; a parameter that is not a preset is ignored outright so
    # a typo in a shared link cannot overwrite the stored choice.
    recognised = (
        zoom.strip().upper() if zoom and zoom.strip() in {"s", "m", "l", "S", "M", "L"} else None
    )
    chosen = normalise_zoom(recognised if recognised is not None else prefs.zoom_preset)

    # A2 says the zoom preset is remembered per user. Persisting on a GET is a
    # deliberate shortcut: the effect is cosmetic and scoped to the reader's own
    # view, and M5 replaces it with a CSRF-protected settings form.
    if recognised is not None and chosen != prefs.zoom_preset:
        persist_zoom(session, user.id, chosen)

    sections = build_shelf(shelf_rows(session, user.id), chosen)
    return templates.TemplateResponse(
        request,
        "shelf.html",
        {
            "user": user,
            "theme": prefs.theme,
            "page_title": "Colophon — My library",
            "zoom": chosen,
            "sections": sections,
            "book_count": sum(len(section.spines) for section in sections),
        },
    )
