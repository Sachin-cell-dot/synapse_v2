from __future__ import annotations

import csv
import json
import math
import sqlite3
from collections import defaultdict
from contextlib import closing

from ..utils.config import OperationalConfig
from ..preprocessing.geography import load_districts


def _metrics(errors: list[float]) -> dict:
    if not errors:
        return {"n": 0, "mae_mm": None, "rmse_mm": None, "bias_mm": None}
    return {
        "n": len(errors),
        "mae_mm": sum(abs(error) for error in errors) / len(errors),
        "rmse_mm": math.sqrt(sum(error * error for error in errors) / len(errors)),
        "bias_mm": sum(errors) / len(errors),
    }


def evaluate_cycle(config: OperationalConfig, cycle_id: str) -> dict:
    database_path = config.resolve(config.data["storage"]["database_path"])
    provider = config.data["verification"]["provider"]
    source_ids = [source["id"] for source in config.enabled_sources]
    geography = config.data["geography"]
    districts = {district.district_id: district for district in load_districts(config.resolve(geography["boundary_path"]), geography)}
    with closing(sqlite3.connect(database_path)) as connection:
        cycle = connection.execute("SELECT retrieved_at_utc,configuration_sha256,mode FROM forecast_cycles WHERE cycle_id=?", (cycle_id,)).fetchone()
        if cycle is None:
            raise ValueError(f"Unknown forecast cycle: {cycle_id}")
        blends = connection.execute(
            """SELECT b.district_id,b.valid_start_utc,b.valid_end_utc,b.lead_days,b.forecast_mm,b.status,b.fallback,b.weights_json,
                      v.value_mm,v.available_at_utc,v.raw_response_sha256
               FROM blended_forecasts b
               LEFT JOIN verification v
                 ON v.district_id=b.district_id AND v.valid_start_utc=b.valid_start_utc
                AND v.valid_end_utc=b.valid_end_utc AND v.provider=?
               WHERE b.cycle_id=? ORDER BY b.lead_days,b.district_id""",
            (provider, cycle_id),
        ).fetchall()
        source_rows = connection.execute(
            "SELECT district_id,valid_start_utc,valid_end_utc,lead_days,source_id,precipitation_mm FROM source_forecasts WHERE cycle_id=?",
            (cycle_id,),
        ).fetchall()
    sources = {(row[0], row[1], row[2], row[3], row[4]): row[5] for row in source_rows}
    records = []
    errors_by_lead: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for district_id, valid_start, valid_end, lead, forecast_mm, status, fallback, weights_json, actual, available_at, verification_sha in blends:
        district = districts[district_id]
        record = {
            "cycle_id": cycle_id, "cycle_retrieved_at_utc": cycle[0],
            "configuration_sha256": cycle[1], "mode": cycle[2],
            "district_id": district_id, "district": district.name, "division": district.division,
            "valid_start_utc": valid_start, "valid_end_utc": valid_end, "lead_days": lead,
            "verification_status": "available" if actual is not None else "pending",
            "verification_provider": provider, "verification_mm": actual,
            "verification_available_at_utc": available_at,
            "verification_artifact_sha256": verification_sha,
            "synapse_wx_forecast_mm": forecast_mm,
            "synapse_wx_error_mm": None if actual is None or forecast_mm is None else forecast_mm - actual,
            "synapse_wx_absolute_error_mm": None if actual is None or forecast_mm is None else abs(forecast_mm - actual),
            "forecast_status": status, "fallback": fallback, "weights_json": weights_json,
        }
        if record["synapse_wx_error_mm"] is not None:
            errors_by_lead[lead]["synapse_wx"].append(record["synapse_wx_error_mm"])
        for source_id in source_ids:
            value = sources.get((district_id, valid_start, valid_end, lead, source_id))
            error = None if actual is None or value is None else value - actual
            record[f"source_{source_id}_mm"] = value
            record[f"source_{source_id}_error_mm"] = error
            record[f"source_{source_id}_absolute_error_mm"] = None if error is None else abs(error)
            if error is not None:
                errors_by_lead[lead][source_id].append(error)
        records.append(record)
    export_directory = config.resolve(config.data["storage"]["export_directory"])
    export_directory.mkdir(parents=True, exist_ok=True)
    csv_path = export_directory / f"synapse_wx_evaluation_{cycle_id}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    summary = {
        str(lead): {model: _metrics(errors) for model, errors in sorted(models.items())}
        for lead, models in sorted(errors_by_lead.items())
    }
    report = {
        "status": "pass",
        "cycle_id": cycle_id,
        "rows": len(records),
        "verified_rows": sum(record["verification_status"] == "available" for record in records),
        "pending_rows": sum(record["verification_status"] == "pending" for record in records),
        "verification_provider": provider,
        "metrics_by_lead": summary,
        "csv_path": str(csv_path),
    }
    json_path = export_directory / f"synapse_wx_evaluation_{cycle_id}.json"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    report["json_path"] = str(json_path)
    return report
