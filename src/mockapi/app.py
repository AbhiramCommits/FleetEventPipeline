"""FastAPI mock of a Samsara-style telematics vendor API.

Endpoints mirror a real vendor's fleet/reference/paginated-telemetry surface so
the ingestion job can be developed and tested without a live account. Every
list endpoint requires ``Authorization: Bearer <FLEET_API_TOKEN>`` and returns
``{"data": [...], "pagination": {"end_cursor": ..., "has_next": bool}}``.
"""

from __future__ import annotations

import base64
import json
import os
import random
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query

from src.common.config import get_settings
from src.mockapi import generator

FAILURE_RATE = float(os.getenv("MOCKAPI_FAILURE_RATE", "0.03"))
_fail_rng = random.Random(7919)

app = FastAPI(title="Fleet Mock Vendor API", version="1.0.0")

_dataset: dict[str, Any] | None = None


def dataset() -> dict[str, Any]:
    """Lazily load (or generate) the synthetic dataset once per process."""
    global _dataset
    if _dataset is None:
        _dataset = generator.load_or_generate()
    return _dataset


def require_auth(authorization: str | None = Header(default=None)) -> None:
    """Enforce ``Authorization: Bearer <token>`` on every endpoint."""
    expected = get_settings().fleet_api_token
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if token != expected:
        raise HTTPException(status_code=401, detail="Invalid bearer token")


def maybe_fail() -> None:
    """Inject transient 429/503 responses (~3% of requests) to exercise retries."""
    if FAILURE_RATE <= 0:
        return
    if _fail_rng.random() < FAILURE_RATE:
        code = 429 if _fail_rng.random() < 0.6 else 503
        raise HTTPException(status_code=code, detail="transient vendor error")


def _encode_cursor(offset: int) -> str:
    raw = json.dumps({"o": offset}).encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        return int(json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())["o"])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Invalid cursor") from exc


def _paginate(
    items: list[dict[str, Any]],
    after: str | None,
    cursor: str | None,
    limit: int,
) -> dict[str, Any]:
    """Return one page plus pagination metadata. ``items`` must be ts-sorted."""
    filtered = items
    if after:
        filtered = [
            e
            for e in items
            if _parse_ts(e.get("ts")) and _parse_ts(e["ts"]) > _parse_ts(after)
        ]
    offset = _decode_cursor(cursor)
    page = filtered[offset : offset + limit]
    next_offset = offset + limit
    has_next = next_offset < len(filtered)
    return {
        "data": page,
        "pagination": {
            "end_cursor": _encode_cursor(next_offset) if has_next else None,
            "has_next": has_next,
        },
    }


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@app.get("/v1/fleet/vehicles", dependencies=[Depends(require_auth)])
def vehicles() -> dict[str, Any]:
    """Return all assets."""
    maybe_fail()
    data = dataset()["reference"]["vehicles"]
    return {"data": data, "pagination": {"end_cursor": None, "has_next": False}}


@app.get("/v1/fleet/operators", dependencies=[Depends(require_auth)])
def operators() -> dict[str, Any]:
    """Return all operators."""
    maybe_fail()
    data = dataset()["reference"]["operators"]
    return {"data": data, "pagination": {"end_cursor": None, "has_next": False}}


@app.get("/v1/fleet/geofences", dependencies=[Depends(require_auth)])
def geofences() -> dict[str, Any]:
    """Return all geofences (yards and job sites)."""
    maybe_fail()
    data = dataset()["reference"]["geofences"]
    return {"data": data, "pagination": {"end_cursor": None, "has_next": False}}


@app.get("/v1/fleet/gps", dependencies=[Depends(require_auth)])
def gps(
    after: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=5000),
) -> dict[str, Any]:
    """Return a ts-ordered page of GPS pings."""
    maybe_fail()
    return _paginate(dataset()["gps"], after, cursor, limit)


@app.get("/v1/fleet/geofence-events", dependencies=[Depends(require_auth)])
def geofence_events(
    after: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=5000),
) -> dict[str, Any]:
    """Return a ts-ordered page of geofence entry/exit events."""
    maybe_fail()
    return _paginate(dataset()["geofenceEvents"], after, cursor, limit)
