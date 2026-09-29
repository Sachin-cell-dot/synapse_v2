from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from math import sqrt
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from ..models.xgboost_blend import infer_expected_errors, load_frozen_model
from ..preprocessing.geography import load_districts
from ..utils.config import OperationalConfig


METHOD_FIELDS = {
    "gfs": "gfs_rain_mm", "ifs_hres": "ifs_hres_rain_mm", "aifs": "aifs_rain_mm",
    "equal_weight": "equal_weight_mm", "inverse_mae": "inverse_mae_mm", "xgboost_error_weighted": "xgboost_blend_mm",
}
THRESHOLDS_MM = (1.0, 10.0, 25.0, 50.0)
EVALUATION_VERSION = "day1-holdout-v1"


def rainfall_intensity(mm: float) -> str:
    if mm <= 0: return "no_rain"
    if mm <= 2.4: return "very_light"
    if mm <= 15.5: return "light"
    if mm <= 64.4: return "moderate"
    if mm <= 115.5: return "heavy"
    if mm <= 204.4: return "very_heavy"
    return "extremely_heavy"


def continuous_metrics(actual: list[float], forecast: list[float], denominator: int) -> dict:
    if len(actual) != len(forecast):
        raise ValueError("Paired metrics require identical actual and forecast denominators")
    if not actual:
        return {"n": 0, "denominator": denominator, "coverage": 0.0 if denominator else None, "mae_mm": None, "rmse_mm": None, "bias_mm": None}
    errors = np.asarray(forecast) - np.asarray(actual)
    return {"n": len(actual), "denominator": denominator, "coverage": len(actual) / denominator if denominator else None, "mae_mm": float(np.mean(np.abs(errors))), "rmse_mm": float(sqrt(np.mean(errors ** 2))), "bias_mm": float(np.mean(errors))}


def event_metrics(actual: list[float], forecast: list[float], threshold: float) -> dict:
    observed = np.asarray(actual) >= threshold
    predicted = np.asarray(forecast) >= threshold
    hits = int(np.sum(observed & predicted)); misses = int(np.sum(observed & ~predicted)); false_alarms = int(np.sum(~observed & predicted))
    forecast_events = hits + false_alarms; observed_events = hits + misses
    return {
        "observed_events": observed_events, "forecast_events": forecast_events, "hits": hits, "misses": misses, "false_alarms": false_alarms,
        "pod": hits / observed_events if observed_events else None,
        "false_alarm_ratio": false_alarms / forecast_events if forecast_events else None,
        "csi": hits / (hits + misses + false_alarms) if hits + misses + false_alarms else None,
    }


def prior_inverse_mae_weights(history: list[dict], *, district_id: str, issue_time: datetime, source_ids: tuple[str, ...], window: int, power: float, floor: float) -> tuple[dict[str, float], str, int]:
    eligible = [row for row in history if row["district_id"] == district_id and row["verification_available_at"] < issue_time]
    eligible = sorted(eligible, key=lambda row: row["date"])[-window:]
    if not eligible:
        equal = 1 / len(source_ids)
        return {source: equal for source in source_ids}, "equal_weight_no_available_history", 0
    maes = {source: sum(abs(row["forecasts"][source] - row["actual_mm"]) for row in eligible) / len(eligible) for source in source_ids}
    raw = {source: 1 / max(value, floor) ** power for source, value in maes.items()}
    total = sum(raw.values())
    return {source: value / total for source, value in raw.items()}, "inverse_mae_past_available_only", len(eligible)


