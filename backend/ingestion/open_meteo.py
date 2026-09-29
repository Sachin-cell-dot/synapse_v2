from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class PointForecast:
    source_id: str
    requested_model_id: str
    latitude: float
    longitude: float
    times: tuple[str, ...]
    precipitation: tuple[float | None, ...]
    response_sha256: str
    raw_path: Path
    request_url: str
    retrieved_at_utc: str
    upstream_run_time_utc: str | None
    run_identity_status: str
    temperature_2m: tuple[float | None, ...] = ()
    wind_speed_10m: tuple[float | None, ...] = ()
    wind_direction_10m: tuple[float | None, ...] = ()
    hourly_units: dict[str, str] | None = None
    requested_at_utc: str | None = None
    returned_latitude: float | None = None
    returned_longitude: float | None = None


def _parse_payload(payload: dict, variable: str) -> tuple[tuple[str, ...], tuple[float | None, ...]]:
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        raise ValueError("Open-Meteo response has no hourly object")
    times = hourly.get("time")
    values = hourly.get(variable)
    if not isinstance(times, list) or not isinstance(values, list):
        raise ValueError(f"Open-Meteo response is missing hourly time or {variable}")
    if len(times) != len(values):
        raise ValueError("Open-Meteo hourly time/value lengths differ")
    return tuple(str(value) for value in times), tuple(None if value is None else float(value) for value in values)


def _parse_optional_hourly(payload: dict, variable: str, expected_length: int) -> tuple[float | None, ...]:
    hourly = payload.get("hourly")
    values = hourly.get(variable) if isinstance(hourly, dict) else None
    if not isinstance(values, list) or len(values) != expected_length:
        return tuple(None for _ in range(expected_length))
    return tuple(None if value is None else float(value) for value in values)


def fetch_point(*, source: dict, latitude: float, longitude: float, forecast: dict, raw_directory: Path, cache_directory: Path) -> PointForecast:
    return fetch_points(source=source, coordinates=((latitude, longitude),), forecast=forecast, raw_directory=raw_directory, cache_directory=cache_directory)[0]


def fetch_points(*, source: dict, coordinates: tuple[tuple[float, float], ...], forecast: dict, raw_directory: Path, cache_directory: Path) -> tuple[PointForecast, ...]:
    if not coordinates:
        raise ValueError("At least one coordinate is required")
    params = {
        "latitude": ",".join(str(latitude) for latitude, _ in coordinates),
        "longitude": ",".join(str(longitude) for _, longitude in coordinates),
        "hourly": ",".join(dict.fromkeys((forecast["hourly_variable"], *forecast.get("additional_hourly_variables", ())))),
        "models": source["api_model"],
        "forecast_days": int(forecast["target_start_day_offset"]) + max(forecast["lead_days"]) + 1,
        "timezone": forecast["source_timezone"],
        "wind_speed_unit": forecast.get("wind_speed_unit", "ms"),
    }
    request_url = f"{forecast['api_base_url']}?{urlencode(params)}"
    cache_seconds = float(forecast["request_cache_ttl_minutes"]) * 60
    cache_bucket = int(time.time() // cache_seconds)
    request_digest = hashlib.sha256(f"{cache_bucket}:{request_url}".encode()).hexdigest()
    cache_directory.mkdir(parents=True, exist_ok=True)
    cache_path = cache_directory / f"{request_digest}.json"
    attempts = int(forecast["request_attempts"])
    last_error: Exception | None = None
    body: bytes | None = None
    requested_at_utc = datetime.now(timezone.utc).isoformat()
    if cache_path.exists():
        body = cache_path.read_bytes()
    else:
        for attempt in range(attempts):
            try:
                time.sleep(float(forecast["request_interval_seconds"]))
                request = Request(request_url, headers={"User-Agent": forecast["user_agent"]})
                with urlopen(request, timeout=float(forecast["request_timeout_seconds"])) as response:
                    body = response.read()
                cache_path.write_bytes(body)
                break
            except Exception as error:
                last_error = error
                if attempt + 1 < attempts:
                    wait = float(forecast["retry_backoff_seconds"]) * (2**attempt)
                    time.sleep(min(wait, float(forecast["maximum_retry_wait_seconds"])))
    if body is None:
        raise RuntimeError(f"Open-Meteo request failed after {attempts} attempts") from last_error
    retrieved_at_utc = datetime.now(timezone.utc).isoformat()
    digest = hashlib.sha256(body).hexdigest()
    raw_directory.mkdir(parents=True, exist_ok=True)
    raw_path = raw_directory / f"{digest}.json"
    if not raw_path.exists():
        raw_path.write_bytes(body)
    payload = json.loads(body)
    payloads = payload if isinstance(payload, list) else [payload]
    if len(payloads) != len(coordinates):
        raise ValueError(f"Open-Meteo returned {len(payloads)} locations for {len(coordinates)} requested coordinates")
    results = []
    for (latitude, longitude), location_payload in zip(coordinates, payloads):
        times, precipitation = _parse_payload(location_payload, forecast["hourly_variable"])
        units = location_payload.get("hourly_units") if isinstance(location_payload.get("hourly_units"), dict) else {}
        results.append(PointForecast(
            source_id=source["id"], requested_model_id=source["api_model"],
            latitude=latitude, longitude=longitude, times=times, precipitation=precipitation,
            response_sha256=digest, raw_path=raw_path, request_url=request_url,
            retrieved_at_utc=retrieved_at_utc, upstream_run_time_utc=None,
            run_identity_status="not_exposed_by_latest_endpoint",
            temperature_2m=_parse_optional_hourly(location_payload, "temperature_2m", len(times)),
            wind_speed_10m=_parse_optional_hourly(location_payload, "wind_speed_10m", len(times)),
            wind_direction_10m=_parse_optional_hourly(location_payload, "wind_direction_10m", len(times)),
            hourly_units={str(key): str(value) for key, value in units.items()},
            requested_at_utc=requested_at_utc,
            returned_latitude=float(location_payload["latitude"]) if location_payload.get("latitude") is not None else None,
            returned_longitude=float(location_payload["longitude"]) if location_payload.get("longitude") is not None else None,
        ))
    return tuple(results)
