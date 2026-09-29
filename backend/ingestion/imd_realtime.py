from __future__ import annotations

import csv
import hashlib
import json
import math
import struct
import time
from datetime import date, datetime, timezone
from math import fsum
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..utils.config import OperationalConfig
from ..preprocessing.geography import geometry_bounds, load_districts, point_in_geometry


def _download(settings: dict, valid_date: date) -> tuple[bytes, dict[str, str], datetime, str]:
    form_value = valid_date.strftime(settings["request_date_format"])
    encoded = urlencode({settings["request_field"]: form_value}).encode()
    attempts = int(settings["request_attempts"])
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = Request(settings["download_url"], data=encoded, headers={"User-Agent": settings["user_agent"]})
            with urlopen(request, timeout=float(settings["request_timeout_seconds"])) as response:
                retrieved_at = datetime.now(timezone.utc)
                return response.read(), dict(response.headers.items()), retrieved_at, form_value
        except Exception as error:
            last_error = error
            if attempt + 1 < attempts:
                time.sleep(float(settings["retry_backoff_seconds"]) * (2**attempt))
    raise RuntimeError(f"IMD request failed after {attempts} attempts") from last_error


def _decode_grid(body: bytes, grid: dict) -> tuple[float, ...]:
    longitude_count = int(grid["longitude_count"])
    latitude_count = int(grid["latitude_count"])
    value_bytes = int(grid["value_bytes"])
    expected_bytes = longitude_count * latitude_count * value_bytes
    if len(body) != expected_bytes:
        raise ValueError(f"IMD binary has {len(body)} bytes; configured grid requires {expected_bytes}")
    if value_bytes != struct.calcsize("f"):
        raise ValueError("Configured IMD value size is not a single-precision float")
    byte_order = {"little": "<", "big": ">"}.get(grid["byte_order"])
    if byte_order is None:
        raise ValueError("IMD byte_order must be little or big")
    return struct.unpack(f"{byte_order}{longitude_count * latitude_count}f", body)


def _district_rows(config: OperationalConfig, values: tuple[float, ...], valid_date: date, retrieved_at: datetime, source_sha256: str) -> list[dict]:
    imd = config.data["imd_realtime"]
    grid = imd["grid"]
    geography = config.data["geography"]
    verification = config.data["verification"]
    districts = load_districts(config.resolve(geography["boundary_path"]), geography)
    longitude_count = int(grid["longitude_count"])
    latitude_count = int(grid["latitude_count"])
    longitude_start = float(grid["longitude_start"])
    latitude_start = float(grid["latitude_start"])
    spacing = float(grid["spacing_degrees"])
    minimum_coverage = float(grid["minimum_valid_coverage_fraction"])
    rows = []
    for district in districts:
        selected = []
        min_longitude, min_latitude, max_longitude, max_latitude = geometry_bounds(district.geometry)
        first_longitude_index = max(0, math.ceil((min_longitude - longitude_start) / spacing))
        last_longitude_index = min(longitude_count - 1, math.floor((max_longitude - longitude_start) / spacing))
        first_latitude_index = max(0, math.ceil((min_latitude - latitude_start) / spacing))
        last_latitude_index = min(latitude_count - 1, math.floor((max_latitude - latitude_start) / spacing))
        for latitude_index in range(first_latitude_index, last_latitude_index + 1):
            latitude = latitude_start + latitude_index * spacing
            for longitude_index in range(first_longitude_index, last_longitude_index + 1):
                longitude = longitude_start + longitude_index * spacing
                if point_in_geometry(longitude, latitude, district.geometry):
                    selected.append((values[latitude_index * longitude_count + longitude_index], math.cos(math.radians(latitude))))
        valid = [(value, weight) for value, weight in selected if math.isfinite(value) and value >= 0]
        coverage = len(valid) / len(selected) if selected else 0.0
        rainfall = fsum(value * weight for value, weight in valid) / fsum(weight for _, weight in valid) if valid and coverage >= minimum_coverage else None
        rows.append({
            verification["date_column"]: valid_date.isoformat(),
            verification["district_id_column"]: district.district_id,
            verification["value_column"]: rainfall,
            verification["availability_time_column"]: retrieved_at.isoformat(),
            "district": district.name,
            "division": district.division,
            "grid_point_count": len(selected),
            "valid_grid_point_count": len(valid),
            "valid_coverage_fraction": coverage,
            "source_artifact_sha256": source_sha256,
        })
    return rows


def fetch_imd_district_rainfall(config: OperationalConfig, valid_date: date) -> dict:
    settings = config.data["imd_realtime"]
    body, headers, retrieved_at, form_value = _download(settings, valid_date)
    values = _decode_grid(body, settings["grid"])
    digest = hashlib.sha256(body).hexdigest()
    raw_directory = config.resolve(settings["raw_directory"])
    raw_directory.mkdir(parents=True, exist_ok=True)
    raw_path = raw_directory / f"{digest}.grd"
    manifest_path = raw_directory / f"{digest}.manifest.json"
    if not raw_path.exists():
        raw_path.write_bytes(body)
    if manifest_path.exists():
        existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        effective_retrieved_at = datetime.fromisoformat(existing_manifest["retrieved_at_utc"].replace("Z", "+00:00")).astimezone(timezone.utc)
    else:
        effective_retrieved_at = retrieved_at
    rows = _district_rows(config, values, valid_date, effective_retrieved_at, digest)
    incomplete = [row for row in rows if row[config.data["verification"]["value_column"]] is None]
    if incomplete:
        raise ValueError(f"IMD district aggregation failed coverage for: {[row['district'] for row in incomplete]}")
    csv_directory = config.resolve(settings["district_csv_directory"])
    csv_directory.mkdir(parents=True, exist_ok=True)
    csv_path = csv_directory / f"imd_district_rainfall_{valid_date.isoformat()}_{digest[:12]}.csv"
    fields = list(rows[0])
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    if not manifest_path.exists():
        manifest_path.write_text(json.dumps({
            "provider": settings["provider"],
            "product": settings["product"],
            "valid_date": valid_date.isoformat(),
            "retrieved_at_utc": effective_retrieved_at.isoformat(),
            "request_url": settings["download_url"],
            "request_form": {settings["request_field"]: form_value},
            "response_headers": headers,
            "sha256": digest,
            "bytes": len(body),
            "configured_grid": settings["grid"],
            "district_output": str(csv_path),
        }, indent=2), encoding="utf-8")
    return {
        "status": "pass",
        "valid_date": valid_date.isoformat(),
        "retrieved_at_utc": effective_retrieved_at.isoformat(),
        "raw_path": str(raw_path),
        "manifest_path": str(manifest_path),
        "district_csv_path": str(csv_path),
        "sha256": digest,
        "bytes": len(body),
        "district_rows": len(rows),
    }
