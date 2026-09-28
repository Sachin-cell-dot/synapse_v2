from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import numpy as np
from xgboost import XGBRegressor


MODEL_TYPE = "source_specific_expected_absolute_error"


@dataclass(frozen=True)
class LoadedErrorModel:
    model: XGBRegressor
    metadata: dict
    artifact_version: str
    feature_schema_sha256: str


def feature_names(source_ids: tuple[str, ...], district_ids: tuple[str, ...], regions: tuple[str, ...]) -> tuple[str, ...]:
    numeric = ("source_value_mm", *(f"forecast_{source}_mm" for source in source_ids), "model_spread_mm", "month_sin", "month_cos")
    categories = (*(f"source={value}" for value in source_ids), *(f"district={value}" for value in district_ids), *(f"region={value}" for value in regions))
    return tuple(numeric) + tuple(categories)


def make_features(*, source_id: str, forecasts: Mapping[str, float], district_id: str, region: str, month: int, metadata: Mapping) -> np.ndarray:
    source_ids = tuple(metadata["source_ids"])
    district_ids = tuple(metadata["district_ids"])
    regions = tuple(metadata["regions"])
    if tuple(forecasts) != source_ids:
        raise ValueError(f"XGBoost requires ordered source contract {source_ids}, got {tuple(forecasts)}")
    if source_id not in source_ids or district_id not in district_ids or region not in regions or not 1 <= month <= 12:
        raise ValueError("Inference category is outside the artifact feature schema")
    values = [float(forecasts[source]) for source in source_ids]
    row = [float(forecasts[source_id]), *values, max(values) - min(values), np.sin(2 * np.pi * month / 12), np.cos(2 * np.pi * month / 12)]
    row += [1.0 if source_id == value else 0.0 for value in source_ids]
    row += [1.0 if district_id == value else 0.0 for value in district_ids]
    row += [1.0 if region == value else 0.0 for value in regions]
    if len(row) != len(metadata["feature_names"]):
        raise ValueError("Feature vector does not match artifact schema")
    return np.asarray(row, dtype=float)


def expected_error_weights(predicted_errors: Mapping[str, float], floor_mm: float) -> dict[str, float]:
    if not predicted_errors or floor_mm <= 0:
        raise ValueError("Predicted errors and a positive floor are required")
    positive = {source: max(float(error), floor_mm) for source, error in predicted_errors.items()}
    inverse = {source: 1.0 / error for source, error in positive.items()}
    total = sum(inverse.values())
    return {source: value / total for source, value in inverse.items()}


def load_active_model(artifact_directory: Path) -> LoadedErrorModel:
    active_path = artifact_directory / "active.json"
    active = json.loads(active_path.read_text(encoding="utf-8"))
    metadata_path = artifact_directory / active["metadata_file"]
    metadata_bytes = metadata_path.read_bytes()
    if hashlib.sha256(metadata_bytes).hexdigest() != active["metadata_sha256"]:
        raise ValueError("Active XGBoost metadata hash mismatch")
    metadata = json.loads(metadata_bytes)
    model_path = artifact_directory / metadata["model_file"]
    if hashlib.sha256(model_path.read_bytes()).hexdigest() != metadata["model_sha256"]:
        raise ValueError("XGBoost model artifact hash mismatch")
    schema = json.dumps(metadata["feature_names"], separators=(",", ":"), ensure_ascii=True).encode()
    if hashlib.sha256(schema).hexdigest() != metadata["feature_schema_sha256"]:
        raise ValueError("XGBoost feature schema hash mismatch")
    model = XGBRegressor()
    model.load_model(model_path)
    return LoadedErrorModel(model, metadata, metadata["artifact_version"], metadata["feature_schema_sha256"])


def load_frozen_model(artifact_directory: Path, *, artifact_version: str, feature_schema_sha256: str) -> LoadedErrorModel:
    loaded = load_active_model(artifact_directory)
    if loaded.artifact_version != artifact_version or loaded.feature_schema_sha256 != feature_schema_sha256:
        raise ValueError("Active XGBoost artifact does not match the frozen evaluation contract")
    return loaded


def infer_expected_errors(loaded: LoadedErrorModel, *, forecasts: Mapping[str, float], district_id: str, region: str, month: int, floor_mm: float) -> tuple[dict[str, float], dict[str, float]]:
    rows = [make_features(source_id=source, forecasts=forecasts, district_id=district_id, region=region, month=month, metadata=loaded.metadata) for source in loaded.metadata["source_ids"]]
    raw = loaded.model.predict(np.vstack(rows))
    errors = {source: max(float(value), floor_mm) for source, value in zip(loaded.metadata["source_ids"], raw)}
    return errors, expected_error_weights(errors, floor_mm)