def _read_approved(config: OperationalConfig) -> tuple[list[dict], list[dict], dict]:
    xgb = config.data["xgboost"]; historical = config.data["historical_bootstrap"]
    source_ids = tuple(xgb["source_ids"]); geography = config.data["geography"]
    districts = load_districts(config.resolve(geography["boundary_path"]), geography)
    region = {district.district_id: district.division or "unassigned" for district in districts}
    history, test, raw_test_rows = [], [], []
    with config.resolve(historical["path"]).open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            day = row[historical["date_column"]]
            if day > xgb["held_out_end"] or day < xgb["train_start"]:
                continue
            in_test = xgb["held_out_start"] <= day <= xgb["held_out_end"]
            district_id = str(row[historical["district_id_column"]])
            if in_test:
                raw_test_rows.append(row)
            complete = district_id in region and row[historical["verification_column"]].strip() != "" and all(row[historical["source_columns"][source]].strip() != "" and row[historical["coverage_status_columns"][source]] == historical["required_coverage_status"] for source in source_ids)
            if not complete:
                continue
            forecasts = {source: float(row[historical["source_columns"][source]]) for source in source_ids}
            day_value = date.fromisoformat(day)
            valid_start = datetime.combine(day_value, time(hour=int(config.data["forecast"]["daily_accumulation_start_hour"])), ZoneInfo(config.data["forecast"]["source_timezone"]))
            record = {"date": day, "district_id": district_id, "district": next(d.name for d in districts if d.district_id == district_id), "region": region[district_id], "month": day[:7], "forecasts": forecasts, "actual_mm": float(row[historical["verification_column"]]), "verification_available_at": valid_start + timedelta(days=1, hours=float(historical["verification_availability_lag_hours"]))}
            (test if in_test else history).append(record)
    if len({(row["date"], row["district_id"]) for row in test}) != len(test):
        raise ValueError("Held-out rows are not uniquely aligned by district/day")
    return history, test, {"raw_test_rows": raw_test_rows, "districts": districts}


def _scorecard(rows: list[dict]) -> list[dict]:
    scopes = [("overall", "all", rows)]
    for field, name in (("district", "district"), ("region", "region"), ("month", "month"), ("observed_intensity", "observed_intensity")):
        values = sorted({row[field] for row in rows})
        scopes.extend((name, value, [row for row in rows if row[field] == value]) for value in values)
    output = []
    for scope_type, scope_value, group in scopes:
        actual = [row["imd_actual_mm"] for row in group]
        for method, field in METHOD_FIELDS.items():
            forecast = [row[field] for row in group]
            output.append({"metric_family": "continuous", "scope_type": scope_type, "scope_value": scope_value, "threshold_mm": None, "method": method, **continuous_metrics(actual, forecast, len(group)), "observed_events": None, "forecast_events": None, "hits": None, "misses": None, "false_alarms": None, "pod": None, "false_alarm_ratio": None, "csi": None})
    actual = [row["imd_actual_mm"] for row in rows]
    for threshold in THRESHOLDS_MM:
        for method, field in METHOD_FIELDS.items():
            event = event_metrics(actual, [row[field] for row in rows], threshold)
            output.append({"metric_family": "event", "scope_type": "overall", "scope_value": "all", "threshold_mm": threshold, "method": method, "n": len(rows), "denominator": len(rows), "coverage": 1.0, "mae_mm": None, "rmse_mm": None, "bias_mm": None, **event})
    return output


