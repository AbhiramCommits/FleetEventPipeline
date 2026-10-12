"""Publish landed batches as Azure Event Grid schema envelopes.

Two modes, selected purely by configuration:

* **Real topic** — ``EVENTGRID_ENDPOINT`` is set: envelopes are POSTed to the
  topic with the ``aeg-sas-key`` header (exactly what a real subscription
  expects).
* **Emulated** — ``EVENTGRID_ENDPOINT`` is unset: the *identical* envelopes are
  POSTed to ``HANDLER_URL`` (the Node Functions endpoint) and a note is logged.
  Swapping to a real topic is a config change only, no code change.

Envelopes are grouped per vehicle so ``subject`` can follow the
``/fleet/vehicles/<id>`` convention, then POSTed in arrays of at most 100.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx

from src.common.config import get_settings
from src.common.logging import get_logger, log_event

logger = get_logger("fleet.ingest.eventgrid")

EVENT_TYPE_BY_SOURCE = {
    "gps": "Fleet.GpsPingBatchLanded",
    "geofence": "Fleet.GeofenceEventLanded",
}
DATA_VERSION = "1.0"
MAX_BATCH = 100


def build_envelopes(source: str, rows: list[dict[str, Any]], run_id: str) -> list[dict[str, Any]]:
    """Group landed rows by vehicle and wrap each group in an Event Grid envelope."""
    event_type = EVENT_TYPE_BY_SOURCE[source]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("vehicleId") or row.get("vehicle_id") or "unknown"), []).append(row)

    event_time = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    envelopes: list[dict[str, Any]] = []
    for vehicle_id, group in grouped.items():
        envelopes.append(
            {
                "id": str(uuid.uuid4()),
                "eventType": event_type,
                "subject": f"/fleet/vehicles/{vehicle_id}",
                "eventTime": event_time,
                "dataVersion": DATA_VERSION,
                "data": {
                    "source": source,
                    "vehicleId": vehicle_id,
                    "runId": run_id,
                    "count": len(group),
                    "rows": group,
                },
            }
        )
    return envelopes


def _chunks(items: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def publish(envelopes: list[dict[str, Any]]) -> dict[str, Any]:
    """POST envelopes (arrays of <=100) to the configured Event Grid sink."""
    settings = get_settings()
    if not envelopes:
        return {"published": 0, "posts": 0, "mode": "none"}

    emulated = settings.eventgrid_emulated
    url = settings.handler_url if emulated else settings.eventgrid_endpoint
    headers = {"Content-Type": "application/json"}
    if emulated:
        log_event(logger, "Event Grid emulated mode: posting to HANDLER_URL", handler_url=url)
    else:
        headers["aeg-sas-key"] = settings.eventgrid_key

    posts = 0
    published = 0
    with httpx.Client(timeout=30.0) as client:
        for batch in _chunks(envelopes, MAX_BATCH):
            resp = client.post(url, json=batch, headers=headers)
            resp.raise_for_status()
            posts += 1
            published += len(batch)
    log_event(logger, "events published", published=published, posts=posts, mode="emulated" if emulated else "topic")
    return {"published": published, "posts": posts, "mode": "emulated" if emulated else "topic"}
