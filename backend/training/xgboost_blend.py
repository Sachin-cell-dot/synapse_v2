from __future__ import annotations

import csv
import hashlib
import json
from datetime import date
from pathlib import Path

import numpy as np
from xgboost import XGBRegressor

from ..models.xgboost_blend import MODEL_TYPE, feature_names, make_features
from ..preprocessing.geography import load_districts
from ..utils.config import OperationalConfig


def _period(value: str, start: str, end: str) -> bool:
    return start <= value <= end


def build_rows(config: OperationalConfig) -> tuple[list[dict], list[dict], dict]:
    settings = config.data["xgboost"]
    historical = config.data["historical_bootstrap"]
    source_ids = tuple(settings["source_ids"])
    if source_ids != tuple(source["id"] for source in config.enabled_sources):
        raise ValueError("XGBoost source contract must exactly match enabled operational sources")
    geography = config.data["geography"]
    districts = load_districts(config.resolve(geography["boundary_path"]), geography)
    district_region = {district.district_id: district.division or "unassigned" for district in districts}
    metadata_stub = {
        "source_ids": list(source_ids), "district_ids": sorted(district_region),
        "regions": sorted(set(district_region.values())),
    }
    metadata_stub["feature_names"] = list(feature_names(tuple(metadata_stub["source_ids"]), tuple(metadata_stub["district_ids"]), tuple(metadata_stub["regions"])))
    required = {historical["date_column"], historical["district_id_column"], historical["verification_column"], *historical["source_columns"].values(), *historical["coverage_status_columns"].values()}
    train, validation, seen = [], [], set()
    approved_digest = hashlib.sha256()
    with config.resolve(historical["path"]).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"Training input missing columns: {sorted(missing)}")
        for row in reader:
            day = row[historical["date_column"]]
            split = "train" if _period(day, settings["train_start"], settings["train_end"]) else "validation" if _period(day, settings["validation_start"], settings["validation_end"]) else None
            if split is None:
                continue
            district_id = str(row[historical["district_id_column"]])
            key = (day, district_id)
            if key in seen:
                raise ValueError(f"Duplicate aligned district/day row: {key}")
            seen.add(key)
            if district_id not in district_region:
                raise ValueError(f"Unknown district in training input: {district_id}")
            if any(not row[historical["source_columns"][source]].strip() for source in source_ids):
                raise ValueError(f"Incomplete three-source row: {key}")
            if any(row[historical["coverage_status_columns"][source]] != historical["required_coverage_status"] for source in source_ids):
                raise ValueError(f"Failed source coverage row: {key}")
            forecasts = {source: float(row[historical["source_columns"][source]]) for source in source_ids}
            actual = float(row[historical["verification_column"]])
            approved_digest.update(json.dumps({"date": day, "district_id": district_id, "forecasts": forecasts, "actual": actual}, sort_keys=True, separators=(",", ":")).encode())
            target = train if split == "train" else validation
            for source in source_ids:
                target.append({"date": day, "district_id": district_id, "region": district_region[district_id], "month": date.fromisoformat(day).month, "source_id": source, "forecasts": forecasts, "target_absolute_error_mm": abs(forecasts[source] - actual)})
    if not train or not validation:
        raise ValueError("Configured chronological train and validation periods must both contain rows")
    contract = {**metadata_stub, "approved_training_validation_rows_sha256": approved_digest.hexdigest(), "historical_run_identity": "unknown_legacy_daily_forecast_alignment_not_exact_run"}
    return train, validation, contract


def _matrix(rows: list[dict], metadata: dict) -> tuple[np.ndarray, np.ndarray]:
    x = np.vstack([make_features(source_id=row["source_id"], forecasts=row["forecasts"], district_id=row["district_id"], region=row["region"], month=row["month"], metadata=metadata) for row in rows])
    y = np.asarray([row["target_absolute_error_mm"] for row in rows], dtype=float)
    return x, y


