from __future__ import annotations

import csv
import hashlib
import json
import math
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from ..utils.config import OperationalConfig
from ..utils.store import initialize_database

SOURCES = ("gfs", "ifs_hres", "aifs")
VARIABLES = {"temperature_2m": ("air_temperature_c", "°C"), "wind_speed_10m": ("wind_speed_ms", "m/s")}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def normalize_value(variable: str, value: str, unit: str) -> float:
    number = float(value)
    normalized = unit.strip().lower().replace(" ", "")
    if variable == "temperature_2m":
        if normalized in {"°c", "c", "celsius"}: result = number
        elif normalized in {"k", "kelvin"}: result = number - 273.15
        else: raise ValueError(f"unsupported temperature unit: {unit}")
        if not -80 <= result <= 60: raise ValueError("temperature outside quality range")
        return result
    if normalized in {"m/s", "ms", "mps"}: result = number
    elif normalized in {"km/h", "kmh"}: result = number / 3.6
    elif normalized in {"kn", "kt", "knot", "knots"}: result = number * 0.514444
    else: raise ValueError(f"unsupported wind-speed unit: {unit}")
    if not 0 <= result <= 100: raise ValueError("wind speed outside quality range")
    return result


def _distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    x = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 6371.0088 * 2 * math.asin(math.sqrt(x))


