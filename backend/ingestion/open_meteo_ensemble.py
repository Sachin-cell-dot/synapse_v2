from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class EnsemblePointForecast:
    source_id: str
    requested_model_id: str
    latitude: float
    longitude: float
    returned_latitude: float
    returned_longitude: float
    times: tuple[str, ...]
    mean: tuple[float | None, ...]
    spread: tuple[float | None, ...]
    response_sha256: str
    retrieved_at_utc: str


def parse_ensemble_payload(payload: dict, *, mean_variable: str, spread_variable: str, required_unit: str) -> tuple[tuple[str, ...], tuple[float | None, ...], tuple[float | None, ...]]:
    hourly = payload.get("hourly"); units = payload.get("hourly_units")
    if not isinstance(hourly, dict) or not isinstance(units, dict):
        raise ValueError("Ensemble response is missing hourly data or units")
    if units.get(mean_variable) != required_unit or units.get(spread_variable) != required_unit:
        raise ValueError(f"Ensemble units must be {required_unit}: {units}")
    times, mean, spread = hourly.get("time"), hourly.get(mean_variable), hourly.get(spread_variable)
    if not all(isinstance(values, list) for values in (times, mean, spread)) or not (len(times) == len(mean) == len(spread)):
        raise ValueError("Ensemble time, mean and spread arrays must be present and aligned")
    return tuple(map(str, times)), tuple(None if value is None else float(value) for value in mean), tuple(None if value is None else float(value) for value in spread)


def fetch_ensemble_points(*, source: dict, coordinates: tuple[tuple[float, float], ...], forecast: dict, raw_directory: Path, cache_directory: Path) -> tuple[EnsemblePointForecast, ...]:
    if not coordinates:
        raise ValueError("At least one ensemble coordinate is required")
    params = {
        "latitude": ",".join(str(latitude) for latitude, _ in coordinates), "longitude": ",".join(str(longitude) for _, longitude in coordinates),
        "hourly": f"{source['mean_variable']},{source['spread_variable']}", "models": source["api_model"],
        "forecast_days": int(forecast["target_start_day_offset"]) + max(forecast["lead_days"]) + 1, "timezone": forecast["source_timezone"], "precipitation_unit": forecast["unit"],
    }
    request_url = f"{source['api_base_url']}?{urlencode(params)}"
    bucket = int(time.time() // (float(forecast["request_cache_ttl_minutes"]) * 60))
    cache_directory.mkdir(parents=True, exist_ok=True)
    cache_path = cache_directory / f"{hashlib.sha256(f'{bucket}:{request_url}'.encode()).hexdigest()}.json"
    body = cache_path.read_bytes() if cache_path.exists() else None
    last_error = None
    for attempt in range(int(forecast["request_attempts"])):
        if body is not None: break
        try:
            time.sleep(float(forecast["request_interval_seconds"]))
            with urlopen(Request(request_url, headers={"User-Agent": forecast["user_agent"]}), timeout=float(forecast["request_timeout_seconds"])) as response:
                body = response.read()
            cache_path.write_bytes(body)
        except Exception as error:
            last_error = error
            if attempt + 1 < int(forecast["request_attempts"]):
                time.sleep(min(float(forecast["retry_backoff_seconds"]) * 2**attempt, float(forecast["maximum_retry_wait_seconds"])))
    if body is None:
        raise RuntimeError("Ensemble API request failed") from last_error
    digest = hashlib.sha256(body).hexdigest(); raw_directory.mkdir(parents=True, exist_ok=True)
    raw_path = raw_directory / f"{digest}.json"
    if not raw_path.exists(): raw_path.write_bytes(body)
    decoded = json.loads(body); payloads = decoded if isinstance(decoded, list) else [decoded]
    if len(payloads) != len(coordinates):
        raise ValueError("Ensemble response location count differs from request")
    retrieved = datetime.now(timezone.utc).isoformat(); results = []
    for (latitude, longitude), payload in zip(coordinates, payloads):
        times, mean, spread = parse_ensemble_payload(payload, mean_variable=source["mean_variable"], spread_variable=source["spread_variable"], required_unit=forecast["unit"])
        results.append(EnsemblePointForecast(source["id"], source["api_model"], latitude, longitude, float(payload["latitude"]), float(payload["longitude"]), times, mean, spread, digest, retrieved))
    return tuple(results)
