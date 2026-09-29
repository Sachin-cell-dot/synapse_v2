from __future__ import annotations

import csv
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from ..utils.config import OperationalConfig
from ..preprocessing.geography import load_districts


def export_cycle(config: OperationalConfig, cycle_id: str) -> dict:
    database_path = config.resolve(config.data["storage"]["database_path"])
    geography = config.data["geography"]
    districts = {district.district_id: district for district in load_districts(config.resolve(geography["boundary_path"]), geography)}
    source_ids = [source["id"] for source in config.enabled_sources]
    if config.data["ensemble"]["enabled"]:
        source_ids.append(config.data["ensemble"]["id"])
    verification_provider = config.data["verification"]["provider"]
    with closing(sqlite3.connect(database_path)) as connection:
        cycle = connection.execute("SELECT retrieved_at_utc,configuration_sha256,mode FROM forecast_cycles WHERE cycle_id=?", (cycle_id,)).fetchone()
        if cycle is None:
            raise ValueError(f"Unknown forecast cycle: {cycle_id}")
        source_rows = connection.execute("""SELECT district_id,lead_days,source_id,precipitation_mm,requested_model_id,retrieved_at_utc,
            issue_time_utc,upstream_run_time_utc,run_identity_status,unit,hourly_completeness,complete_point_count,
            required_point_count,point_coverage,status,raw_response_sha256,source_class,blend_role,ensemble_spread_mm,spread_semantics FROM source_forecasts WHERE cycle_id=?""", (cycle_id,)).fetchall()
        blend_rows = connection.execute("SELECT district_id,valid_start_utc,valid_end_utc,lead_days,forecast_mm,status,fallback,method,predicted_errors_json,artifact_version,feature_schema_sha256,weights_json,issued_at_utc FROM blended_forecasts WHERE cycle_id=? ORDER BY lead_days,district_id", (cycle_id,)).fetchall()
        source_variable_rows = connection.execute("""SELECT district_id,lead_days,source_id,variable_id,statistic,value,unit,hourly_completeness,
            complete_point_count,required_point_count,point_coverage,status,raw_response_sha256 FROM source_variable_forecasts WHERE cycle_id=?""", (cycle_id,)).fetchall()
        variable_blend_rows = connection.execute("""SELECT district_id,lead_days,variable_id,statistic,value,unit,status,method,blend_eligibility,
            fallback,weights_json FROM variable_blended_forecasts WHERE cycle_id=?""", (cycle_id,)).fetchall()
        verification_rows = connection.execute(
            """SELECT district_id,valid_start_utc,valid_end_utc,value_mm,provider,classification,available_at_utc
               FROM verification WHERE provider=?""",
            (verification_provider,),
        ).fetchall()
    sources = {(row[0], row[1], row[2]): row[3:] for row in source_rows}
    source_variables = {(row[0], row[1], row[2], row[3], row[4]): row[5:] for row in source_variable_rows}
    variable_blends = {(row[0], row[1], row[2], row[3]): row[4:] for row in variable_blend_rows}
    verification = {
        (district_id, valid_start, valid_end): (value, provider, classification, available_at)
        for district_id, valid_start, valid_end, value, provider, classification, available_at in verification_rows
    }
    records = []
    for district_id, valid_start, valid_end, lead, forecast_mm, status, fallback, method, predicted_errors_json, artifact_version, feature_schema_sha256, weights_json, issued_at in blend_rows:
        district = districts[district_id]
        weights = json.loads(weights_json)
        predicted_errors = json.loads(predicted_errors_json or "{}")
        actual, provider, classification, available_at = verification.get(
            (district_id, valid_start, valid_end), (None, verification_provider, None, None)
        )
        record = {
            "cycle_id": cycle_id, "issued_at_utc": issued_at,
            "configuration_sha256": cycle[1], "mode": cycle[2],
            "district_id": district_id, "district": district.name, "division": district.division,
            "valid_start_utc": valid_start, "valid_end_utc": valid_end,
            "lead_days": lead, "synapse_wx_forecast_mm": forecast_mm,
            "status": status, "fallback": fallback,
            "blend_method": method, "artifact_version": artifact_version, "feature_schema_sha256": feature_schema_sha256,
            "verification_status": "available" if actual is not None else "pending",
            "verification_provider": provider,
            "verification_classification": classification,
            "verification_available_at_utc": available_at,
            "verification_mm": actual,
            "synapse_wx_absolute_error_mm": None if actual is None or forecast_mm is None else abs(forecast_mm - actual),
        }
        for source_id in source_ids:
            source = sources.get((district_id, lead, source_id))
            fields = ("mm", "requested_model_id", "retrieved_at_utc", "issue_time_utc", "upstream_run_time_utc", "run_identity_status", "unit", "hourly_completeness", "complete_point_count", "required_point_count", "point_coverage", "status", "raw_response_sha256", "source_class", "blend_role", "ensemble_spread_mm", "spread_semantics")
            for name, value in zip(fields, source or (None,) * len(fields)):
                record[f"source_{source_id}_{name}"] = value
            record[f"weight_{source_id}"] = weights.get(source_id)
            record[f"predicted_error_{source_id}_mm"] = predicted_errors.get(source_id)
        for variable_id, statistic in (("temperature_2m", "maximum"), ("temperature_2m", "minimum"), ("wind_speed_10m", "maximum"), ("wind_direction_10m", "mean")):
            label = f"{variable_id}_{statistic}"
            blend_value = variable_blends.get((district_id, lead, variable_id, statistic))
            blend_fields = ("value", "unit", "status", "method", "blend_eligibility", "fallback", "weights_json")
            for name, value in zip(blend_fields, blend_value or (None,) * len(blend_fields)):
                record[f"blend_{label}_{name}"] = value
            for source_id in [source["id"] for source in config.enabled_sources]:
                source_value = source_variables.get((district_id, lead, source_id, variable_id, statistic))
                source_fields = ("value", "unit", "hourly_completeness", "complete_point_count", "required_point_count", "point_coverage", "status", "raw_response_sha256")
                for name, value in zip(source_fields, source_value or (None,) * len(source_fields)):
                    record[f"source_{source_id}_{label}_{name}"] = value
        records.append(record)
    export_directory = config.resolve(config.data["storage"]["export_directory"])
    export_directory.mkdir(parents=True, exist_ok=True)
    output_path = export_directory / f"synapse_wx_cycle_{cycle_id}.csv"
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    return {"status": "pass", "cycle_id": cycle_id, "rows": len(records), "path": str(output_path)}