def ingest_and_verify_synop(config: OperationalConfig, observations_path: Path, forecasts_path: Path | None = None, *, start: str | None = None, end: str | None = None, station_id: str | None = None) -> dict:
    observation_hash = _sha256(observations_path)
    with observations_path.open(encoding="utf-8", newline="") as handle:
        raw_observations = list(csv.DictReader(handle))
    observations, rejected = [], []
    for row in raw_observations:
        try:
            row_station_id = row["station_id"].strip(); when = _utc(row["observation_time_utc"])
            if station_id and station_id != row_station_id: continue
            day = when.date().isoformat()
            if start and day < start or end and day > end: continue
            latitude, longitude = float(row["latitude"]), float(row["longitude"])
            if not row_station_id or not (-90 <= latitude <= 90 and -180 <= longitude <= 180): raise ValueError("invalid station identity or coordinate")
            for variable, (column, unit) in VARIABLES.items():
                value = row.get(column, "").strip()
                observations.append({"station_id": row_station_id, "time": when, "latitude": latitude, "longitude": longitude,
                                     "variable_id": variable, "value": None if not value else normalize_value(variable, value, unit), "unit": unit})
        except (KeyError, ValueError) as error:
            rejected.append({"station_id": row.get("station_id"), "time": row.get("observation_time_utc"), "reason": str(error)})

    forecast_hash = _sha256(forecasts_path) if forecasts_path else None
    forecasts: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    if forecasts_path:
        with forecasts_path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                variable, source = row["variable_id"], row["source_id"]
                if variable not in VARIABLES or source not in SOURCES: continue
                valid = _utc(row["valid_time_utc"])
                issue = _utc(row["issue_time_utc"]) if row.get("issue_time_utc", "").strip() else None
                issue_status = row.get("issue_time_status", "verified_exact")
                if issue and issue > valid: continue
                if issue is None and issue_status != "unverified_exact_previous_day1_product": continue
                forecasts[(source, variable, valid.isoformat())].append({**row, "valid": valid, "issue": issue, "issue_status": issue_status,
                    "requested_latitude": float(row.get("requested_latitude") or row["latitude"]), "requested_longitude": float(row.get("requested_longitude") or row["longitude"]),
                    "returned_latitude": float(row.get("returned_grid_latitude") or row["latitude"]), "returned_longitude": float(row.get("returned_grid_longitude") or row["longitude"]),
                    "value": normalize_value(variable, row["value"], row["unit"]) if row.get("value", "").strip() else None})

    now = datetime.now(timezone.utc).isoformat()
    pairs, station_times = [], defaultdict(list)
    for obs in observations:
        station_times[obs["station_id"]].append(obs["time"])
        for source in SOURCES:
            candidates = forecasts.get((source, obs["variable_id"], obs["time"].isoformat()), [])
            station_requested = [item for item in candidates if _distance_km((obs["latitude"], obs["longitude"]), (item["requested_latitude"], item["requested_longitude"])) <= .05]
            chosen = sorted(station_requested, key=lambda item: item["issue"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[0] if station_requested else None
            status = ("matched_issue_time_unverified" if chosen and chosen["issue"] is None else "matched") if chosen and chosen["value"] is not None and obs["value"] is not None else "missing_observation" if obs["value"] is None else "missing_forecast" if chosen else "no_station_requested_forecast"
            distance = _distance_km((obs["latitude"], obs["longitude"]), (chosen["returned_latitude"], chosen["returned_longitude"])) if chosen else None
            pairs.append({"station_id": obs["station_id"], "observation_time_utc": obs["time"].isoformat().replace("+00:00", "Z"),
                "variable_id": obs["variable_id"], "source_id": source, "requested_model_id": chosen.get("requested_model_id") if chosen else None,
                "issue_time_utc": chosen["issue"].isoformat().replace("+00:00", "Z") if chosen and chosen["issue"] else None,
                "issue_time_status": chosen["issue_status"] if chosen else "unavailable",
                "valid_time_utc": chosen["valid"].isoformat().replace("+00:00", "Z") if chosen else None,
                "lead_hours": (chosen["valid"]-chosen["issue"]).total_seconds()/3600 if chosen and chosen["issue"] else 24.0 if chosen else None,
                "latitude": obs["latitude"], "longitude": obs["longitude"], "observation_value": obs["value"],
                "forecast_value": chosen["value"] if chosen else None, "unit": obs["unit"], "match_status": status,
                "spatial_distance_km": distance,
                "requested_latitude": chosen["requested_latitude"] if chosen else None, "requested_longitude": chosen["requested_longitude"] if chosen else None,
                "returned_grid_latitude": chosen["returned_latitude"] if chosen else None, "returned_grid_longitude": chosen["returned_longitude"] if chosen else None,
                "request_url_sha256": chosen.get("request_url_sha256") if chosen else None,
                "observation_provenance_sha256": observation_hash,
                "forecast_provenance_sha256": forecast_hash, "created_at_utc": now})

    database = config.resolve(config.data["storage"]["database_path"]); initialize_database(database)
    stations = {}
    for obs in observations:
        stations[obs["station_id"]] = (obs["latitude"], obs["longitude"])
    with closing(sqlite3.connect(database)) as connection, connection:
        for station_id, (lat, lon) in stations.items():
            times = station_times[station_id]
            connection.execute("INSERT OR REPLACE INTO synop_stations VALUES(?,?,?,?,?,?,?)", (station_id, lat, lon, min(times).isoformat(), max(times).isoformat(), len(set(times)), observation_hash))
        columns = list(pairs[0]) if pairs else []
        if columns:
            connection.executemany(f"INSERT OR REPLACE INTO synop_forecast_pairs({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", [tuple(row[column] for column in columns) for row in pairs])

    matched = [row for row in pairs if row["match_status"].startswith("matched")]
    groups = defaultdict(list)
    for row in matched:
        groups[("full_availability", row["station_id"], row["variable_id"], row["source_id"], row["lead_hours"], row["issue_time_status"])].append(row)
    matched_sources = defaultdict(set)
    for row in matched:
        matched_sources[(row["station_id"], row["variable_id"], row["observation_time_utc"])].add(row["source_id"])
    common_keys = {key for key, sources in matched_sources.items() if sources == set(SOURCES)}
    for row in matched:
        if (row["station_id"], row["variable_id"], row["observation_time_utc"]) in common_keys:
            groups[("common_three_source", row["station_id"], row["variable_id"], row["source_id"], row["lead_hours"], row["issue_time_status"])].append(row)
    metrics = []
    for (scope, station, variable, source, lead, issue_status), rows in sorted(groups.items()):
        errors = [row["forecast_value"]-row["observation_value"] for row in rows]
        metrics.append({"scope":scope, "station_id": station, "variable_id": variable, "source_id": source, "lead_hours": lead, "n": len(errors),
            "days":len({row["observation_time_utc"][:10] for row in rows}), "issue_time_status": issue_status,
            "measurement_height_status":"not_applicable" if variable == "temperature_2m" else "synop_height_unverified_vs_forecast_10m",
            "mae": sum(abs(x) for x in errors)/len(errors), "rmse": math.sqrt(sum(x*x for x in errors)/len(errors)), "bias": sum(errors)/len(errors)})

    export_dir = config.resolve(config.data["storage"]["export_directory"]) / "synop"; export_dir.mkdir(parents=True, exist_ok=True)
    def write(name: str, rows: list[dict], fields: list[str]):
        path = export_dir / name
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer=csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
        return str(path)
    station_rows = [{"station_id": sid, "latitude": value[0], "longitude": value[1], "observation_count": len(set(station_times[sid])), "first_observation_utc": min(station_times[sid]).isoformat(), "last_observation_utc": max(station_times[sid]).isoformat(), "label": "SYNOP point observation"} for sid,value in sorted(stations.items())]
    station_path = write("synop_stations.csv", station_rows, list(station_rows[0]) if station_rows else ["station_id"])
    pair_fields = list(pairs[0]) if pairs else ["station_id","observation_time_utc","variable_id","source_id","match_status"]
    pair_path = write("synop_forecast_pairs.csv", pairs, pair_fields)
    metric_fields = ["scope","station_id","variable_id","source_id","lead_hours","n","days","issue_time_status","measurement_height_status","mae","rmse","bias"]
    metric_path = write("synop_station_metrics.csv", metrics, metric_fields)
    summary = {"status":"pass", "classification":"SYNOP point observations", "stations":len(stations), "observation_variable_rows":len(observations),
        "pair_rows":len(pairs), "matched_pairs":len(matched), "unmatched_pairs":len(pairs)-len(matched), "rejected_rows":len(rejected),
        "metrics":len(metrics), "observation_sha256":observation_hash, "forecast_sha256":forecast_hash,
        "spatial_rule":"forecast coordinate must be within 0.05 km; district forecasts are never substituted", "outputs":{"stations":station_path,"pairs":pair_path,"metrics":metric_path}}
    (export_dir/"synop_verification_summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    return summary