def train_model(config: OperationalConfig) -> dict:
    if config.data["xgboost"]["training_frozen"]:
        raise RuntimeError("XGBoost training is frozen; refusing to retrain after held-out evaluation opened")
    train, validation, contract = build_rows(config)
    settings = config.data["xgboost"]
    x_train, y_train = _matrix(train, contract)
    x_validation, y_validation = _matrix(validation, contract)
    candidates = settings["parameter_candidates"]
    trials = []
    for params in candidates:
        model = XGBRegressor(objective="reg:absoluteerror", eval_metric="mae", tree_method="hist", random_state=int(settings["random_seed"]), n_jobs=1, **params)
        model.fit(x_train, y_train, verbose=False)
        mae = float(np.mean(np.abs(model.predict(x_validation) - y_validation)))
        trials.append((mae, params))
    validation_mae, selected = min(trials, key=lambda item: item[0])
    final = XGBRegressor(objective="reg:absoluteerror", eval_metric="mae", tree_method="hist", random_state=int(settings["random_seed"]), n_jobs=1, **selected)
    final.fit(np.vstack((x_train, x_validation)), np.concatenate((y_train, y_validation)), verbose=False)
    schema_bytes = json.dumps(contract["feature_names"], separators=(",", ":"), ensure_ascii=True).encode()
    schema_sha = hashlib.sha256(schema_bytes).hexdigest()
    version_seed = json.dumps({"schema": schema_sha, "source": contract["approved_training_validation_rows_sha256"], "selected": selected, "train": [settings["train_start"], settings["train_end"]], "validation": [settings["validation_start"], settings["validation_end"]]}, sort_keys=True, separators=(",", ":")).encode()
    version = hashlib.sha256(version_seed).hexdigest()[:16]
    artifact_dir = config.resolve(settings["artifact_directory"])
    artifact_dir.mkdir(parents=True, exist_ok=True)
    model_file = f"day1_expected_error_{version}.json"
    final.save_model(artifact_dir / model_file)
    model_sha = hashlib.sha256((artifact_dir / model_file).read_bytes()).hexdigest()
    metadata = {
        "artifact_version": version, "model_type": MODEL_TYPE, "model_file": model_file, "model_sha256": model_sha,
        "feature_names": contract["feature_names"], "feature_schema_sha256": schema_sha,
        "source_ids": contract["source_ids"], "district_ids": contract["district_ids"], "regions": contract["regions"],
        "target": "absolute source error against IMD rainfall (mm)", "lead_days": 1,
        "train_period": [settings["train_start"], settings["train_end"]], "validation_period": [settings["validation_start"], settings["validation_end"]],
        "held_out_period_not_loaded_for_fit_or_tuning": [settings["held_out_start"], settings["held_out_end"]],
        "train_source_rows": len(train), "validation_source_rows": len(validation), "selected_parameters": selected,
        "validation_expected_error_mae_mm": validation_mae,
        "parameter_trials": [{"parameters": params, "validation_expected_error_mae_mm": mae} for mae, params in trials],
        "approved_training_validation_rows_sha256": contract["approved_training_validation_rows_sha256"], "historical_run_identity": contract["historical_run_identity"],
        "rolling_error_features": [], "feature_importance_is_not_blend_weight": True,
    }
    metadata_file = f"day1_expected_error_{version}.metadata.json"
    metadata_bytes = json.dumps(metadata, indent=2, sort_keys=True).encode()
    (artifact_dir / metadata_file).write_bytes(metadata_bytes)
    active = {"artifact_version": version, "metadata_file": metadata_file, "metadata_sha256": hashlib.sha256(metadata_bytes).hexdigest()}
    (artifact_dir / "active.json").write_text(json.dumps(active, indent=2, sort_keys=True), encoding="utf-8")
    return {"status": "pass", "artifact_version": version, "artifact_directory": str(artifact_dir), "model_file": model_file, "metadata_file": metadata_file, "train_source_rows": len(train), "validation_source_rows": len(validation), "validation_expected_error_mae_mm": validation_mae}
