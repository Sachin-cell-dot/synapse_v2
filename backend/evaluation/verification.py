from __future__ import annotations

import csv
import hashlib
import sqlite3
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ..utils.config import OperationalConfig
from ..preprocessing.geography import load_districts
from ..utils.store import initialize_database


def _available_at(row: dict[str, str], column: str | None, imported_at: datetime) -> datetime:
    if not column:
        return imported_at
    value = row.get(column, "").strip()
    if not value:
        raise ValueError(f"Verification row is missing configured availability time column {column}")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Verification availability timestamps must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def import_verification(config: OperationalConfig, source_path: Path, *, dry_run: bool = False) -> dict:
    source_path = source_path.resolve()
    source_bytes = source_path.read_bytes()
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    settings = config.data["verification"]
    forecast = config.data["forecast"]
    geography = config.data["geography"]
    known_districts = {district.district_id for district in load_districts(config.resolve(geography["boundary_path"]), geography)}
    database_path = config.resolve(config.data["storage"]["database_path"])
    imported_at = datetime.now(timezone.utc)
    zone = ZoneInfo(forecast["source_timezone"])
    accumulation_hour = int(forecast["daily_accumulation_start_hour"])
    required_columns = {settings["date_column"], settings["district_id_column"], settings["value_column"]}
    if settings["availability_time_column"]:
        required_columns.add(settings["availability_time_column"])
    records = []
    seen = set()
    with source_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = sorted(required_columns - set(reader.fieldnames or ()))
        if missing:
            raise ValueError(f"Verification file is missing configured columns: {missing}")
        for line_number, row in enumerate(reader, start=2):
            district_id = str(row[settings["district_id_column"]]).strip()
            if settings["reject_unknown_districts"] and district_id not in known_districts:
                raise ValueError(f"Unknown district id {district_id} on line {line_number}")
            valid_date = date.fromisoformat(row[settings["date_column"]].strip())
            valid_start_local = datetime.combine(valid_date, time(hour=accumulation_hour), zone)
            valid_end_local = valid_start_local + timedelta(days=1)
            key = (district_id, valid_start_local.isoformat(), settings["provider"])
            if key in seen:
                raise ValueError(f"Duplicate verification key on line {line_number}: {key}")
            seen.add(key)
            value = float(row[settings["value_column"]])
            if value < 0:
                raise ValueError(f"Negative verification rainfall on line {line_number}")
            records.append((
                district_id,
                valid_start_local.astimezone(timezone.utc).isoformat(),
                valid_end_local.astimezone(timezone.utc).isoformat(),
                value,
                settings["provider"],
                settings["classification"],
                _available_at(row, settings["availability_time_column"], imported_at).isoformat(),
                source_sha256,
            ))
    if dry_run:
        return {
            "status": "pass",
            "dry_run": True,
            "source_path": str(source_path),
            "source_sha256": source_sha256,
            "validated_rows": len(records),
            "provider": settings["provider"],
            "classification": settings["classification"],
            "availability_policy": "configured_column" if settings["availability_time_column"] else "import_time",
        }
    initialize_database(database_path)
    inserted = 0
    unchanged = 0
    with closing(sqlite3.connect(database_path)) as connection, connection:
        for record in records:
            existing = connection.execute(
                """SELECT valid_end_utc,value_mm,classification,available_at_utc,raw_response_sha256
                   FROM verification WHERE district_id=? AND valid_start_utc=? AND provider=?""",
                (record[0], record[1], record[4]),
            ).fetchone()
            expected = (record[2], record[3], record[5], record[6], record[7])
            if existing is not None:
                comparable_existing = existing if settings["availability_time_column"] else (existing[0], existing[1], existing[2], existing[4])
                comparable_expected = expected if settings["availability_time_column"] else (expected[0], expected[1], expected[2], expected[4])
                if comparable_existing != comparable_expected:
                    raise RuntimeError(f"Immutable verification conflict for {record[0]}/{record[1]}/{record[4]}")
                unchanged += 1
                continue
            connection.execute(
                "INSERT INTO verification(district_id,valid_start_utc,valid_end_utc,value_mm,provider,classification,available_at_utc,raw_response_sha256) VALUES(?,?,?,?,?,?,?,?)",
                record,
            )
            inserted += 1
    return {
        "status": "pass",
        "dry_run": False,
        "source_path": str(source_path),
        "source_sha256": source_sha256,
        "rows": len(records),
        "inserted": inserted,
        "unchanged": unchanged,
        "provider": settings["provider"],
        "classification": settings["classification"],
        "availability_policy": "configured_column" if settings["availability_time_column"] else "import_time",
    }
