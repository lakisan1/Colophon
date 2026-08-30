# The single home of the shared page context.
#
# Every HTML page extends base.html, which reads `theme`, `user` and `page_title`,
# and the nav partial reads the active item plus the filter query string. When each
# router assembled those keys itself, any page could silently drop one — the edit
# page shipped rendering `data-theme="light"` for a user whose row says dark. This
# module is now the only place allowed to produce the common keys: a router passes
# the request, the signed-in user and its own page data, and `theme` cannot go
# missing because the router never touches it.
#
# The theme is read at most once per request. `current_user` (app/deps.py) — the
# dependency every signed-in route already runs, and no new middleware was invented
# for this — loads the effective prefs and stashes them on `request.state` under
# PREFS_STATE. Routes without a signed-in user (the login page) fall back to
# prefs.DEFAULTS, which is what login rendered before and why an anonymous request
# still renders instead of crashing.
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import Request
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.prefs import DEFAULTS, Prefs, load_prefs

if TYPE_CHECKING:
    # Typing only: app.deps imports this module, so importing it back at runtime
    # would be a cycle.
    from app.deps import CurrentUser

# The request.state key under which the per-request Prefs live.
PREFS_STATE = "colophon_prefs"


def page_prefs(request: Request) -> Prefs:
    """The effective prefs for this request, loaded at most once.

    `current_user` fills the cache before any signed-in router runs. The fallback
    covers anonymous requests and a database that has no settings table yet, so
    calling this first is safe rather than load-order-dependent.
    """
    prefs = getattr(request.state, PREFS_STATE, None)
    if prefs is None:
        prefs = Prefs(**DEFAULTS)
        setattr(request.state, PREFS_STATE, prefs)
    return prefs


def load_prefs_into_state(request: Request, session: Session, owner_id: int) -> None:
    """Load the owner's prefs and cache them for this request.

    Called by `current_user`; also the refresh path for a request that *writes*
    prefs mid-flight (saving settings, persisting shelf zoom) and must render the
    new values rather than the read from before the write.
    """
    try:
        prefs = load_prefs(session, owner_id)
    except OperationalError:
        # An unmigrated database: render with the defaults, same as the dev user.
        prefs = Prefs(**DEFAULTS)
    setattr(request.state, PREFS_STATE, prefs)


def page_context(
    request: Request,
    user: CurrentUser | None = None,
    *,
    page_title: str | None = None,
    active_nav: str | None = None,
    **page_keys: Any,
) -> dict[str, Any]:
    """The full context for a template: the common keys plus this page's own.

    Routers pass everything specific to the page (`book`, `rows`, `sections`,
    `errors`, …) as keyword arguments; everything the base template expects
    comes from here, so there is no second place a router could set `theme`
    wrongly. `active_shelf` is the name the shelf nav partial uses and travels
    as an alias of `active_nav` so templates stay untouched.
    """
    prefs = page_prefs(request)
    context: dict[str, Any] = {
        "user": user,
        "theme": prefs.theme,
        "zoom": prefs.zoom_preset,
        "currency": prefs.currency_symbol,
        "prefs": prefs,
        "page_title": page_title,
        "active_nav": active_nav,
        "active_shelf": active_nav,
        "filter_query": "",
    }
    context.update(page_keys)
    return context
