"""Deterministic synthetic fleet generator for the mock telematics vendor.

The generator is seeded with :data:`SEED` so every process produces the exact
same fleet, geofences and event stream. It writes a cache to
``data/generated/fleet_data.json`` (gitignored) and always records the true
injected-defect counts to ``data/injected_defects.json`` (committed) so the
downstream data-quality report can measure dedupe recall against ground truth.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SEED = 1993
NUM_VEHICLES = 40
NUM_SITES = 6
NUM_YARDS = 3
HISTORY_DAYS = 30
STALE_VEHICLES = 3
STALE_AGE_HOURS = 26

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = REPO_ROOT / "data" / "generated"
CACHE_FILE = CACHE_DIR / "fleet_data.json"
DEFECTS_FILE = REPO_ROOT / "data" / "injected_defects.json"

ASSET_TYPES = ["excavator", "dozer", "haul_truck", "pickup"]
ASSET_MAKES: dict[str, list[tuple[str, str]]] = {
    "excavator": [("CAT", "336"), ("Komatsu", "PC360"), ("Deere", "350G")],
    "dozer": [("CAT", "D6"), ("Komatsu", "D65"), ("Deere", "850K")],
    "haul_truck": [("CAT", "745"), ("Volvo", "A40G"), ("Komatsu", "HM400")],
    "pickup": [("Ford", "F-250"), ("Chevy", "Silverado 2500"), ("Ram", "2500")],
}
LICENSE_CLASSES = ["A", "B", "C"]

# Approximate Kansas City metro bounding area.
LAT0, LON0 = 39.05, -94.55


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    radius = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


@dataclass
class Reference:
    """Reference data returned by the fleet endpoints."""

    vehicles: list[dict[str, Any]]
    operators: list[dict[str, Any]]
    geofences: list[dict[str, Any]]


def _build_geofences(rng: random.Random) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    geofences: list[dict[str, Any]] = []
    yards: list[dict[str, Any]] = []
    for i in range(NUM_YARDS):
        clat = LAT0 + rng.uniform(-0.05, 0.05)
        clon = LON0 + rng.uniform(-0.05, 0.05)
        yards.append(
            {
                "geofenceId": f"YARD-{i + 1:02d}",
                "siteName": f"Yard {i + 1}",
                "jobNumber": None,
                "siteType": "yard",
                "minLat": round(clat - 0.004, 6),
                "maxLat": round(clat + 0.004, 6),
                "minLon": round(clon - 0.005, 6),
                "maxLon": round(clon + 0.005, 6),
            }
        )
    sites: list[dict[str, Any]] = []
    for i in range(NUM_SITES):
        clat = LAT0 + rng.uniform(-0.25, 0.25)
        clon = LON0 + rng.uniform(-0.25, 0.25)
        sites.append(
            {
                "geofenceId": f"SITE-{i + 1:02d}",
                "siteName": f"{rng.choice(['Riverside', 'Northgate', 'Summit', 'Fairview', 'Bluffs', 'Cedar'])} Site {i + 1}",
                "jobNumber": f"J-{1000 + i * 7}",
                "siteType": "job_site",
                "minLat": round(clat - 0.01, 6),
                "maxLat": round(clat + 0.01, 6),
                "minLon": round(clon - 0.012, 6),
                "maxLon": round(clon + 0.012, 6),
            }
        )
    geofences.extend(yards)
    geofences.extend(sites)
    return geofences, yards + sites


def _point_in(bbox: dict[str, Any], rng: random.Random) -> tuple[float, float]:
    return (
        round(rng.uniform(bbox["minLat"], bbox["maxLat"]), 6),
        round(rng.uniform(bbox["minLon"], bbox["maxLon"]), 6),
    )


def build_reference(rng: random.Random) -> tuple[Reference, list[dict[str, Any]]]:
    """Build vehicles, operators and geofences."""
    geofences, all_geofences = _build_geofences(rng)
    vehicle_meta: list[dict[str, Any]] = []
    for i in range(NUM_VEHICLES):
        asset_type = ASSET_TYPES[i % len(ASSET_TYPES)]
        make, model = rng.choice(ASSET_MAKES[asset_type])
        yard = all_geofences[i % NUM_YARDS]
        site = all_geofences[NUM_YARDS + (i % NUM_SITES)]
        vehicle_meta.append(
            {
                "vehicleId": f"VEH-{i + 1:03d}",
                "name": f"Unit {i + 1:03d}",
                "make": make,
                "model": model,
                "year": rng.choice([2016, 2018, 2019, 2020, 2021, 2022, 2023]),
                "assetType": asset_type,
                "serial": f"SN{i + 1:05d}{rng.randint(100, 999)}",
                "homeYard": yard["geofenceId"],
                "yard": yard,
                "site": site,
            }
        )
    operators = [
        {
            "operatorId": f"OP-{i + 1:03d}",
            "name": rng.choice(
                ["J. Alvarez", "M. Chen", "R. Patel", "T. Brooks", "K. Nguyen", "D. Ortiz", "S. Kim", "A. Flores"]
            )
            + f" {i + 1}",
            "employeeNo": f"E{2000 + i}",
            "homeYard": all_geofences[i % NUM_YARDS]["geofenceId"],
            "licenseClass": rng.choice(LICENSE_CLASSES),
        }
        for i in range(24)
    ]
    ref = Reference(
        vehicles=[{k: v for k, v in m.items() if k not in ("yard", "site")} for m in vehicle_meta],
        operators=operators,
        geofences=geofences,
    )
    return ref, vehicle_meta


def _workdays(now: datetime, rng: random.Random) -> list[datetime]:
    days: list[datetime] = []
    for d in range(HISTORY_DAYS):
        day = (now - timedelta(days=d)).replace(hour=0, minute=0, second=0, microsecond=0)
        if day.weekday() < 5 or (day.weekday() == 5 and rng.random() < 0.3):
            days.append(day)
    return sorted(days)


def _gen_vehicle_day(
    vehicle: dict[str, Any],
    operator_id: str,
    day: datetime,
    rng: random.Random,
    seq_start: int,
    odometer: float,
    engine_hours: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, float, float]:
    gps: list[dict[str, Any]] = []
    geo: list[dict[str, Any]] = []
    seq = seq_start

    yard = vehicle["yard"]
    site = vehicle["site"]
    yard_lat = (yard["minLat"] + yard["maxLat"]) / 2
    yard_lon = (yard["minLon"] + yard["maxLon"]) / 2
    site_lat = (site["minLat"] + site["maxLat"]) / 2
    site_lon = (site["minLon"] + site["maxLon"]) / 2

    shift_start = day + timedelta(hours=rng.randint(6, 8), minutes=rng.randint(0, 59))
    shift_minutes = rng.randint(480, 600)
    t = shift_start
    end = shift_start + timedelta(minutes=shift_minutes)

    def emit(ts: datetime, lat: float, lon: float, speed_mph: float, engine_on: bool) -> None:
        nonlocal seq, odometer, engine_hours
        gps.append(
            {
                "id": f"gps-{int(vehicle['vehicleId'].split('-')[1]):03d}-{seq:07d}",
                "vehicleId": vehicle["vehicleId"],
                "operatorId": operator_id,
                "ts": ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "lat": round(lat, 6),
                "lon": round(lon, 6),
                "speedMph": round(speed_mph, 2),
                "heading": round(rng.uniform(0, 359), 1),
                "engineOn": engine_on,
                "odometerMi": round(odometer, 1),
                "engineHours": round(engine_hours, 2),
                "fuelLevelPct": round(max(5.0, 95 - (seq % 500) * 0.15), 1),
            }
        )
        seq += 1

    def add_geo(ts: datetime, geofence: dict[str, Any], event_type: str) -> None:
        lat, lon = _point_in(geofence, rng)
        geo.append(
            {
                "id": f"gf-{int(vehicle['vehicleId'].split('-')[1]):03d}-{len(geo):05d}",
                "vehicleId": vehicle["vehicleId"],
                "geofenceId": geofence["geofenceId"],
                "eventType": event_type,
                "ts": ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "lat": lat,
                "lon": lon,
            }
        )

    interval = rng.uniform(150, 210)

    # 1) Idle at the yard (engine on, speed 0).
    idle_min = rng.randint(5, 15)
    cur = t
    while cur < t + timedelta(minutes=idle_min):
        emit(cur, yard_lat, yard_lon, 0.0, True)
        engine_hours += interval / 3600
        cur += timedelta(seconds=interval)
    add_geo(cur, yard, "exit")
    t = cur

    # 2) Travel yard -> site.
    travel_min = rng.randint(20, 45)
    steps = max(3, int(travel_min * 60 / interval))
    for s in range(steps):
        frac = s / steps
        lat = _lerp(yard_lat, site_lat, frac) + rng.uniform(-0.002, 0.002)
        lon = _lerp(yard_lon, site_lon, frac) + rng.uniform(-0.002, 0.002)
        speed = rng.uniform(15, 40)
        ts = t + timedelta(seconds=interval * s)
        emit(ts, lat, lon, speed, True)
        odometer += speed / 60 * (interval / 60)
        engine_hours += interval / 3600
    t = t + timedelta(minutes=travel_min)
    add_geo(t, site, "entry")

    # 3) At the site: alternating work (slow moving) and idle blocks.
    while t < end - timedelta(minutes=30):
        if rng.random() < 0.55:
            block = rng.randint(15, 45)
            while block > 0:
                emit(t, site_lat + rng.uniform(-0.008, 0.008), site_lon + rng.uniform(-0.008, 0.008), rng.uniform(1.5, 6.5), True)
                engine_hours += interval / 3600
                t += timedelta(seconds=interval)
                block -= interval / 60
        else:
            block = rng.randint(6, 25)
            while block > 0:
                emit(t, site_lat + rng.uniform(-0.008, 0.008), site_lon + rng.uniform(-0.008, 0.008), 0.0, True)
                engine_hours += interval / 3600
                t += timedelta(seconds=interval)
                block -= interval / 60

    add_geo(t, site, "exit")

    # 4) Travel site -> yard.
    travel_min = rng.randint(20, 45)
    steps = max(3, int(travel_min * 60 / interval))
    for s in range(steps):
        frac = s / steps
        lat = _lerp(site_lat, yard_lat, frac) + rng.uniform(-0.002, 0.002)
        lon = _lerp(site_lon, yard_lon, frac) + rng.uniform(-0.002, 0.002)
        speed = rng.uniform(15, 40)
        ts = t + timedelta(seconds=interval * s)
        emit(ts, lat, lon, speed, True)
        odometer += speed / 60 * (interval / 60)
        engine_hours += interval / 3600
    t = t + timedelta(minutes=travel_min)
    add_geo(t, yard, "entry")

    # 5) Short idle then engine off.
    emit(t, yard_lat, yard_lon, 0.0, False)
    return gps, geo, seq, odometer, engine_hours


def _inject_defects(
    gps: list[dict[str, Any]],
    geo: list[dict[str, Any]],
    rng: random.Random,
) -> dict[str, int]:
    counts = {
        "duplicate_events": 0,
        "late_arriving": 0,
        "null_gps": 0,
        "null_island": 0,
        "out_of_range": 0,
        "unmatched_geofence_exits": 0,
        "stale_vehicles": 0,
    }
    now = max(
        datetime.fromisoformat(e["ts"].replace("Z", "+00:00")) for e in gps
    )

    # 1) Null / zero-island / out-of-range coordinates (~1.5% combined).
    n_coord = int(len(gps) * 0.015)
    for victim in rng.sample(gps, n_coord):
        roll = rng.random()
        if roll < 0.34:
            victim["lat"] = None
            victim["lon"] = None
            counts["null_gps"] += 1
        elif roll < 0.67:
            victim["lat"] = 0.0
            victim["lon"] = 0.0
            counts["null_island"] += 1
        else:
            victim["lat"] = round(rng.choice([-1, 1]) * rng.uniform(120, 400), 4)
            victim["lon"] = round(rng.choice([-1, 1]) * rng.uniform(200, 500), 4)
            counts["out_of_range"] += 1

    # 2) Late-arriving events (older than the lookback window, ~1%).
    n_late = int(len(gps) * 0.01)
    for src in rng.sample(gps, n_late):
        clone = dict(src)
        clone["id"] = src["id"] + "-late"
        when = now - timedelta(days=rng.randint(31, 40), hours=rng.randint(0, 23))
        clone["ts"] = when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        gps.append(clone)
        counts["late_arriving"] += 1

    # 3) Unmatched geofence exits (~0.5% of exits lose their entry).
    exits = [e for e in geo if e["eventType"] == "exit"]
    n_unmatched = max(1, int(len(exits) * 0.005))
    for ex in rng.sample(exits, n_unmatched):
        for other in geo:
            if (
                other["vehicleId"] == ex["vehicleId"]
                and other["geofenceId"] == ex["geofenceId"]
                and other["eventType"] == "entry"
                and other["ts"] < ex["ts"]
            ):
                geo.remove(other)
                counts["unmatched_geofence_exits"] += 1
                break

    # 4) Exact duplicate events (~2%).
    n_dupes = int(len(gps) * 0.02)
    for src in rng.sample(gps, n_dupes):
        gps.append(dict(src))
        counts["duplicate_events"] += 1

    # 5) A handful of vehicles with a stale last ping.
    vehicle_ids = sorted({e["vehicleId"] for e in gps})
    for vid in rng.sample(vehicle_ids, STALE_VEHICLES):
        cutoff = now - timedelta(hours=STALE_AGE_HOURS)
        stale_removed = [
            e
            for e in gps
            if e["vehicleId"] == vid and datetime.fromisoformat(e["ts"].replace("Z", "+00:00")) > cutoff
        ]
        for e in stale_removed:
            gps.remove(e)
        counts["stale_vehicles"] += 1

    return counts


def generate() -> dict[str, Any]:
    """Generate the full dataset and return it as a JSON-serialisable dict."""
    rng = random.Random(SEED)
    ref, vehicle_meta = build_reference(rng)
    now = datetime.now(timezone.utc)
    days = _workdays(now, rng)

    gps: list[dict[str, Any]] = []
    geo: list[dict[str, Any]] = []
    for idx, vehicle in enumerate(vehicle_meta):
        operator = ref.operators[idx % len(ref.operators)]
        odometer = rng.uniform(1000, 90000)
        engine_hours = rng.uniform(100, 9000)
        seq = 0
        for day in days:
            day_gps, day_geo, seq, odometer, engine_hours = _gen_vehicle_day(
                vehicle, operator["operatorId"], day, rng, seq, odometer, engine_hours
            )
            gps.extend(day_gps)
            geo.extend(day_geo)

    counts = _inject_defects(gps, geo, rng)

    gps.sort(key=lambda e: (e["ts"], e["id"]))
    geo.sort(key=lambda e: (e["ts"], e["id"]))

    dataset = {
        "generatedAt": now.isoformat(),
        "reference": {
            "vehicles": ref.vehicles,
            "operators": ref.operators,
            "geofences": ref.geofences,
        },
        "gps": gps,
        "geofenceEvents": geo,
        "injected": counts,
        "counts": {
            "gps_events_total": len(gps),
            "geofence_events_total": len(geo),
            "vehicles": len(ref.vehicles),
            "geofences": len(ref.geofences),
        },
    }
    return dataset


def write_cache(dataset: dict[str, Any]) -> None:
    """Persist the dataset and injected-defect ground truth."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(dataset))
    defects = {
        "seed": SEED,
        "generated_at": dataset["generatedAt"],
        "history_days": HISTORY_DAYS,
        "counts": dataset["counts"],
        "injected": dataset["injected"],
    }
    DEFECTS_FILE.write_text(json.dumps(defects, indent=2))
    (REPO_ROOT / "data").mkdir(parents=True, exist_ok=True)


def load_or_generate(force: bool = False) -> dict[str, Any]:
    """Load the cached dataset or generate + cache it."""
    if CACHE_FILE.exists() and not force:
        try:
            return json.loads(CACHE_FILE.read_text())
        except json.JSONDecodeError:
            pass
    dataset = generate()
    write_cache(dataset)
    return dataset


if __name__ == "__main__":
    data = load_or_generate(force=True)
    print(json.dumps(data["counts"], indent=2))
    print(json.dumps(data["injected"], indent=2))
