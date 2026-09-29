from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from ..evaluation.evaluate import evaluate_cycle
from ..evaluation.verification import import_verification
from ..inference.export import export_cycle
from ..inference.pipeline import run_statewide_cycle
from ..ingestion.imd_realtime import fetch_imd_district_rainfall
from ..ingestion.synop_operational import archive_station_forecasts
from ..models.xgboost_blend import load_frozen_model
from ..preprocessing.geography import load_districts, sample_district
from ..utils.config import OperationalConfig


def issue_slot(now_utc: datetime, timezone_name: str, issue_hours: list[int]) -> tuple[datetime, datetime, str]:
    if now_utc.tzinfo is None:
        raise ValueError("Routine time must be timezone-aware")
    zone = ZoneInfo(timezone_name)
    local = now_utc.astimezone(zone)
    candidates = [datetime.combine(local.date(), time(hour=hour), zone) for hour in sorted(issue_hours)]
    eligible = [candidate for candidate in candidates if candidate <= local]
    start_local = max(eligible) if eligible else datetime.combine(local.date() - timedelta(days=1), time(hour=max(issue_hours)), zone)
    later_today = [candidate for candidate in candidates if candidate > start_local]
    end_local = min(later_today) if later_today else datetime.combine(start_local.date() + timedelta(days=1), time(hour=min(issue_hours)), zone)
    slot_id = f"{start_local.date().isoformat()}T{start_local.hour:02d}:00[{timezone_name}]"
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc), slot_id


def _existing_statewide_cycle(config: OperationalConfig, slot_start: datetime, slot_end: datetime, expected_districts: int) -> str | None:
    path = config.resolve(config.data["storage"]["database_path"])
    if not path.exists():
        return None
    with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as connection:
        row = connection.execute(
            """SELECT c.cycle_id FROM forecast_cycles c JOIN blended_forecasts b ON b.cycle_id=c.cycle_id
               WHERE c.retrieved_at_utc>=? AND c.retrieved_at_utc<?
               GROUP BY c.cycle_id HAVING COUNT(DISTINCT b.district_id)=?
               ORDER BY c.retrieved_at_utc DESC LIMIT 1""",
            (slot_start.isoformat(), slot_end.isoformat(), expected_districts),
        ).fetchone()
    return None if row is None else str(row[0])


def _eligible_cycles(config: OperationalConfig, exclude_cycle_id: str) -> list[str]:
    path = config.resolve(config.data["storage"]["database_path"])
    provider = config.data["verification"]["provider"]
    if not path.exists():
        return []
    with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as connection:
        rows = connection.execute(
            """SELECT b.cycle_id FROM blended_forecasts b JOIN verification v
                 ON v.district_id=b.district_id AND v.valid_start_utc=b.valid_start_utc AND v.valid_end_utc=b.valid_end_utc AND v.provider=?
               WHERE b.cycle_id<>? GROUP BY b.cycle_id HAVING COUNT(*)>0 ORDER BY MIN(b.valid_start_utc)""",
            (provider, exclude_cycle_id),
        ).fetchall()
    return [str(row[0]) for row in rows]