def evaluate_frozen_holdout(config: OperationalConfig) -> dict:
    xgb = config.data["xgboost"]
    loaded = load_frozen_model(config.resolve(xgb["artifact_directory"]), artifact_version=xgb["frozen_artifact_version"], feature_schema_sha256=xgb["frozen_feature_schema_sha256"])
    output_dir = config.resolve(config.data["storage"]["export_directory"]) / "holdout"
    prefix = f"{EVALUATION_VERSION}-{loaded.artifact_version}"
    metadata_path = output_dir / f"{prefix}.metadata.json"
    if metadata_path.exists():
        existing = json.loads(metadata_path.read_text(encoding="utf-8"))
        return {"status": "unchanged", **existing["outputs"], "artifact_version": loaded.artifact_version}
    history, test, inventory = _read_approved(config)
    source_ids = tuple(xgb["source_ids"]); blend = config.data["blend"]
    predictions = []
    for target in sorted(test, key=lambda row: (row["date"], row["district_id"])):
        valid_start = datetime.combine(date.fromisoformat(target["date"]), time(hour=int(config.data["forecast"]["daily_accumulation_start_hour"])), ZoneInfo(config.data["forecast"]["source_timezone"]))
        issue_time = valid_start - timedelta(days=1)
        inv_weights, inv_status, history_days = prior_inverse_mae_weights(history + test, district_id=target["district_id"], issue_time=issue_time, source_ids=source_ids, window=int(blend["rolling_window_days"]), power=float(blend["inverse_mae_power"]), floor=float(blend["mae_floor_mm"]))
        predicted_errors, xgb_weights = infer_expected_errors(loaded, forecasts=target["forecasts"], district_id=target["district_id"], region=target["region"], month=date.fromisoformat(target["date"]).month, floor_mm=float(xgb["predicted_error_floor_mm"]))
        forecasts = target["forecasts"]
        predictions.append({
            "evaluation_version": EVALUATION_VERSION, "artifact_version": loaded.artifact_version, "feature_schema_sha256": loaded.feature_schema_sha256,
            "historical_run_identity": loaded.metadata["historical_run_identity"], "date": target["date"], "district_id": target["district_id"], "district": target["district"], "region": target["region"], "month": target["month"], "observed_intensity": rainfall_intensity(target["actual_mm"]), "imd_actual_mm": target["actual_mm"],
            "gfs_rain_mm": forecasts["gfs"], "ifs_hres_rain_mm": forecasts["ifs_hres"], "aifs_rain_mm": forecasts["aifs"],
            "equal_weight_mm": sum(forecasts.values()) / 3, "inverse_mae_mm": sum(forecasts[source] * inv_weights[source] for source in source_ids), "xgboost_blend_mm": sum(forecasts[source] * xgb_weights[source] for source in source_ids),
            "inverse_mae_status": inv_status, "inverse_mae_history_days": history_days,
            **{f"inverse_mae_weight_{source}": inv_weights[source] for source in source_ids},
            **{f"xgboost_predicted_error_{source}_mm": predicted_errors[source] for source in source_ids},
            **{f"xgboost_weight_{source}": xgb_weights[source] for source in source_ids},
        })
    paired_keys = {(row["date"], row["district_id"]) for row in predictions}
    if len(paired_keys) != len(predictions) or any(any(row[field] is None for field in METHOD_FIELDS.values()) for row in predictions):
        raise ValueError("All methods must use one unique, fully paired held-out denominator")
    raw_rows = inventory["raw_test_rows"]
    expected = len(inventory["districts"]) * ((date.fromisoformat(xgb["held_out_end"]) - date.fromisoformat(xgb["held_out_start"])).days + 1)
    coverage = []
    for source in source_ids:
        value_column = config.data["historical_bootstrap"]["source_columns"][source]
        status_column = config.data["historical_bootstrap"]["coverage_status_columns"][source]
        available = sum(row[value_column].strip() != "" and row[status_column] == config.data["historical_bootstrap"]["required_coverage_status"] for row in raw_rows)
        coverage.append({"evaluation_version": EVALUATION_VERSION, "source": source, "expected_calendar_district_days": expected, "observed_input_rows": len(raw_rows), "quality_available_rows": available, "quality_coverage_of_observed_rows": available / len(raw_rows) if raw_rows else None, "quality_coverage_of_expected_calendar": available / expected if expected else None, "paired_evaluation_rows": len(predictions)})
    scorecard = _scorecard(predictions)
    output_dir.mkdir(parents=True, exist_ok=True)
    files = {"predictions": output_dir / f"{prefix}.predictions.csv", "scorecard": output_dir / f"{prefix}.scorecard.csv", "coverage": output_dir / f"{prefix}.missing-source-coverage.csv"}
    for key, rows in (("predictions", predictions), ("scorecard", scorecard), ("coverage", coverage)):
        with files[key].open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    hashes = {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in files.items()}
    outputs = {f"{key}_path": str(path) for key, path in files.items()}
    metadata = {"evaluation_version": EVALUATION_VERSION, "artifact_version": loaded.artifact_version, "feature_schema_sha256": loaded.feature_schema_sha256, "historical_run_identity": loaded.metadata["historical_run_identity"], "held_out_period": [xgb["held_out_start"], xgb["held_out_end"]], "paired_rows": len(predictions), "methods": list(METHOD_FIELDS), "thresholds_mm": list(THRESHOLDS_MM), "inverse_mae_observation_availability_rule": "valid interval end plus configured 120-hour lag must be strictly earlier than simulated Day-1 issue time; conservative proxy because actual historical release timestamps are unknown", "xgboost_expected_error_mae_is_not_blended_rainfall_mae": True, "file_sha256": hashes, "outputs": outputs}
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    return {"status": "pass", **outputs, "metadata_path": str(metadata_path), "paired_rows": len(predictions), "artifact_version": loaded.artifact_version}
