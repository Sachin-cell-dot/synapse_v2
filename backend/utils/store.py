from __future__ import annotations

import sqlite3
import json
from collections.abc import Mapping, Sequence
from contextlib import closing
from pathlib import Path


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS forecast_cycles (
    cycle_id TEXT PRIMARY KEY,
    retrieved_at_utc TEXT NOT NULL,
    configuration_sha256 TEXT NOT NULL,
    mode TEXT NOT NULL,
    created_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_forecasts (
    cycle_id TEXT NOT NULL REFERENCES forecast_cycles(cycle_id),
    district_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_class TEXT NOT NULL,
    blend_role TEXT NOT NULL,
    requested_model_id TEXT NOT NULL,
    retrieved_at_utc TEXT NOT NULL,
    issue_time_utc TEXT NOT NULL,
    upstream_run_time_utc TEXT,
    run_identity_status TEXT NOT NULL,
    valid_start_utc TEXT NOT NULL,
    valid_end_utc TEXT NOT NULL,
    lead_days INTEGER NOT NULL,
    unit TEXT NOT NULL,
    precipitation_mm REAL,
    ensemble_spread_mm REAL,
    spread_semantics TEXT,
    hourly_completeness REAL NOT NULL,
    complete_point_count INTEGER NOT NULL,
    required_point_count INTEGER NOT NULL,
    point_coverage REAL NOT NULL,
    status TEXT NOT NULL,
    raw_response_sha256 TEXT NOT NULL,
    PRIMARY KEY (cycle_id, district_id, source_id, valid_start_utc, valid_end_utc)
);
CREATE TABLE IF NOT EXISTS blended_forecasts (
    cycle_id TEXT NOT NULL REFERENCES forecast_cycles(cycle_id),
    district_id TEXT NOT NULL,
    valid_start_utc TEXT NOT NULL,
    valid_end_utc TEXT NOT NULL,
    lead_days INTEGER NOT NULL,
    forecast_mm REAL,
    status TEXT NOT NULL,
    fallback TEXT,
    method TEXT NOT NULL,
    predicted_errors_json TEXT NOT NULL,
    artifact_version TEXT,
    feature_schema_sha256 TEXT,
    weights_json TEXT NOT NULL,
    issued_at_utc TEXT NOT NULL,
    PRIMARY KEY (cycle_id, district_id, valid_start_utc, valid_end_utc)
);
CREATE TABLE IF NOT EXISTS source_variable_forecasts (
    cycle_id TEXT NOT NULL REFERENCES forecast_cycles(cycle_id),
    district_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    requested_model_id TEXT NOT NULL,
    variable_id TEXT NOT NULL,
    statistic TEXT NOT NULL,
    retrieved_at_utc TEXT NOT NULL,
    issue_time_utc TEXT NOT NULL,
    upstream_run_time_utc TEXT,
    run_identity_status TEXT NOT NULL,
    valid_start_utc TEXT NOT NULL,
    valid_end_utc TEXT NOT NULL,
    lead_days INTEGER NOT NULL,
    unit TEXT NOT NULL,
    value REAL,
    hourly_completeness REAL NOT NULL,
    complete_point_count INTEGER NOT NULL,
    required_point_count INTEGER NOT NULL,
    point_coverage REAL NOT NULL,
    status TEXT NOT NULL,
    raw_response_sha256 TEXT NOT NULL,
    PRIMARY KEY (cycle_id,district_id,source_id,variable_id,statistic,valid_start_utc,valid_end_utc)
);
CREATE TABLE IF NOT EXISTS variable_blended_forecasts (
    cycle_id TEXT NOT NULL REFERENCES forecast_cycles(cycle_id),
    district_id TEXT NOT NULL,
    valid_start_utc TEXT NOT NULL,
    valid_end_utc TEXT NOT NULL,
    lead_days INTEGER NOT NULL,
    variable_id TEXT NOT NULL,
    statistic TEXT NOT NULL,
    unit TEXT NOT NULL,
    value REAL,
    status TEXT NOT NULL,
    method TEXT NOT NULL,
    blend_eligibility TEXT NOT NULL,
    fallback TEXT,
    weights_json TEXT NOT NULL,
    issued_at_utc TEXT NOT NULL,
    PRIMARY KEY (cycle_id,district_id,variable_id,statistic,valid_start_utc,valid_end_utc)
);
CREATE TABLE IF NOT EXISTS verification (
    district_id TEXT NOT NULL,
    valid_start_utc TEXT NOT NULL,
    valid_end_utc TEXT NOT NULL,
    value_mm REAL NOT NULL,
    provider TEXT NOT NULL,
    classification TEXT NOT NULL,
    available_at_utc TEXT NOT NULL,
    raw_response_sha256 TEXT NOT NULL,
    PRIMARY KEY (district_id, valid_start_utc, valid_end_utc, provider)
);
CREATE TABLE IF NOT EXISTS skill_observations (
    district_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    lead_days INTEGER NOT NULL,
    valid_start_utc TEXT NOT NULL,
    valid_end_utc TEXT NOT NULL,
    forecast_mm REAL NOT NULL,
    verification_mm REAL NOT NULL,
    verification_provider TEXT NOT NULL,
    verification_classification TEXT NOT NULL,
    verification_available_at_utc TEXT NOT NULL,
    source_artifact_sha256 TEXT NOT NULL,
    imported_at_utc TEXT NOT NULL,
    PRIMARY KEY (district_id, source_id, lead_days, valid_start_utc, verification_provider)
);
CREATE TABLE IF NOT EXISTS synop_stations (
    station_id TEXT PRIMARY KEY,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    first_observation_utc TEXT NOT NULL,
    last_observation_utc TEXT NOT NULL,
    observation_count INTEGER NOT NULL,
    provenance_sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS synop_forecast_pairs (
    station_id TEXT NOT NULL REFERENCES synop_stations(station_id),
    observation_time_utc TEXT NOT NULL,
    variable_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    requested_model_id TEXT,
    issue_time_utc TEXT,
    issue_time_status TEXT NOT NULL,
    valid_time_utc TEXT,
    lead_hours REAL,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    observation_value REAL,
    forecast_value REAL,
    unit TEXT NOT NULL,
    match_status TEXT NOT NULL,
    spatial_distance_km REAL,
    requested_latitude REAL,
    requested_longitude REAL,
    returned_grid_latitude REAL,
    returned_grid_longitude REAL,
    request_url_sha256 TEXT,
    observation_provenance_sha256 TEXT NOT NULL,
    forecast_provenance_sha256 TEXT,
    created_at_utc TEXT NOT NULL,
    PRIMARY KEY (station_id,observation_time_utc,variable_id,source_id)
);
CREATE TABLE IF NOT EXISTS synop_station_forecast_requests (
    cycle_id TEXT NOT NULL REFERENCES forecast_cycles(cycle_id),
    station_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    requested_model_id TEXT NOT NULL,
    request_time_utc TEXT NOT NULL,
    requested_latitude REAL NOT NULL,
    requested_longitude REAL NOT NULL,
    returned_grid_latitude REAL,
    returned_grid_longitude REAL,
    grid_distance_km REAL,
    response_sha256 TEXT,
    expected_hour_count INTEGER NOT NULL,
    temperature_complete_fraction REAL NOT NULL,
    wind_complete_fraction REAL NOT NULL,
    status TEXT NOT NULL,
    error_type TEXT,
    error_message TEXT,
    PRIMARY KEY (cycle_id, station_id, source_id)
);
CREATE TABLE IF NOT EXISTS synop_station_forecast_values (
    cycle_id TEXT NOT NULL REFERENCES forecast_cycles(cycle_id),
    station_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    variable_id TEXT NOT NULL,
    valid_time_utc TEXT NOT NULL,
    unit TEXT NOT NULL,
    value REAL,
    status TEXT NOT NULL,
    response_sha256 TEXT,
    request_time_utc TEXT NOT NULL,
    PRIMARY KEY (cycle_id, station_id, source_id, variable_id, valid_time_utc),
    FOREIGN KEY (cycle_id, station_id, source_id)
      REFERENCES synop_station_forecast_requests(cycle_id, station_id, source_id)
);
"""


def initialize_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript(SCHEMA)
        existing = {row[1] for row in connection.execute("PRAGMA table_info(source_forecasts)")}
        migrations = {
            "retrieved_at_utc": "TEXT", "issue_time_utc": "TEXT", "unit": "TEXT",
            "hourly_completeness": "REAL", "complete_point_count": "INTEGER",
            "required_point_count": "INTEGER", "point_coverage": "REAL", "status": "TEXT",
            "source_class": "TEXT", "blend_role": "TEXT", "ensemble_spread_mm": "REAL", "spread_semantics": "TEXT",
        }
        for column, column_type in migrations.items():
            if column not in existing:
                connection.execute(f"ALTER TABLE source_forecasts ADD COLUMN {column} {column_type}")
        blend_existing = {row[1] for row in connection.execute("PRAGMA table_info(blended_forecasts)")}
        blend_migrations = {"method": "TEXT", "predicted_errors_json": "TEXT", "artifact_version": "TEXT", "feature_schema_sha256": "TEXT"}
        for column, column_type in blend_migrations.items():
            if column not in blend_existing:
                connection.execute(f"ALTER TABLE blended_forecasts ADD COLUMN {column} {column_type}")
        synop_pair_existing = {row[1] for row in connection.execute("PRAGMA table_info(synop_forecast_pairs)")}
        synop_pair_migrations = {
            "issue_time_status": "TEXT NOT NULL DEFAULT 'not_recorded'", "requested_latitude": "REAL",
            "requested_longitude": "REAL", "returned_grid_latitude": "REAL", "returned_grid_longitude": "REAL",
            "request_url_sha256": "TEXT",
        }
        for column, definition in synop_pair_migrations.items():
            if column not in synop_pair_existing:
                connection.execute(f"ALTER TABLE synop_forecast_pairs ADD COLUMN {column} {definition}")


def historical_errors(connection: sqlite3.Connection, *, district_id: str, lead_days: int, source_ids: Sequence[str], available_before_utc: str, limit: int) -> dict[str, list[float]]:
    result = {}
    for source_id in source_ids:
        rows = connection.execute(
            """
            SELECT absolute_error FROM (
                SELECT sf.valid_start_utc AS valid_start, ABS(sf.precipitation_mm - v.value_mm) AS absolute_error
                FROM source_forecasts sf
                JOIN verification v
                  ON v.district_id = sf.district_id
                 AND v.valid_start_utc = sf.valid_start_utc
                 AND v.valid_end_utc = sf.valid_end_utc
                WHERE sf.district_id = ? AND sf.source_id = ? AND sf.lead_days = ?
                  AND sf.precipitation_mm IS NOT NULL AND v.available_at_utc < ?
                UNION ALL
                SELECT valid_start_utc AS valid_start, ABS(forecast_mm - verification_mm) AS absolute_error
                FROM skill_observations
                WHERE district_id = ? AND source_id = ? AND lead_days = ?
                  AND verification_available_at_utc < ?
            )
            ORDER BY valid_start DESC
            LIMIT ?
            """,
            (district_id, source_id, lead_days, available_before_utc, district_id, source_id, lead_days, available_before_utc, limit),
        ).fetchall()
        result[source_id] = [float(row[0]) for row in reversed(rows)]
    return result


def hierarchical_historical_errors(connection: sqlite3.Connection, *, district_id: str, region_district_ids: Sequence[str], statewide_district_ids: Sequence[str], lead_days: int, source_ids: Sequence[str], available_before_utc: str, limit: int) -> tuple[dict[str, list[float]], str | None]:
    scopes = (
        ("district", (district_id,)),
        ("regional", tuple(dict.fromkeys(region_district_ids))),
        ("statewide", tuple(dict.fromkeys(statewide_district_ids))),
    )
    last = {source_id: [] for source_id in source_ids}
    for level, district_ids in scopes:
        if not district_ids:
            continue
        placeholders = ",".join("?" for _ in district_ids)
        result = {}
        for source_id in source_ids:
            query = f"""
                SELECT absolute_error FROM (
                    SELECT sf.valid_start_utc AS valid_start, ABS(sf.precipitation_mm-v.value_mm) AS absolute_error
                    FROM source_forecasts sf JOIN verification v
                      ON v.district_id=sf.district_id AND v.valid_start_utc=sf.valid_start_utc AND v.valid_end_utc=sf.valid_end_utc
                    WHERE sf.district_id IN ({placeholders}) AND sf.source_id=? AND sf.lead_days=?
                      AND sf.precipitation_mm IS NOT NULL AND v.available_at_utc < ?
                    UNION ALL
                    SELECT valid_start_utc, ABS(forecast_mm-verification_mm)
                    FROM skill_observations
                    WHERE district_id IN ({placeholders}) AND source_id=? AND lead_days=?
                      AND verification_available_at_utc < ?
                ) ORDER BY valid_start DESC LIMIT ?
            """
            params = (*district_ids, source_id, lead_days, available_before_utc, *district_ids, source_id, lead_days, available_before_utc, limit * len(district_ids))
            result[source_id] = [float(row[0]) for row in reversed(connection.execute(query, params).fetchall())]
        last = result
        if all(result.get(source_id) for source_id in source_ids):
            return result, None if level == "district" else f"{level}_lead_skill_fallback"
    return last, None


def write_cycle(*, path: Path, cycle: Mapping, source_rows: Sequence[Mapping], blend_rows: Sequence[Mapping], source_variable_rows: Sequence[Mapping] = (), variable_blend_rows: Sequence[Mapping] = ()) -> None:
    initialize_database(path)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "INSERT INTO forecast_cycles(cycle_id,retrieved_at_utc,configuration_sha256,mode,created_at_utc) VALUES(?,?,?,?,?)",
            (cycle["cycle_id"], cycle["retrieved_at_utc"], cycle["configuration_sha256"], cycle["mode"], cycle["created_at_utc"]),
        )
        connection.executemany(
            """INSERT INTO source_forecasts(cycle_id,district_id,source_id,source_class,blend_role,requested_model_id,retrieved_at_utc,issue_time_utc,upstream_run_time_utc,run_identity_status,valid_start_utc,valid_end_utc,lead_days,unit,precipitation_mm,ensemble_spread_mm,spread_semantics,hourly_completeness,complete_point_count,required_point_count,point_coverage,status,raw_response_sha256)
               VALUES(:cycle_id,:district_id,:source_id,:source_class,:blend_role,:requested_model_id,:retrieved_at_utc,:issue_time_utc,:upstream_run_time_utc,:run_identity_status,:valid_start_utc,:valid_end_utc,:lead_days,:unit,:precipitation_mm,:ensemble_spread_mm,:spread_semantics,:hourly_completeness,:complete_point_count,:required_point_count,:point_coverage,:status,:raw_response_sha256)""",
            [{**row, "source_class": row.get("source_class", "unspecified"), "blend_role": row.get("blend_role", "locked_blend_source"), "ensemble_spread_mm": row.get("ensemble_spread_mm"), "spread_semantics": row.get("spread_semantics")} for row in source_rows],
        )
        connection.executemany(
            """INSERT INTO blended_forecasts(cycle_id,district_id,valid_start_utc,valid_end_utc,lead_days,forecast_mm,status,fallback,method,predicted_errors_json,artifact_version,feature_schema_sha256,weights_json,issued_at_utc)
               VALUES(:cycle_id,:district_id,:valid_start_utc,:valid_end_utc,:lead_days,:forecast_mm,:status,:fallback,:method,:predicted_errors_json,:artifact_version,:feature_schema_sha256,:weights_json,:issued_at_utc)""",
            [{**row, "method": row.get("method", "inverse_mae_baseline"), "predicted_errors_json": json.dumps(row.get("predicted_errors", {}), sort_keys=True, separators=(",", ":")), "artifact_version": row.get("artifact_version"), "feature_schema_sha256": row.get("feature_schema_sha256"), "weights_json": json.dumps(row["weights"], sort_keys=True, separators=(",", ":"))} for row in blend_rows],
        )
        connection.executemany(
            """INSERT INTO source_variable_forecasts(cycle_id,district_id,source_id,requested_model_id,variable_id,statistic,retrieved_at_utc,issue_time_utc,upstream_run_time_utc,run_identity_status,valid_start_utc,valid_end_utc,lead_days,unit,value,hourly_completeness,complete_point_count,required_point_count,point_coverage,status,raw_response_sha256)
               VALUES(:cycle_id,:district_id,:source_id,:requested_model_id,:variable_id,:statistic,:retrieved_at_utc,:issue_time_utc,:upstream_run_time_utc,:run_identity_status,:valid_start_utc,:valid_end_utc,:lead_days,:unit,:value,:hourly_completeness,:complete_point_count,:required_point_count,:point_coverage,:status,:raw_response_sha256)""",
            source_variable_rows,
        )
        connection.executemany(
            """INSERT INTO variable_blended_forecasts(cycle_id,district_id,valid_start_utc,valid_end_utc,lead_days,variable_id,statistic,unit,value,status,method,blend_eligibility,fallback,weights_json,issued_at_utc)
               VALUES(:cycle_id,:district_id,:valid_start_utc,:valid_end_utc,:lead_days,:variable_id,:statistic,:unit,:value,:status,:method,:blend_eligibility,:fallback,:weights_json,:issued_at_utc)""",
            [{**row, "weights_json": json.dumps(row["weights"], sort_keys=True, separators=(",", ":"))} for row in variable_blend_rows],
        )