def _write_health(config: OperationalConfig, event: dict) -> None:
    path = config.resolve(config.data["routine"]["health_log_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")


def run_routine(
    config: OperationalConfig,
    *,
    dry_run: bool = False,
    now_utc: datetime | None = None,
    run_forecast: Callable[[OperationalConfig], dict] = run_statewide_cycle,
    export_forecast: Callable[[OperationalConfig, str], dict] = export_cycle,
    fetch_imd: Callable[[OperationalConfig, date], dict] = fetch_imd_district_rainfall,
    import_imd: Callable[..., dict] = import_verification,
    evaluate: Callable[[OperationalConfig, str], dict] = evaluate_cycle,
    archive_stations: Callable[[OperationalConfig, str], dict] = archive_station_forecasts,
) -> dict:
    now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    routine = config.data["routine"]
    forecast = config.data["forecast"]
    xgb = config.data["xgboost"]
    loaded = load_frozen_model(config.resolve(xgb["artifact_directory"]), artifact_version=xgb["frozen_artifact_version"], feature_schema_sha256=xgb["frozen_feature_schema_sha256"])
    geography = config.data["geography"]
    districts = load_districts(config.resolve(geography["boundary_path"]), geography)
    expected = int(routine["expected_district_count"])
    if len(districts) != expected or len({district.district_id for district in districts}) != expected:
        raise ValueError(f"Routine requires {expected} unique districts; found {len(districts)}")
    sampling_points = sum(len(sample_district(district, geography["sampling"])) for district in districts)
    slot_start, slot_end, slot_id = issue_slot(now, forecast["source_timezone"], list(routine["issue_hours_ist"]))
    duplicate = _existing_statewide_cycle(config, slot_start, slot_end, expected)
    preflight = {
        "status": "pass", "dry_run": dry_run, "slot_id": slot_id,
        "slot_start_utc": slot_start.isoformat(), "slot_end_utc": slot_end.isoformat(),
        "districts": len(districts), "sampling_points": sampling_points,
        "artifact_version": loaded.artifact_version, "feature_schema_sha256": loaded.feature_schema_sha256,
        "duplicate_cycle_id": duplicate,
        "synop_station_count": len(config.data["synop_forecast_archive"]["stations"]) if config.data["synop_forecast_archive"]["enabled"] else 0,
    }
    if dry_run:
        return preflight
    base_event = {"timestamp_utc": now.isoformat(), "slot_id": slot_id, "configuration_sha256": config.sha256}
    if duplicate:
        event = {**base_event, "status": "skipped_duplicate", "cycle_id": duplicate}
        _write_health(config, event)
        return {**preflight, **event}
    try:
        cycle = run_forecast(config)
        cycle_id = str(cycle["cycle_id"])
        exported = export_forecast(config, cycle_id)
    except Exception as error:
        event = {**base_event, "status": "failed_before_or_during_issuance", "error_type": type(error).__name__, "error": str(error)}
        _write_health(config, event)
        raise RuntimeError(json.dumps(event, sort_keys=True)) from error
    try:
        synop_archive = archive_stations(config, cycle_id)
    except Exception as error:
        synop_archive = {"status": "failed", "error_type": type(error).__name__, "error": str(error)}
    target_date = now.astimezone(ZoneInfo(forecast["source_timezone"])).date() - timedelta(days=int(config.data["imd_realtime"]["latest_available_day_offset"]))
    imd_result: dict
    try:
        fetched = fetch_imd(config, target_date)
        imported = import_imd(config, Path(fetched["district_csv_path"]))
        imd_result = {"status": "ingested", "valid_date": target_date.isoformat(), "inserted": imported.get("inserted", 0), "unchanged": imported.get("unchanged", 0)}
    except (RuntimeError, ValueError, OSError) as error:
        imd_result = {"status": "unavailable", "valid_date": target_date.isoformat(), "error_type": type(error).__name__, "error": str(error)}
    evaluations = []
    for eligible in _eligible_cycles(config, cycle_id):
        report_path = config.resolve(config.data["storage"]["export_directory"]) / f"synapse_wx_evaluation_{eligible}.json"
        if report_path.exists():
            evaluations.append({"cycle_id": eligible, "status": "already_present"})
        else:
            result = evaluate(config, eligible)
            evaluations.append({"cycle_id": eligible, "status": result["status"], "verified_rows": result["verified_rows"]})
    event = {**base_event, "status": "pass", "cycle_id": cycle_id, "districts": cycle["districts"], "blend_rows": cycle["blend_rows"], "export_rows": exported["rows"], "synop_archive": synop_archive, "imd": imd_result, "evaluations": evaluations}
    _write_health(config, event)
    return {**preflight, **event, "export_path": exported["path"]}
