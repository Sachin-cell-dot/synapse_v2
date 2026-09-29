from __future__ import annotations

import csv
import hashlib
import json
import math
import sqlite3
import time
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, time as clock_time, timedelta, timezone
from math import fsum
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .blend import blend_forecasts
from ..utils.config import OperationalConfig
from .export import export_cycle
from ..preprocessing.geography import District, load_districts, sample_district
from ..utils.store import hierarchical_historical_errors, initialize_database, write_cycle


@dataclass(frozen=True)
class ArchivePoint:
    latitude: float
    longitude: float
    times: tuple[str, ...]
    values: dict[str, tuple[float | None, ...]]
    response_sha256: str
    retrieved_at_utc: str


def _request_archive(*, url: str, coordinates: tuple[tuple[float, float], ...], fields: tuple[str, ...], settings: dict, raw_directory: Path, cache_directory: Path) -> tuple[ArchivePoint, ...]:
    digest = hashlib.sha256(url.encode()).hexdigest()
    cache_directory.mkdir(parents=True, exist_ok=True)
    cache_path = cache_directory / f"{digest}.json"
    body = cache_path.read_bytes() if cache_path.exists() else None
    last_error: Exception | None = None
    for attempt in range(int(settings["request_attempts"])):
        if body is not None:
            break
        try:
            time.sleep(float(settings["request_interval_seconds"]))
            request = Request(url, headers={"User-Agent": settings["user_agent"]})
            with urlopen(request, timeout=float(settings["request_timeout_seconds"])) as response:
                body = response.read()
            cache_path.write_bytes(body)
        except Exception as error:
            last_error = error
            if attempt + 1 < int(settings["request_attempts"]):
                delay = float(settings["retry_backoff_seconds"]) * (2**attempt)
                time.sleep(min(delay, float(settings["maximum_retry_wait_seconds"])))
    if body is None:
        raise RuntimeError("Archived Open-Meteo request failed") from last_error
    body_sha = hashlib.sha256(body).hexdigest()
    retrieved_at_utc = datetime.now(timezone.utc).isoformat()
    raw_directory.mkdir(parents=True, exist_ok=True)
    raw_path = raw_directory / f"{body_sha}.json"
    if not raw_path.exists():
        raw_path.write_bytes(body)
    decoded = json.loads(body)
    payloads = decoded if isinstance(decoded, list) else [decoded]
    if len(payloads) != len(coordinates):
        raise ValueError("Archived Open-Meteo location count differs from request")
    points = []
    for (latitude, longitude), payload in zip(coordinates, payloads):
        hourly = payload.get("hourly")
        if not isinstance(hourly, dict) or not isinstance(hourly.get("time"), list):
            raise ValueError("Archived Open-Meteo response has no hourly series")
        times = tuple(str(item) for item in hourly["time"])
        values = {}
        for field in fields:
            series = hourly.get(field)
            if not isinstance(series, list) or len(series) != len(times):
                raise ValueError(f"Archived Open-Meteo response is missing {field}")
            values[field] = tuple(None if item is None else float(item) for item in series)
        points.append(ArchivePoint(latitude, longitude, times, values, body_sha, retrieved_at_utc))
    return tuple(points)


def _archive_url(endpoint: str, *, source: dict, coordinates: tuple[tuple[float, float], ...], fields: tuple[str, ...], timezone_name: str, start_date: date | None = None, end_date: date | None = None, run: datetime | None = None, forecast_days: int | None = None) -> str:
    params = {
        "latitude": ",".join(str(item[0]) for item in coordinates),
        "longitude": ",".join(str(item[1]) for item in coordinates),
        "hourly": ",".join(fields), "models": source["api_model"], "timezone": timezone_name,
    }
    if start_date is not None:
        params.update({"start_date": start_date.isoformat(), "end_date": (end_date or start_date).isoformat()})
    if run is not None:
        params["run"] = run.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M")
    if forecast_days is not None:
        params["forecast_days"] = str(forecast_days)
    return f"{endpoint}?{urlencode(params)}"


def _daily_value(point: ArchivePoint, field: str, target: date, *, timezone_name: str, accumulation_hour: int) -> float | None:
    zone = ZoneInfo(timezone_name)
    hourly = defaultdict(list)
    for timestamp, value in zip(point.times, point.values[field]):
        parsed = datetime.fromisoformat(timestamp)
        local = parsed.replace(tzinfo=zone) if parsed.tzinfo is None else parsed.astimezone(zone)
        if (local - timedelta(hours=accumulation_hour)).date() == target:
            hourly[local].append(value)
    expected = {datetime.combine(target, clock_time(hour=accumulation_hour), zone) + timedelta(hours=offset) for offset in range(24)}
    complete = set(hourly) == expected and all(len(values) == 1 and values[0] is not None for values in hourly.values())
    return fsum(values[0] for values in hourly.values()) if complete else None


