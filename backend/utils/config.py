from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigurationError(ValueError):
    """Raised when operational configuration is incomplete or inconsistent."""


@dataclass(frozen=True)
class OperationalConfig:
    path: Path
    root: Path
    data: dict[str, Any]
    sha256: str

    def resolve(self, configured_path: str) -> Path:
        candidate = Path(configured_path)
        return candidate if candidate.is_absolute() else self.root / candidate

    @property
    def enabled_sources(self) -> tuple[dict[str, Any], ...]:
        return tuple(source for source in self.data["forecast"]["sources"] if source["enabled"])


def _require(mapping: dict[str, Any], keys: tuple[str, ...], context: str) -> None:
    missing = [key for key in keys if key not in mapping]
    if missing:
        raise ConfigurationError(f"Missing {context} settings: {', '.join(missing)}")


def load_config(path: Path) -> OperationalConfig:
    path = path.resolve()
    raw = path.read_bytes()
    data = json.loads(raw)
    _require(data, ("schema_version", "project", "storage", "geography", "forecast", "ensemble", "blend", "routine", "xgboost", "verification", "imd_realtime", "historical_bootstrap", "archive", "synop_forecast_archive"), "top-level")
    _require(data["storage"], ("database_path", "raw_response_directory", "export_directory"), "storage")
    if data["schema_version"] != 1:
        raise ConfigurationError(f"Unsupported schema_version: {data['schema_version']}")
    _require(data["forecast"], ("api_base_url", "hourly_variable", "unit", "source_timezone", "daily_accumulation_start_hour", "target_start_day_offset", "required_hours_per_day", "lead_days", "sources", "request_timeout_seconds", "request_attempts", "retry_backoff_seconds", "maximum_retry_wait_seconds", "request_interval_seconds", "request_cache_ttl_minutes", "request_cache_directory", "coordinate_batch_size", "user_agent"), "forecast")
    _require(data["blend"], ("rolling_window_days", "inverse_mae_power", "mae_floor_mm", "minimum_sources_to_publish"), "blend")
    sources = data["forecast"]["sources"]
    if not sources:
        raise ConfigurationError("At least one forecast source must be declared")
    ids = [source.get("id") for source in sources]
    if any(not source_id for source_id in ids) or len(ids) != len(set(ids)):
        raise ConfigurationError("Forecast source ids must be present and unique")
    for source in sources:
        _require(source, ("id", "label", "provider", "api_model", "source_class", "enabled"), f"source {source.get('id', '<unknown>')}")
    leads = data["forecast"]["lead_days"]
    if not leads or any(not isinstance(day, int) or day < 0 for day in leads) or len(leads) != len(set(leads)):
        raise ConfigurationError("lead_days must contain unique non-negative integers")
    if data["forecast"]["target_start_day_offset"] < 1:
        raise ConfigurationError("target_start_day_offset must be at least 1 so the default target is a future complete day")
    if min(leads) < data["forecast"]["target_start_day_offset"]:
        raise ConfigurationError("lead_days cannot precede target_start_day_offset")
    if data["forecast"]["required_hours_per_day"] != 24:
        raise ConfigurationError("rainfall daily totals require exactly 24 distinct hourly values")
    _require(data["forecast"], ("additional_hourly_variables", "wind_speed_unit", "variable_units"), "forecast")
    expected_variables = {"temperature_2m": "°C", "wind_speed_10m": "m/s", "wind_direction_10m": "°"}
    if set(data["forecast"]["additional_hourly_variables"]) != set(expected_variables) or data["forecast"]["variable_units"] != expected_variables or data["forecast"]["wind_speed_unit"] != "ms":
        raise ConfigurationError("Temperature/wind contract requires temperature_2m °C and 10 m wind in m/s/degrees")
    if data["forecast"]["request_timeout_seconds"] <= 0 or data["forecast"]["request_attempts"] <= 0 or data["forecast"]["retry_backoff_seconds"] < 0 or data["forecast"]["maximum_retry_wait_seconds"] <= 0 or data["forecast"]["request_interval_seconds"] < 0 or data["forecast"]["request_cache_ttl_minutes"] <= 0:
        raise ConfigurationError("Request timeout and attempts must be positive; retry backoff cannot be negative")
    if not isinstance(data["forecast"]["coordinate_batch_size"], int) or data["forecast"]["coordinate_batch_size"] <= 0:
        raise ConfigurationError("coordinate_batch_size must be a positive integer")
    if data["blend"]["rolling_window_days"] <= 0 or data["blend"]["inverse_mae_power"] <= 0 or data["blend"]["mae_floor_mm"] <= 0:
        raise ConfigurationError("Blending window, power, and MAE floor must be positive")
    enabled = [source for source in sources if source["enabled"]]
    if data["blend"]["minimum_sources_to_publish"] > len(enabled):
        raise ConfigurationError("minimum_sources_to_publish exceeds enabled source count")
    routine = data["routine"]
    _require(routine, ("issue_hours_ist", "expected_district_count", "health_log_path"), "routine")
    if not routine["issue_hours_ist"] or len(set(routine["issue_hours_ist"])) != len(routine["issue_hours_ist"]) or any(not isinstance(hour, int) or hour < 0 or hour > 23 for hour in routine["issue_hours_ist"]):
        raise ConfigurationError("routine.issue_hours_ist must contain unique IST hours from 0 to 23")
    if routine["expected_district_count"] <= 0 or not routine["health_log_path"]:
        raise ConfigurationError("Routine district count and health log path are required")
    station_archive = data["synop_forecast_archive"]
    _require(station_archive, ("enabled", "stations"), "synop_forecast_archive")
    stations = station_archive["stations"]
    station_ids = [str(station.get("station_id", "")) for station in stations]
    if station_archive["enabled"] and not stations:
        raise ConfigurationError("Enabled SYNOP forecast archive requires at least one station")
    if any(not station_id for station_id in station_ids) or len(station_ids) != len(set(station_ids)):
        raise ConfigurationError("SYNOP archive station ids must be present and unique")
    for station in stations:
        _require(station, ("station_id", "name", "latitude", "longitude"), f"SYNOP station {station.get('station_id', '<unknown>')}")
        if not -90 <= float(station["latitude"]) <= 90 or not -180 <= float(station["longitude"]) <= 180:
            raise ConfigurationError(f"Invalid coordinates for SYNOP station {station['station_id']}")
    xgb = data["xgboost"]
    _require(xgb, ("enabled", "training_frozen", "frozen_artifact_version", "frozen_feature_schema_sha256", "lead_days", "source_ids", "artifact_directory", "predicted_error_floor_mm", "random_seed", "train_start", "train_end", "validation_start", "validation_end", "held_out_start", "held_out_end", "parameter_candidates"), "xgboost")
    if tuple(xgb["source_ids"]) != tuple(source["id"] for source in enabled):
        raise ConfigurationError("xgboost.source_ids must exactly match enabled forecast sources in order")
    if xgb["lead_days"] != 1 or xgb["lead_days"] not in leads or xgb["predicted_error_floor_mm"] <= 0:
        raise ConfigurationError("XGBoost MVP must target configured Day 1 with a positive predicted-error floor")
    if not (xgb["train_start"] <= xgb["train_end"] < xgb["validation_start"] <= xgb["validation_end"] < xgb["held_out_start"] <= xgb["held_out_end"]):
        raise ConfigurationError("XGBoost train, validation, and held-out periods must be ordered and disjoint")
    if not xgb["parameter_candidates"]:
        raise ConfigurationError("At least one XGBoost parameter candidate is required")
    if xgb["training_frozen"] and (not xgb["frozen_artifact_version"] or not xgb["frozen_feature_schema_sha256"]):
        raise ConfigurationError("Frozen XGBoost training requires artifact and feature-schema identifiers")
    ensemble = data["ensemble"]
    _require(ensemble, ("enabled", "id", "label", "provider", "api_base_url", "api_model", "source_class", "blend_role", "mean_variable", "spread_variable", "spread_aggregation"), "ensemble")
    if ensemble["source_class"] != "ensemble" or ensemble["blend_role"] != "display_only_not_in_locked_blend":
        raise ConfigurationError("Ensemble must remain display-only and outside the locked blend")
    if ensemble["id"] in ids or ensemble["id"] in xgb["source_ids"]:
        raise ConfigurationError("Ensemble source id must be separate from the locked three-source contract")
    bootstrap = data["historical_bootstrap"]
    _require(bootstrap, ("path", "date_column", "district_id_column", "verification_column", "verification_provider", "verification_classification", "verification_availability_lag_hours", "lead_days", "source_columns", "eligible_start_date", "eligible_end_date", "expected_district_count", "coverage_status_columns", "required_coverage_status"), "historical_bootstrap")
    unknown_bootstrap_sources = set(bootstrap["source_columns"]) - set(ids)
    if unknown_bootstrap_sources:
        raise ConfigurationError(f"Historical bootstrap references unknown sources: {sorted(unknown_bootstrap_sources)}")
    archive = data["archive"]
    _require(archive, ("previous_runs_api_base_url", "single_runs_api_base_url", "raw_response_directory", "request_cache_directory", "skill_classification", "reconstruction_mode", "reconstruction_lead_days", "run_cycle_hours_utc", "run_availability_lag_hours", "run_identity_status", "single_run_forecast_days"), "archive")
    if archive["reconstruction_lead_days"] not in leads:
        raise ConfigurationError("archive reconstruction_lead_days must be a configured forecast lead")
    run_hours = archive["run_cycle_hours_utc"]
    if not run_hours or len(run_hours) != len(set(run_hours)) or any(not isinstance(hour, int) or hour < 0 or hour > 23 for hour in run_hours):
        raise ConfigurationError("archive run_cycle_hours_utc must contain unique UTC hours from 0 to 23")
    if archive["run_availability_lag_hours"] < 0 or archive["single_run_forecast_days"] <= 0:
        raise ConfigurationError("archive run availability lag cannot be negative and forecast days must be positive")
    verification = data["verification"]
    _require(verification, ("provider", "classification", "unit", "date_column", "district_id_column", "value_column", "availability_time_column", "reject_unknown_districts"), "verification")
    imd = data["imd_realtime"]
    _require(imd, ("provider", "product", "download_url", "request_field", "request_date_format", "latest_available_day_offset", "user_agent", "request_timeout_seconds", "request_attempts", "retry_backoff_seconds", "raw_directory", "district_csv_directory", "grid"), "imd_realtime")
    _require(imd["grid"], ("longitude_count", "latitude_count", "longitude_start", "latitude_start", "spacing_degrees", "value_bytes", "byte_order", "missing_value", "minimum_valid_coverage_fraction"), "imd_realtime.grid")
    if not isinstance(imd["latest_available_day_offset"], int) or imd["latest_available_day_offset"] < 0:
        raise ConfigurationError("imd_realtime.latest_available_day_offset must be a non-negative integer")
    root = path.parent.parent if path.parent.name in {"config", "configs"} else path.parent
    return OperationalConfig(path=path, root=root, data=data, sha256=hashlib.sha256(raw).hexdigest())
