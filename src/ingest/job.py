"""Incremental ingestion job (Azure Container Apps Job / timer-triggered Function).

For each configured vendor source the job:

1. opens a ``ctl.pipeline_run`` row,
2. reads the stored watermark and pages the vendor API forward from it,
3. lands every page into ``raw.*`` as JSONB and advances the watermark in the
   same transaction (at-least-once), and
4. publishes Event Grid envelopes for the landed rows.

Run it with::

    python -m src.ingest.job --source all --lookback-hours 720
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from src.common.config import get_settings
from src.common.db import execute_many, get_conn
from src.common.logging import get_logger, log_event, set_run_id
from src.ingest.client import FleetApiClient
from src.ingest.eventgrid import build_envelopes, publish
from src.ingest.reference_sync import sync_references
from src.ingest.watermark import advance_watermark, read_watermark

logger = get_logger("fleet.ingest.job")

SOURCE_TABLE: dict[str, tuple[str, str]] = {
    "gps": ("raw.gps_ping_raw", "/v1/fleet/gps"),
    "geofence": ("raw.geofence_event_raw", "/v1/fleet/geofence-events"),
}


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def start_run(job_name: str, conn: psycopg.Connection) -> uuid.UUID:
    """Insert a running ``ctl.pipeline_run`` row and return its uuid."""
    run_id = uuid.uuid4()
    conn.execute(
        "INSERT INTO ctl.pipeline_run (run_id, job_name, status) VALUES (%s, %s, 'running')",
        (run_id, job_name),
    )
    set_run_id(str(run_id))
    log_event(logger, "run started", job_name=job_name)
    return run_id


def finish_run(
    conn: psycopg.Connection,
    run_id: uuid.UUID,
    status: str,
    rows_read: int = 0,
    rows_written: int = 0,
    rows_rejected: int | None = None,
    rows_deduped: int | None = None,
    notes: str | None = None,
) -> None:
    """Finalize a ``ctl.pipeline_run`` row.

    ``rows_rejected`` / ``rows_deduped`` default to ``None`` so the counters the
    Event Grid handler incremented during the run are preserved rather than
    overwritten.
    """
    conn.execute(
        """
        UPDATE ctl.pipeline_run
           SET finished_at   = now(),
               status        = %s,
               rows_read     = %s,
               rows_written  = %s,
               rows_rejected = COALESCE(%s, rows_rejected),
               rows_deduped  = COALESCE(%s, rows_deduped),
               notes         = %s
         WHERE run_id = %s
        """,
        (status, rows_read, rows_written, rows_rejected, rows_deduped, notes, run_id),
    )


def run_source(
    source: str,
    client: FleetApiClient,
    conn: psycopg.Connection,
    run_id: uuid.UUID,
    limit: int,
    publish_events: bool = True,
) -> dict[str, Any]:
    """Ingest one source incrementally. Returns per-source statistics."""
    raw_table, endpoint = SOURCE_TABLE[source]
    insert_sql = f"INSERT INTO {raw_table} (payload, source_id, run_id) VALUES (%s, %s, %s)"

    watermark = read_watermark(source, conn)
    after = watermark.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if watermark else None
    log_event(logger, "source start", source=source, after=after, endpoint=endpoint)

    rows_read = 0
    rows_landed = 0
    pages = 0
    retries_before = client.stats.retries
    started = time.perf_counter()

    for page in client.paginate(endpoint, after=after, limit=limit):
        rows: list[dict[str, Any]] = page["data"]
        pages += 1
        rows_read += len(rows)
        if rows:
            max_ts = max(_parse_ts(r["ts"]) for r in rows)
            with conn.transaction():
                execute_many(insert_sql, [(Jsonb(r), str(r.get("id")), run_id) for r in rows], conn, batch_size=1000)
                advance_watermark(source, max_ts, run_id, conn)
            rows_landed += len(rows)
            if publish_events:
                envelopes = build_envelopes(source, rows, str(run_id))
                publish(envelopes)
        log_event(
            logger,
            "page landed",
            source=source,
            page=pages,
            rows=len(rows),
            end_cursor=page["end_cursor"],
            latency_ms=page["latency_ms"],
            cumulative_retries=client.stats.retries - retries_before,
        )

    elapsed = time.perf_counter() - started
    stats = {
        "source": source,
        "rows_read": rows_read,
        "rows_landed": rows_landed,
        "pages": pages,
        "retries": client.stats.retries - retries_before,
        "elapsed_sec": round(elapsed, 3),
    }
    log_event(logger, "source complete", **stats)
    return stats


def run(sources: list[str], lookback_hours: int, publish_events: bool = True) -> dict[str, Any]:
    """Run the ingestion job for the requested sources."""
    settings = get_settings()
    conn = get_conn(autocommit=True)
    try:
        run_id = start_run("fleet_ingest", conn)
        client = FleetApiClient(page_size=settings.page_size)
        results: list[dict[str, Any]] = []
        total_read = total_landed = total_retries = 0
        started = time.perf_counter()
        try:
            sync_references(client, conn)
            for source in sources:
                res = run_source(source, client, conn, run_id, limit=settings.page_size, publish_events=publish_events)
                results.append(res)
                total_read += res["rows_read"]
                total_landed += res["rows_landed"]
                total_retries += res["retries"]
            elapsed = round(time.perf_counter() - started, 3)
            finish_run(
                conn,
                run_id,
                "succeeded",
                rows_read=total_read,
                rows_written=total_landed,
                notes=f"sources={','.join(sources)} retries={total_retries} elapsed_sec={elapsed}",
            )
            log_event(logger, "ingestion complete", run_id=str(run_id), rows_read=total_read, rows_landed=total_landed)
            return {"run_id": str(run_id), "results": results, "rows_read": total_read, "rows_landed": total_landed}
        except Exception as exc:  # noqa: BLE001
            finish_run(conn, run_id, "failed", rows_read=total_read, rows_written=total_landed, notes=str(exc))
            log_event(logger, "ingestion failed", error=str(exc))
            raise
        finally:
            client.close()
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Incremental fleet ingestion job")
    parser.add_argument("--source", choices=["all", "gps", "geofence"], default="all")
    parser.add_argument("--lookback-hours", type=int, default=get_settings().lookback_hours)
    parser.add_argument("--no-publish", action="store_true", help="Land raw rows without publishing events")
    args = parser.parse_args(argv)

    sources = ["gps", "geofence"] if args.source == "all" else [args.source]
    result = run(sources, args.lookback_hours, publish_events=not args.no_publish)
    log_event(logger, "job done", run_id=result["run_id"], rows_landed=result["rows_landed"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
