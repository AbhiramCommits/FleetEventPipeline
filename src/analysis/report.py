"""End-to-end run report.

Reads the control plane, staging and mart, correlates against the injected
ground truth in ``data/injected_defects.json`` and writes:

* ``reports/metrics.json`` — machine-readable metrics (no placeholders), and
* ``reports/run_report.md``  — a human-readable summary.

All numbers are measured from the database produced by the run; nothing is
estimated.

Usage::

    python -m src.analysis.report
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import UTC, datetime
from typing import Any

from tabulate import tabulate

from src.common.config import get_settings
from src.common.db import fetch_dicts
from src.common.logging import get_logger, log_event

logger = get_logger("fleet.report")

REPORTS_DIR = get_settings().repo_root / "reports"
DEFECTS_FILE = get_settings().repo_root / "data" / "injected_defects.json"


def _scalar(sql: str) -> Any:
    rows = fetch_dicts(sql)
    return next(iter(rows[0].values())) if rows else None


def _latest_run(job_name: str) -> dict[str, Any]:
    rows = fetch_dicts(
        "SELECT run_id, started_at, finished_at, status, rows_read, rows_written, rows_deduped, rows_rejected, notes "
        "FROM ctl.pipeline_run WHERE job_name = %s ORDER BY started_at DESC LIMIT 1",
        (job_name,),
    )
    return rows[0] if rows else {}


def _extract_retries(notes: str | None) -> int:
    if not notes:
        return 0
    match = re.search(r"retries=(\d+)", notes)
    return int(match.group(1)) if match else 0


def _ingestion_metrics() -> dict[str, Any]:
    run = _latest_run("fleet_ingest")
    elapsed = 0.0
    if run.get("started_at") and run.get("finished_at"):
        elapsed = (run["finished_at"] - run["started_at"]).total_seconds()
    rows_read = run.get("rows_read", 0) or 0
    return {
        "run_id": str(run.get("run_id")) if run.get("run_id") else None,
        "wall_clock_sec": round(elapsed, 3),
        "rows_read_total": rows_read,
        "rows_landed_total": run.get("rows_written", 0) or 0,
        "rows_deduped": run.get("rows_deduped", 0) or 0,
        "rows_rejected": run.get("rows_rejected", 0) or 0,
        "retry_count": _extract_retries(run.get("notes")),
        "rows_per_sec": round(rows_read / elapsed, 1) if elapsed else 0.0,
    }


def _table_counts() -> dict[str, dict[str, int]]:
    raw = {
        "gps_ping_raw": _scalar("SELECT count(*) FROM raw.gps_ping_raw"),
        "geofence_event_raw": _scalar("SELECT count(*) FROM raw.geofence_event_raw"),
    }
    staging = {
        "gps_ping": _scalar("SELECT count(*) FROM stg.gps_ping"),
        "geofence_event": _scalar("SELECT count(*) FROM stg.geofence_event"),
        "vehicle": _scalar("SELECT count(*) FROM stg.vehicle"),
        "operator": _scalar("SELECT count(*) FROM stg.operator"),
        "geofence": _scalar("SELECT count(*) FROM stg.geofence"),
    }
    mart = {
        t: _scalar(f"SELECT count(*) FROM mart.{t}")
        for t in [
            "dim_vehicle",
            "dim_operator",
            "dim_geofence",
            "dim_date",
            "dim_time_of_day",
            "fact_gps_ping",
            "fact_trip_segment",
            "fact_idle_event",
            "fact_geofence_transition",
        ]
    }
    return {"raw": raw, "staging": staging, "mart": mart}


def _reject_breakdown() -> dict[str, int]:
    rows = fetch_dicts("SELECT reason_code, count(*) AS n FROM ctl.reject GROUP BY reason_code ORDER BY reason_code")
    return {r["reason_code"]: r["n"] for r in rows}


def _defect_recall(ingestion: dict[str, Any], rejects: dict[str, int], unmatched_exits: int, stale: int) -> list[dict[str, Any]]:
    try:
        injected = json.loads(DEFECTS_FILE.read_text())["injected"]
    except Exception:  # noqa: BLE001
        injected = {}
    caught_map = {
        "duplicate_events": ingestion["rows_deduped"],
        "late_arriving": rejects.get("LATE_ARRIVING", 0),
        "null_gps": rejects.get("MISSING_GPS", 0),
        "null_island": rejects.get("NULL_ISLAND", 0),
        "out_of_range": rejects.get("OUT_OF_RANGE_LATLON", 0),
        "unmatched_geofence_exits": unmatched_exits,
        "stale_vehicles": stale,
    }
    out = []
    for defect, injected_n in injected.items():
        caught = caught_map.get(defect, 0)
        recall = round(caught / injected_n * 100, 2) if injected_n else 0.0
        out.append({"defect": defect, "injected": injected_n, "caught": caught, "recall_pct": recall})
    return out


def _utilization() -> list[dict[str, Any]]:
    moving = {
        r["asset_type"]: float(r["minutes"])
        for r in fetch_dicts(
            "SELECT dv.asset_type, COALESCE(sum(extract(epoch FROM (f.end_ts - f.start_ts)) / 60.0), 0) AS minutes "
            "FROM mart.fact_trip_segment f JOIN mart.dim_vehicle dv USING (vehicle_key) GROUP BY dv.asset_type"
        )
    }
    idle = {
        r["asset_type"]: float(r["minutes"])
        for r in fetch_dicts(
            "SELECT dv.asset_type, COALESCE(sum(f.duration_min), 0) AS minutes "
            "FROM mart.fact_idle_event f JOIN mart.dim_vehicle dv USING (vehicle_key) GROUP BY dv.asset_type"
        )
    }
    asset_types = sorted(set(moving) | set(idle))
    out = []
    for at in asset_types:
        engine = moving.get(at, 0.0) + idle.get(at, 0.0)
        util = round(moving.get(at, 0.0) / engine * 100, 2) if engine else 0.0
        idle_pct = round(idle.get(at, 0.0) / engine * 100, 2) if engine else 0.0
        out.append(
            {
                "asset_type": at,
                "moving_hours": round(moving.get(at, 0.0) / 60, 2),
                "idle_hours": round(idle.get(at, 0.0) / 60, 2),
                "utilization_pct": util,
                "idle_pct": idle_pct,
            }
        )
    return out


def _top_idle_vehicles() -> list[dict[str, Any]]:
    rows = fetch_dicts(
        "SELECT dv.vehicle_id, dv.name, dv.asset_type, round((sum(f.duration_min) / 60.0)::numeric, 2) AS idle_hours "
        "FROM mart.fact_idle_event f JOIN mart.dim_vehicle dv USING (vehicle_key) "
        "GROUP BY dv.vehicle_id, dv.name, dv.asset_type ORDER BY idle_hours DESC LIMIT 10"
    )
    return [
        {
            "vehicle_id": r["vehicle_id"],
            "name": r["name"],
            "asset_type": r["asset_type"],
            "idle_hours": float(r["idle_hours"]) if r["idle_hours"] is not None else 0.0,
        }
        for r in rows
    ]


def _geofence_by_site() -> list[dict[str, Any]]:
    counts = {
        r["geofence_id"]: r
        for r in fetch_dicts(
            "SELECT dg.geofence_id, dg.site_name, dg.job_number, "
            "count(*) FILTER (WHERE e.event_type = 'entry') AS entries, "
            "count(*) FILTER (WHERE e.event_type = 'exit') AS exits "
            "FROM stg.geofence_event e JOIN mart.dim_geofence dg USING (geofence_id) "
            "WHERE dg.site_type = 'job_site' GROUP BY dg.geofence_id, dg.site_name, dg.job_number"
        )
    }
    dwell = {
        r["geofence_id"]: r["mean_dwell"]
        for r in fetch_dicts(
            "SELECT dg.geofence_id, round(avg(f.dwell_min)::numeric, 2) AS mean_dwell "
            "FROM mart.fact_geofence_transition f JOIN mart.dim_geofence dg USING (geofence_key) "
            "WHERE f.dwell_min IS NOT NULL GROUP BY dg.geofence_id"
        )
    }
    out = []
    for gid, r in sorted(counts.items()):
        out.append(
            {
                "site_name": r["site_name"],
                "job_number": r["job_number"],
                "entries": r["entries"],
                "exits": r["exits"],
                "mean_dwell_min": float(dwell[gid]) if gid in dwell and dwell[gid] is not None else None,
            }
        )
    return out


def _dq_gates() -> tuple[list[dict[str, Any]], str | None]:
    run = _latest_run("dq")
    if not run.get("run_id"):
        return [], None
    rows = fetch_dicts(
        "SELECT check_name, severity, passed, observed, threshold, detail FROM ctl.dq_result "
        "WHERE run_id = %s ORDER BY id",
        (run["run_id"],),
    )
    gates = [
        {
            "check_name": r["check_name"],
            "severity": r["severity"],
            "passed": bool(r["passed"]),
            "observed": float(r["observed"]) if r["observed"] is not None else None,
            "threshold": float(r["threshold"]) if r["threshold"] is not None else None,
            "detail": r["detail"],
        }
        for r in rows
    ]
    return gates, str(run["run_id"])


def _stale_vehicle_count() -> int:
    return _scalar(
        "SELECT count(*) FROM (SELECT vehicle_id FROM stg.gps_ping GROUP BY vehicle_id "
        "HAVING (now() - max(ts)) > interval '24 hours') s"
    )


def collect_metrics() -> dict[str, Any]:
    """Assemble the full metrics dictionary from the database."""
    ingestion = _ingestion_metrics()
    tables = _table_counts()
    rejects = _reject_breakdown()
    unmatched = _scalar(
        "SELECT count(*) FROM mart.fact_geofence_transition WHERE entered_at IS NULL"
    )
    stale = _stale_vehicle_count()
    gates, dq_run_id = _dq_gates()
    elt = _latest_run("elt")

    started = os.getenv("DEMO_STARTED_AT")
    ended = os.getenv("DEMO_ENDED_AT")
    if started and ended:
        total_runtime = round(float(ended) - float(started), 2)
    else:
        elt_elapsed = 0.0
        if elt.get("started_at") and elt.get("finished_at"):
            elt_elapsed = (elt["finished_at"] - elt["started_at"]).total_seconds()
        total_runtime = round(ingestion["wall_clock_sec"] + elt_elapsed, 2)

    metrics: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "run_ids": {
            "ingest": ingestion["run_id"],
            "elt": str(elt.get("run_id")) if elt.get("run_id") else None,
            "dq": dq_run_id,
        },
        "total_pipeline_runtime_sec": total_runtime,
        "ingestion": ingestion,
        "tables": tables,
        "rejects": {"total": sum(rejects.values()), "by_reason": rejects},
        "dedupe": {
            "duplicates_blocked": ingestion["rows_deduped"],
            "injected_duplicates": None,
            "dedupe_recall_pct": None,
        },
        "defect_recall": _defect_recall(ingestion, rejects, unmatched or 0, stale or 0),
        "utilization_by_asset_type": _utilization(),
        "top_idle_vehicles": _top_idle_vehicles(),
        "geofence_by_site": _geofence_by_site(),
        "dq_gates": gates,
        "dq_pass_count": sum(1 for g in gates if g["passed"]),
        "dq_total": len(gates),
    }
    for d in metrics["defect_recall"]:
        if d["defect"] == "duplicate_events":
            metrics["dedupe"]["injected_duplicates"] = d["injected"]
            metrics["dedupe"]["dedupe_recall_pct"] = d["recall_pct"]
    return metrics


def _render_report(m: dict[str, Any]) -> str:
    ing = m["ingestion"]
    lines: list[str] = []
    lines.append("# FleetEventPipeline — run report\n")
    lines.append(f"_Generated {m['generated_at']}_\n")
    lines.append("## Headline\n")
    lines.append(
        tabulate(
            [
                ["Events ingested (read)", f"{ing['rows_read_total']:,}"],
                ["Rows raw-landed", f"{ing['rows_landed_total']:,}"],
                ["Duplicates blocked", f"{ing['rows_deduped']:,}"],
                ["Rows rejected", f"{ing['rows_rejected']:,}"],
                ["Ingestion wall-clock", f"{ing['wall_clock_sec']} s"],
                ["Throughput", f"{ing['rows_per_sec']:,} rows/s"],
                ["Retries (429/503)", f"{ing['retry_count']}"],
                ["Total pipeline runtime", f"{m['total_pipeline_runtime_sec']} s"],
                ["DQ gates passed", f"{m['dq_pass_count']}/{m['dq_total']}"],
            ],
            tablefmt="github",
        )
    )
    lines.append("\n## Rows per table\n")
    lines.append("### Raw\n")
    lines.append(tabulate([[k, f"{v:,}"] for k, v in m["tables"]["raw"].items()], headers=["table", "rows"], tablefmt="github"))
    lines.append("\n### Staging\n")
    lines.append(tabulate([[k, f"{v:,}"] for k, v in m["tables"]["staging"].items()], headers=["table", "rows"], tablefmt="github"))
    lines.append("\n### Mart\n")
    lines.append(tabulate([[k, f"{v:,}"] for k, v in m["tables"]["mart"].items()], headers=["table", "rows"], tablefmt="github"))

    lines.append("\n## Injected vs caught defects\n")
    lines.append(
        tabulate(
            [[d["defect"], d["injected"], d["caught"], f"{d['recall_pct']}%"] for d in m["defect_recall"]],
            headers=["defect", "injected", "caught", "recall"],
            tablefmt="github",
        )
    )

    lines.append("\n## Rejected rows by reason\n")
    lines.append(
        tabulate([[k, v] for k, v in m["rejects"]["by_reason"].items()], headers=["reason_code", "rows"], tablefmt="github")
    )

    lines.append("\n## Utilization by asset type\n")
    lines.append(
        tabulate(
            [[u["asset_type"], u["moving_hours"], u["idle_hours"], f"{u['utilization_pct']}%", f"{u['idle_pct']}%"] for u in m["utilization_by_asset_type"]],
            headers=["asset_type", "moving_hours", "idle_hours", "utilization", "idle"],
            tablefmt="github",
        )
    )

    lines.append("\n## Top 10 idle assets (hours)\n")
    lines.append(
        tabulate(
            [[v["vehicle_id"], v["name"], v["asset_type"], v["idle_hours"]] for v in m["top_idle_vehicles"]],
            headers=["vehicle_id", "name", "asset_type", "idle_hours"],
            tablefmt="github",
        )
    )

    lines.append("\n## Geofence activity by site\n")
    lines.append(
        tabulate(
            [[g["site_name"], g["job_number"], g["entries"], g["exits"], g["mean_dwell_min"]] for g in m["geofence_by_site"]],
            headers=["site", "job", "entries", "exits", "mean_dwell_min"],
            tablefmt="github",
        )
    )

    lines.append("\n## Data-quality gates\n")
    lines.append(
        tabulate(
            [[g["check_name"], "PASS" if g["passed"] else "FAIL", g["observed"], g["threshold"], g["detail"]] for g in m["dq_gates"]],
            headers=["check", "result", "observed", "threshold", "detail"],
            tablefmt="github",
        )
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    """CLI entrypoint."""
    metrics = collect_metrics()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str))
    (REPORTS_DIR / "run_report.md").write_text(_render_report(metrics))

    print(
        tabulate(
            [
                ["Events ingested", f"{metrics['ingestion']['rows_read_total']:,}"],
                ["Throughput", f"{metrics['ingestion']['rows_per_sec']:,} rows/s"],
                ["Duplicates blocked", f"{metrics['ingestion']['rows_deduped']:,}"],
                ["Dedupe recall", f"{metrics['dedupe']['dedupe_recall_pct']}%"],
                ["Rejected", f"{metrics['rejects']['total']:,}"],
                ["DQ gates", f"{metrics['dq_pass_count']}/{metrics['dq_total']}"],
            ],
            tablefmt="github",
        )
    )
    log_event(logger, "report written", metrics_path=str(REPORTS_DIR / "metrics.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