def _district_average(points: tuple[ArchivePoint, ...], field: str, target: date, *, timezone_name: str, accumulation_hour: int, minimum_coverage: float) -> float | None:
    selected = []
    for point in points:
        value = _daily_value(point, field, target, timezone_name=timezone_name, accumulation_hour=accumulation_hour)
        if value is not None:
            selected.append((value, math.cos(math.radians(point.latitude))))
    coverage = len(selected) / len(points) if points else 0
    return fsum(value * weight for value, weight in selected) / fsum(weight for _, weight in selected) if selected and coverage >= minimum_coverage else None


def _districts_and_points(config: OperationalConfig) -> tuple[tuple[District, ...], dict[str, tuple[tuple[float, float], ...]]]:
    geography = config.data["geography"]
    districts = load_districts(config.resolve(geography["boundary_path"]), geography)
    return districts, {district.district_id: sample_district(district, geography["sampling"]) for district in districts}


def _batches(districts: tuple[District, ...], points: dict[str, tuple[tuple[float, float], ...]], size: int):
    flattened = [(district.district_id, point) for district in districts for point in points[district.district_id]]
    for offset in range(0, len(flattened), size):
        yield flattened[offset:offset + size]


def _history_rows(config: OperationalConfig) -> tuple[list[dict], list[date]]:
    settings = config.data["historical_bootstrap"]
    path = config.resolve(settings["path"])
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    dates = sorted({date.fromisoformat(row[settings["date_column"]]) for row in rows})
    selected_dates = dates[-int(config.data["blend"]["rolling_window_days"]):]
    return rows, selected_dates


