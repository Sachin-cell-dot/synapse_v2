from __future__ import annotations

import math
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from .open_meteo import PointForecast, fetch_point
from ..utils.config import OperationalConfig
from ..utils.store import initialize_database


VARIABLE_FIELDS = {
    "temperature_2m": "temperature_2m",
    "wind_speed_10m": "wind_speed_10m",
}


def _utc(value: str, source_timezone: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(source_timezone))
    return parsed.astimezone(timezone.utc).isoformat()


def _distance_km(latitude_a: float, longitude_a: float, latitude_b: float, longitude_b: float) -> float:
    radius_km = 6371.0088
    lat_a, lat_b = math.radians(latitude_a), math.radians(latitude_b)
    delta_lat = lat_b - lat_a
    delta_lon = math.radians(longitude_b - longitude_a)
    haversine = math.sin(delta_lat / 2) ** 2 + math.cos(lat_a) * math.cos(lat_b) * math.sin(delta_lon / 2) ** 2
    return 2 * radius_km * math.asin(math.sqrt(haversine))


def _already_archived(connection: sqlite3.Connection, cycle_id: str, station_id: str, source_id: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM synop_station_forecast_requests WHERE cycle_id=? AND station_id=? AND source_id=?",
        (cycle_id, station_id, source_id),
    ).fetchone() is not None


def archive_station_forecasts(
    config: OperationalConfig,
    cycle_id: str,
    *,
    fetcher: Callable[..., PointForecast] = fetch_point,
) -> dict:
    settings = config.data["synop_forecast_archive"]
    if not settings["enabled"]:
        return {"status": "disabled", "stations": 0, "complete": 0, "incomplete": 0, "failed": 0, "skipped": 0}

    database = config.resolve(config.data["storage"]["database_path"])
    initialize_database(database)
    forecast = config.data["forecast"]
    raw_directory = config.resolve(config.data["storage"]["raw_response_directory"]) / "synop-stations"
    cache_directory = config.resolve(forecast["request_cache_directory"]) / "synop-stations"
    counts = {"complete": 0, "incomplete": 0, "failed": 0, "skipped": 0}

    for station in settings["stations"]:
        station_id = str(station["station_id"])
        requested_latitude = float(station["latitude"])
        requested_longitude = float(station["longitude"])
        for source in config.enabled_sources:
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("PRAGMA foreign_keys=ON")
                if _already_archived(connection, cycle_id, station_id, source["id"]):
                    counts["skipped"] += 1
                    continue
            request_time = datetime.now(timezone.utc).isoformat()
            try:
                point = fetcher(
                    source=source,
                    latitude=requested_latitude,
                    longitude=requested_longitude,
                    forecast=forecast,
                    raw_directory=raw_directory,
                    cache_directory=cache_directory,
                )
                request_time = point.requested_at_utc or point.retrieved_at_utc
                returned_latitude = point.returned_latitude
                returned_longitude = point.returned_longitude
                distance = None if returned_latitude is None or returned_longitude is None else _distance_km(
                    requested_latitude, requested_longitude, returned_latitude, returned_longitude
                )
                expected = len(point.times)
                distinct_times = len(set(point.times)) == expected
                values_by_variable = {variable: tuple(getattr(point, field)) for variable, field in VARIABLE_FIELDS.items()}
                fractions = {
                    variable: (sum(value is not None for value in values) / expected if expected else 0.0)
                    for variable, values in values_by_variable.items()
                }
                expected_units = forecast["variable_units"]
                units = point.hourly_units or {}
                lengths_valid = all(len(values) == expected for values in values_by_variable.values())
                units_valid = all(units.get(variable) == expected_units[variable] for variable in VARIABLE_FIELDS)
                complete = bool(expected and distinct_times and lengths_valid and units_valid and returned_latitude is not None and returned_longitude is not None and all(value == 1.0 for value in fractions.values()))
                status = "complete" if complete else "incomplete"
                error_message = None if complete else "missing/duplicate hours, values, units, or returned grid coordinates"
                with closing(sqlite3.connect(database)) as connection, connection:
                    connection.execute("PRAGMA foreign_keys=ON")
                    connection.execute(
                        """INSERT INTO synop_station_forecast_requests VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (cycle_id, station_id, source["id"], source["api_model"], request_time,
                         requested_latitude, requested_longitude, returned_latitude, returned_longitude,
                         distance, point.response_sha256, expected, fractions["temperature_2m"],
                         fractions["wind_speed_10m"], status, None, error_message),
                    )
                    for variable, values in values_by_variable.items():
                        for valid_time, value in zip(point.times, values):
                            connection.execute(
                                """INSERT INTO synop_station_forecast_values VALUES(?,?,?,?,?,?,?,?,?,?)""",
                                (cycle_id, station_id, source["id"], variable,
                                 _utc(valid_time, forecast["source_timezone"]), expected_units[variable], value,
                                 "complete" if value is not None and units.get(variable) == expected_units[variable] else "missing_or_invalid_unit",
                                 point.response_sha256, request_time),
                            )
                counts[status] += 1
            except Exception as error:
                with closing(sqlite3.connect(database)) as connection, connection:
                    connection.execute("PRAGMA foreign_keys=ON")
                    if not _already_archived(connection, cycle_id, station_id, source["id"]):
                        connection.execute(
                            """INSERT INTO synop_station_forecast_requests VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (cycle_id, station_id, source["id"], source["api_model"], request_time,
                             requested_latitude, requested_longitude, None, None, None, None, 0, 0.0, 0.0,
                             "failed", type(error).__name__, str(error)),
                        )
                counts["failed"] += 1

    attempted = counts["complete"] + counts["incomplete"] + counts["failed"]
    status = "complete" if attempted and counts["complete"] == attempted else "partial"
    return {"status": status, "stations": len(settings["stations"]), **counts}


def eligible_archived_forecasts(
    connection: sqlite3.Connection,
    *,
    station_id: str,
    observation_time_utc: str,
) -> list[sqlite3.Row]:
    """Return only values archived before the observation valid time, at that exact valid time."""
    connection.row_factory = sqlite3.Row
    return connection.execute(
        """SELECT v.*, r.requested_model_id, r.requested_latitude, r.requested_longitude,
                  r.returned_grid_latitude, r.returned_grid_longitude, r.grid_distance_km
             FROM synop_station_forecast_values v
             JOIN synop_station_forecast_requests r
               ON r.cycle_id=v.cycle_id AND r.station_id=v.station_id AND r.source_id=v.source_id
            WHERE v.station_id=? AND v.valid_time_utc=? AND v.request_time_utc<?
              AND v.status='complete' AND r.status='complete'
            ORDER BY v.source_id, v.variable_id""",
        (station_id, observation_time_utc, observation_time_utc),
    ).fetchall()
