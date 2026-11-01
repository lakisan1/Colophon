# Liveness endpoint for probes and container healthchecks. Deliberately
# dependency-free: no DB, no auth.
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