def bootstrap_archived_skill(config: OperationalConfig) -> dict:
    archive = config.data["archive"]
    forecast = config.data["forecast"]
    rows, selected_dates = _history_rows(config)
    if not selected_dates:
        raise ValueError("Historical master has no dates for archive bootstrap")
    verification = {(str(row[config.data["historical_bootstrap"]["district_id_column"]]), date.fromisoformat(row[config.data["historical_bootstrap"]["date_column"]])): float(row[config.data["historical_bootstrap"]["verification_column"]]) for row in rows if date.fromisoformat(row[config.data["historical_bootstrap"]["date_column"]]) in set(selected_dates)}
    districts, district_points = _districts_and_points(config)
    leads = tuple(int(item) for item in forecast["lead_days"])
    base = forecast["hourly_variable"]
    fields = tuple(base if lead == 0 else f"{base}_previous_day{lead}" for lead in leads)
    aggregates: dict[tuple[str, str, int, date], tuple[float, str]] = {}
    for source in config.enabled_sources:
        by_district: dict[str, list[ArchivePoint]] = defaultdict(list)
        for batch in _batches(districts, district_points, int(forecast["coordinate_batch_size"])):
            coordinates = tuple(point for _, point in batch)
            url = _archive_url(archive["previous_runs_api_base_url"], source=source, coordinates=coordinates, fields=fields, timezone_name=forecast["source_timezone"], start_date=selected_dates[0], end_date=selected_dates[-1])
            results = _request_archive(url=url, coordinates=coordinates, fields=fields, settings=forecast, raw_directory=config.resolve(archive["raw_response_directory"]), cache_directory=config.resolve(archive["request_cache_directory"]))
            for (district_id, _), result in zip(batch, results):
                by_district[district_id].append(result)
        for district in districts:
            points = tuple(by_district[district.district_id])
            digest = hashlib.sha256("".join(sorted({point.response_sha256 for point in points})).encode()).hexdigest()
            for target in selected_dates:
                for lead, field in zip(leads, fields):
                    value = _district_average(points, field, target, timezone_name=forecast["source_timezone"], accumulation_hour=int(forecast["daily_accumulation_start_hour"]), minimum_coverage=float(config.data["geography"]["sampling"]["minimum_coverage_fraction"]))
                    if value is not None:
                        aggregates[(district.district_id, source["id"], lead, target)] = (value, digest)
    database = config.resolve(config.data["storage"]["database_path"])
    initialize_database(database)
    zone = ZoneInfo(forecast["source_timezone"])
    lag = timedelta(hours=float(config.data["historical_bootstrap"]["verification_availability_lag_hours"]))
    inserted = unchanged = 0
    with closing(sqlite3.connect(database)) as connection, connection:
        for (district_id, source_id, lead, target), (value, digest) in aggregates.items():
            actual = verification.get((district_id, target))
            if actual is None:
                continue
            start_local = datetime.combine(target, clock_time(hour=int(forecast["daily_accumulation_start_hour"])), zone)
            start, end = start_local.astimezone(timezone.utc).isoformat(), (start_local + timedelta(days=1)).astimezone(timezone.utc).isoformat()
            key = (district_id, source_id, lead, start, config.data["verification"]["provider"])
            if connection.execute("SELECT 1 FROM skill_observations WHERE district_id=? AND source_id=? AND lead_days=? AND valid_start_utc=? AND verification_provider=?", key).fetchone():
                unchanged += 1
                continue
            connection.execute("""INSERT INTO skill_observations(district_id,source_id,lead_days,valid_start_utc,valid_end_utc,forecast_mm,verification_mm,verification_provider,verification_classification,verification_available_at_utc,source_artifact_sha256,imported_at_utc) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (*key[:4], end, value, actual, key[4], archive["skill_classification"], (start_local + timedelta(days=1) + lag).astimezone(timezone.utc).isoformat(), digest, datetime.now(timezone.utc).isoformat()))
            inserted += 1
    return {"status": "pass", "dates": len(selected_dates), "leads": list(leads), "inserted": inserted, "unchanged": unchanged}


def _latest_run_before(valid_start_utc: datetime, run_hours: tuple[int, ...]) -> datetime:
    candidates = [valid_start_utc.replace(hour=hour, minute=0, second=0, microsecond=0) for hour in run_hours]
    eligible = [candidate for candidate in candidates if candidate <= valid_start_utc]
    return max(eligible) if eligible else (valid_start_utc - timedelta(days=1)).replace(hour=max(run_hours), minute=0, second=0, microsecond=0)


def _gap_dates(config: OperationalConfig) -> list[date]:
    _, historical_dates = _history_rows(config)
    if not historical_dates:
        return []
    database = config.resolve(config.data["storage"]["database_path"])
    initialize_database(database)
    live_mode = config.data["project"]["mode"]
    with closing(sqlite3.connect(database)) as connection:
        row = connection.execute("""SELECT MIN(b.valid_start_utc) FROM blended_forecasts b JOIN forecast_cycles c ON c.cycle_id=b.cycle_id WHERE c.mode=?""", (live_mode,)).fetchone()
    if not row or not row[0]:
        return []
    zone = ZoneInfo(config.data["forecast"]["source_timezone"])
    first_live = datetime.fromisoformat(row[0]).astimezone(zone).date()
    cursor = historical_dates[-1] + timedelta(days=1)
    result = []
    while cursor < first_live:
        result.append(cursor)
        cursor += timedelta(days=1)
    return result


def reconstruct_missing_cycles(config: OperationalConfig) -> dict:
    archive = config.data["archive"]
    forecast = config.data["forecast"]
    blend_settings = config.data["blend"]
    dates = _gap_dates(config)
    if not dates:
        return {"status": "pass", "dates": [], "cycles": [], "message": "No gap dates detected"}
    districts, district_points = _districts_and_points(config)
    zone = ZoneInfo(forecast["source_timezone"])
    accumulation_hour = int(forecast["daily_accumulation_start_hour"])
    run_hours = tuple(sorted(int(item) for item in archive["run_cycle_hours_utc"]))
    raw_directory = config.resolve(archive["raw_response_directory"])
    cache_directory = config.resolve(archive["request_cache_directory"])
    database = config.resolve(config.data["storage"]["database_path"])
    results = []
    for target in dates:
        valid_start_local = datetime.combine(target, clock_time(hour=accumulation_hour), zone)
        valid_start_utc = valid_start_local.astimezone(timezone.utc)
        valid_end_utc = valid_start_utc + timedelta(days=1)
        cycle_key = f"{archive['reconstruction_mode']}:{target.isoformat()}:{config.sha256}"
        cycle_id = hashlib.sha256(cycle_key.encode()).hexdigest()
        with closing(sqlite3.connect(database)) as connection:
            if connection.execute("SELECT 1 FROM forecast_cycles WHERE cycle_id=?", (cycle_id,)).fetchone():
                results.append({"date": target.isoformat(), "cycle_id": cycle_id, "status": "unchanged"})
                continue
        per_district: dict[str, dict[str, float | None]] = {district.district_id: {} for district in districts}
        source_rows = []
        source_runs = []
        for source in config.enabled_sources:
            run = _latest_run_before(valid_start_utc, run_hours)
            source_runs.append(run)
            by_district: dict[str, list[ArchivePoint]] = defaultdict(list)
            field = forecast["hourly_variable"]
            for batch in _batches(districts, district_points, int(forecast["coordinate_batch_size"])):
                coordinates = tuple(point for _, point in batch)
                url = _archive_url(archive["single_runs_api_base_url"], source=source, coordinates=coordinates, fields=(field,), timezone_name=forecast["source_timezone"], run=run, forecast_days=int(archive["single_run_forecast_days"]))
                points = _request_archive(url=url, coordinates=coordinates, fields=(field,), settings=forecast, raw_directory=raw_directory, cache_directory=cache_directory)
                for (district_id, _), point in zip(batch, points):
                    by_district[district_id].append(point)
            for district in districts:
                points = tuple(by_district[district.district_id])
                value = _district_average(points, field, target, timezone_name=forecast["source_timezone"], accumulation_hour=accumulation_hour, minimum_coverage=float(config.data["geography"]["sampling"]["minimum_coverage_fraction"]))
                digest = hashlib.sha256("".join(sorted({point.response_sha256 for point in points})).encode()).hexdigest()
                per_district[district.district_id][source["id"]] = value
                complete_points = sum(_daily_value(point, field, target, timezone_name=forecast["source_timezone"], accumulation_hour=accumulation_hour) is not None for point in points)
                required_points = math.ceil(float(config.data["geography"]["sampling"]["minimum_coverage_fraction"]) * len(points))
                source_rows.append({"cycle_id": cycle_id, "district_id": district.district_id, "source_id": source["id"], "requested_model_id": source["api_model"], "retrieved_at_utc": max(point.retrieved_at_utc for point in points), "issue_time_utc": (run + timedelta(hours=float(archive["run_availability_lag_hours"]))).isoformat(), "upstream_run_time_utc": run.isoformat(), "run_identity_status": archive["run_identity_status"], "valid_start_utc": valid_start_utc.isoformat(), "valid_end_utc": valid_end_utc.isoformat(), "lead_days": int(archive["reconstruction_lead_days"]), "unit": forecast["unit"], "precipitation_mm": value, "hourly_completeness": 1.0 if complete_points == len(points) else 0.0, "complete_point_count": complete_points, "required_point_count": required_points, "point_coverage": complete_points / len(points) if points else 0.0, "status": "complete" if value is not None else "incomplete", "raw_response_sha256": digest})
        issued_at = (max(source_runs) + timedelta(hours=float(archive["run_availability_lag_hours"]))).isoformat()
        blend_rows = []
        statewide_ids = tuple(district.district_id for district in districts)
        regional_ids = {district.district_id: tuple(peer.district_id for peer in districts if peer.division == district.division) for district in districts}
        with closing(sqlite3.connect(database)) as connection:
            for district in districts:
                forecasts = per_district[district.district_id]
                errors, hierarchy_fallback = hierarchical_historical_errors(connection, district_id=district.district_id, region_district_ids=regional_ids[district.district_id], statewide_district_ids=statewide_ids, lead_days=int(archive["reconstruction_lead_days"]), source_ids=tuple(forecasts), available_before_utc=issued_at, limit=int(blend_settings["rolling_window_days"]))
                blend = blend_forecasts(forecasts, errors, power=float(blend_settings["inverse_mae_power"]), mae_floor=float(blend_settings["mae_floor_mm"]), minimum_sources=int(blend_settings["minimum_sources_to_publish"]))
                blend_rows.append({"cycle_id": cycle_id, "district_id": district.district_id, "valid_start_utc": valid_start_utc.isoformat(), "valid_end_utc": valid_end_utc.isoformat(), "lead_days": int(archive["reconstruction_lead_days"]), "forecast_mm": blend.forecast_mm, "status": blend.status, "fallback": blend.fallback or hierarchy_fallback, "weights": blend.weights, "issued_at_utc": issued_at})
        cycle = {"cycle_id": cycle_id, "retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "configuration_sha256": config.sha256, "mode": archive["reconstruction_mode"], "created_at_utc": datetime.now(timezone.utc).isoformat()}
        write_cycle(path=database, cycle=cycle, source_rows=source_rows, blend_rows=blend_rows)
        exported = export_cycle(config, cycle_id)
        results.append({"date": target.isoformat(), "cycle_id": cycle_id, "status": "created", "export": exported["path"]})
    return {"status": "pass", "dates": [item.isoformat() for item in dates], "cycles": results}
