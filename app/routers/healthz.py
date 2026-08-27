# Liveness endpoint for probes and the container healthcheck (M7 wires the
# healthcheck itself). Deliberately dependency-free: no DB, no auth.
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
