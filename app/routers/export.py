# CSV and JSON export (SPEC §11). Two routes, no import partner (D13): the library
# is entered by hand, so the only direction data moves is out.
#
# The export obeys the same filters as the page it was reached from, so "export what
# I am looking at" works, and no filters means the owner's entire library.
from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.db import get_session
from app.deps import CurrentUser, current_user
from app.export import export_rows, to_csv, to_json
from app.filters import parse_filters

router = APIRouter()

CSV_MEDIA_TYPE = "text/csv; charset=utf-8"
JSON_MEDIA_TYPE = "application/json"


@router.get("/export.csv")
def export_csv(
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    filters = parse_filters(request.query_params, scope="export")
    body = to_csv(export_rows(session, user.id, filters))
    return Response(
        content=body,
        media_type=CSV_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{_filename("csv")}"'},
    )


@router.get("/export.json")
def export_json(
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    filters = parse_filters(request.query_params, scope="export")
    body = to_json(export_rows(session, user.id, filters))
    return Response(
        content=body,
        media_type=JSON_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{_filename("json")}"'},
    )


def _filename(extension: str) -> str:
    # A dated name so a folder of exports reads chronologically without opening them.
    return f"colophon-{date.today().isoformat()}.{extension}"
