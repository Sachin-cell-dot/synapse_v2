from __future__ import annotations

import hashlib
import math
import sqlite3
import uuid
from collections import defaultdict
from contextlib import closing
from datetime import datetime, time, timedelta, timezone
from math import fsum
from zoneinfo import ZoneInfo

from .blend import blend_forecasts
from ..models.xgboost_blend import infer_expected_errors, load_active_model, load_frozen_model
from ..utils.config import OperationalConfig
from ..preprocessing.geography import District, load_districts, sample_district
from ..ingestion.open_meteo import PointForecast, fetch_points
from ..ingestion.open_meteo_ensemble import EnsemblePointForecast, fetch_ensemble_points
from ..utils.store import hierarchical_historical_errors, initialize_database, write_cycle


def _target_date(issue_local: datetime, lead: int, start_day_offset: int) -> object:
    if lead < start_day_offset:
        raise ValueError("lead cannot precede the configured target start day")
    return issue_local.date() + timedelta(days=lead)


def _select_district(config: OperationalConfig, selector: str) -> District:
    geography = config.data["geography"]
    districts = load_districts(config.resolve(geography["boundary_path"]), geography)
    matches = [district for district in districts if district.district_id.casefold() == selector.casefold() or district.name.casefold() == selector.casefold()]
    if len(matches) != 1:
        raise ValueError(f"District selector matched {len(matches)} districts")
    return matches[0]


def _daily_point_values(point: PointForecast, timezone_name: str, accumulation_start_hour: int, required_hours: int) -> dict:
    zone = ZoneInfo(timezone_name)
    values_by_day: dict = defaultdict(dict)
    for timestamp, value in zip(point.times, point.precipitation):
        local = datetime.fromisoformat(timestamp)
        if local.tzinfo is None:
            local = local.replace(tzinfo=zone)
        else:
            local = local.astimezone(zone)
        day = (local - timedelta(hours=accumulation_start_hour)).date()
        values_by_day[day].setdefault(local, []).append(value)
    result = {}
    for day, hourly in values_by_day.items():
        distinct_hours = len(hourly)
        window_start = datetime.combine(day, time(hour=accumulation_start_hour), zone)
        expected = {window_start + timedelta(hours=offset) for offset in range(required_hours)}
        complete = set(hourly) == expected and all(len(values) == 1 and values[0] is not None for values in hourly.values())
        result[day] = {"value": fsum(values[0] for values in hourly.values()) if complete else None, "distinct_hours": distinct_hours, "complete": complete}
    return result


def circular_mean_degrees(values: list[float], weights: list[float] | None = None) -> float | None:
    """Return a vector/circular mean; 0/360 cancellation is handled correctly."""
    if not values:
        return None
    weights = weights or [1.0] * len(values)
    if len(weights) != len(values) or not any(weight > 0 for weight in weights):
        return None
    east = fsum(weight * math.sin(math.radians(value)) for value, weight in zip(values, weights))
    north = fsum(weight * math.cos(math.radians(value)) for value, weight in zip(values, weights))
    if math.isclose(east, 0.0, abs_tol=1e-12) and math.isclose(north, 0.0, abs_tol=1e-12):
        return None
    return math.degrees(math.atan2(east, north)) % 360.0


def _daily_point_weather(point: PointForecast, timezone_name: str, accumulation_start_hour: int, required_hours: int) -> dict:
    zone = ZoneInfo(timezone_name)
    by_day: dict = defaultdict(dict)
    series = {
        "temperature_2m": point.temperature_2m,
        "wind_speed_10m": point.wind_speed_10m,
        "wind_direction_10m": point.wind_direction_10m,
    }
    for index, timestamp in enumerate(point.times):
        local = datetime.fromisoformat(timestamp)
        local = local.replace(tzinfo=zone) if local.tzinfo is None else local.astimezone(zone)
        day = (local - timedelta(hours=accumulation_start_hour)).date()
        by_day[day][local] = {name: values[index] if index < len(values) else None for name, values in series.items()}
    output = {}
    for day, hourly in by_day.items():
        expected = {datetime.combine(day, time(hour=accumulation_start_hour), zone) + timedelta(hours=offset) for offset in range(required_hours)}
        aligned = set(hourly) == expected
        result = {"distinct_hours": len(hourly)}
        for variable in ("temperature_2m", "wind_speed_10m"):
            values = [row[variable] for row in hourly.values()]
            complete = aligned and len(values) == required_hours and all(value is not None for value in values)
            result[variable] = {"complete": complete, "minimum": min(values) if complete else None, "maximum": max(values) if complete else None}
        directions = [row["wind_direction_10m"] for row in hourly.values()]
        speeds = [row["wind_speed_10m"] for row in hourly.values()]
        complete_direction = aligned and len(directions) == required_hours and all(value is not None for value in directions) and all(value is not None for value in speeds)
        result["wind_direction_10m"] = {"complete": complete_direction, "mean": circular_mean_degrees(directions, speeds) if complete_direction else None}
        output[day] = result
    return output


def _aggregate_weather(points: tuple[PointForecast, ...], *, issue_local: datetime, lead_days: tuple[int, ...], minimum_coverage: float, timezone_name: str, accumulation_start_hour: int, target_start_day_offset: int, required_hours: int, expected_units: dict[str, str]) -> dict[int, dict]:
    daily = [_daily_point_weather(point, timezone_name, accumulation_start_hour, required_hours) for point in points]
    point_weights = [math.cos(math.radians(point.latitude)) for point in points]
    statistics = (("temperature_2m", "maximum"), ("temperature_2m", "minimum"), ("wind_speed_10m", "maximum"), ("wind_direction_10m", "mean"))
    result = {}
    for lead in lead_days:
        target = _target_date(issue_local, lead, target_start_day_offset)
        lead_result = {}
        for variable, statistic in statistics:
            unit_ok = [point.hourly_units is not None and point.hourly_units.get(variable) == expected_units[variable] for point in points]
            available = []
            for values, weight, valid_unit in zip(daily, point_weights, unit_ok):
                item = values.get(target, {}).get(variable, {})
                if valid_unit and item.get("complete") and item.get(statistic) is not None:
                    available.append((float(item[statistic]), weight))
            required_points = math.ceil(minimum_coverage * len(points))
            complete = bool(points) and len(available) >= required_points
            if complete and variable == "wind_direction_10m":
                value = circular_mean_degrees([item[0] for item in available], [item[1] for item in available])
                complete = value is not None
            else:
                value = fsum(value * weight for value, weight in available) / fsum(weight for _, weight in available) if complete else None
            unsupported = bool(points) and not any(unit_ok)
            lead_result[(variable, statistic)] = {
                "value": value, "unit": expected_units[variable],
                "hourly_completeness": min((values.get(target, {}).get("distinct_hours", 0) for values in daily), default=0) / required_hours,
                "complete_point_count": len(available), "required_point_count": required_points,
                "point_coverage": len(available) / len(points) if points else 0.0,
                "status": "complete" if complete else "unsupported:missing_field_or_unit" if unsupported else "incomplete",
            }
        result[lead] = lead_result
    return result


def _aggregate_source(points: tuple[PointForecast, ...], *, issue_local: datetime, lead_days: tuple[int, ...], minimum_coverage: float, timezone_name: str, accumulation_start_hour: int, target_start_day_offset: int, required_hours: int) -> dict[int, dict]:
    daily = [_daily_point_values(point, timezone_name, accumulation_start_hour, required_hours) for point in points]
    point_weights = [math.cos(math.radians(point.latitude)) for point in points]
    result = {}
    for lead in lead_days:
        valid_date = _target_date(issue_local, lead, target_start_day_offset)
        available = [(values[valid_date]["value"], weight) for values, weight in zip(daily, point_weights) if valid_date in values and values[valid_date]["complete"]]
        coverage = len(available) / len(points) if points else 0.0
        required_point_count = math.ceil(minimum_coverage * len(points))
        complete = bool(points) and len(available) >= required_point_count
        result[lead] = {
            "value": fsum(value * weight for value, weight in available) / fsum(weight for _, weight in available) if complete else None,
            "hourly_completeness": min((values.get(valid_date, {}).get("distinct_hours", 0) for values in daily), default=0) / required_hours,
            "complete_point_count": len(available), "required_point_count": required_point_count,
            "point_coverage": coverage, "status": "complete" if complete else "incomplete",
        }
    return result


def _aggregate_ensemble(points: tuple[EnsemblePointForecast, ...], *, issue_local: datetime, lead_days: tuple[int, ...], minimum_coverage: float, timezone_name: str, accumulation_start_hour: int, target_start_day_offset: int, required_hours: int) -> dict[int, dict]:
    zone = ZoneInfo(timezone_name); per_point = []
    for point in points:
        hourly = defaultdict(dict)
        for timestamp, mean, spread in zip(point.times, point.mean, point.spread):
            local = datetime.fromisoformat(timestamp); local = local.replace(tzinfo=zone) if local.tzinfo is None else local.astimezone(zone)
            day = (local - timedelta(hours=accumulation_start_hour)).date()
            hourly[day].setdefault(local, []).append((mean, spread))
        daily = {}
        for day, values in hourly.items():
            expected = {datetime.combine(day, time(hour=accumulation_start_hour), zone) + timedelta(hours=offset) for offset in range(required_hours)}
            complete = set(values) == expected and all(len(pair) == 1 and pair[0][0] is not None and pair[0][1] is not None for pair in values.values())
            daily[day] = {"mean": fsum(pair[0][0] for pair in values.values()) if complete else None, "spread": math.sqrt(fsum(pair[0][1] ** 2 for pair in values.values())) if complete else None, "hours": len(values), "complete": complete}
        per_point.append(daily)
    weights = [math.cos(math.radians(point.latitude)) for point in points]; result = {}
    for lead in lead_days:
        target = _target_date(issue_local, lead, target_start_day_offset)
        available = [(daily[target], weight) for daily, weight in zip(per_point, weights) if target in daily and daily[target]["complete"]]
        required_points = math.ceil(minimum_coverage * len(points)); complete = bool(points) and len(available) >= required_points
        denominator = fsum(weight for _, weight in available) if available else 0
        result[lead] = {"value": fsum(item["mean"] * weight for item, weight in available) / denominator if complete else None, "spread": fsum(item["spread"] * weight for item, weight in available) / denominator if complete else None, "hourly_completeness": min((daily.get(target, {}).get("hours", 0) for daily in per_point), default=0) / required_hours, "complete_point_count": len(available), "required_point_count": required_points, "point_coverage": len(available) / len(points) if points else 0.0, "status": "complete" if complete else "incomplete"}
    return result


def _run_cycle(config: OperationalConfig, districts: tuple[District, ...]) -> dict:
    forecast_config = config.data["forecast"]
    blend_config = config.data["blend"]
    geography_config = config.data["geography"]
    zone = ZoneInfo(forecast_config["source_timezone"])
    retrieved_at = datetime.now(timezone.utc)
    issue_local = retrieved_at.astimezone(zone)
    issue_at = retrieved_at
    cycle_id = uuid.uuid4().hex
    district_points = {district.district_id: sample_district(district, geography_config["sampling"]) for district in districts}
    raw_directory = config.resolve(config.data["storage"]["raw_response_directory"])
    cache_directory = config.resolve(forecast_config["request_cache_directory"])
    database_path = config.resolve(config.data["storage"]["database_path"])
    initialize_database(database_path)
    xgb_settings = config.data["xgboost"]
    loaded_xgb = None
    xgb_load_error = None
    if xgb_settings["enabled"]:
        try:
            loaded_xgb = load_frozen_model(config.resolve(xgb_settings["artifact_directory"]), artifact_version=xgb_settings["frozen_artifact_version"], feature_schema_sha256=xgb_settings["frozen_feature_schema_sha256"]) if xgb_settings["training_frozen"] else load_active_model(config.resolve(xgb_settings["artifact_directory"]))
        except (FileNotFoundError, ValueError, OSError) as error:
            xgb_load_error = type(error).__name__
    source_rows = []
    source_variable_rows = []
    variable_values = {district.district_id: {lead: defaultdict(dict) for lead in forecast_config["lead_days"]} for district in districts}
    per_district_forecasts = {
        district.district_id: {lead: {} for lead in forecast_config["lead_days"]}
        for district in districts
    }
    for source in config.enabled_sources:
        batch_size = int(forecast_config["coordinate_batch_size"])
        flattened = [(district.district_id, point) for district in districts for point in district_points[district.district_id]]
        results_by_district: dict[str, list[PointForecast]] = defaultdict(list)
        for start in range(0, len(flattened), batch_size):
            batch = flattened[start:start + batch_size]
            results = fetch_points(source=source, coordinates=tuple(point for _, point in batch), forecast=forecast_config, raw_directory=raw_directory, cache_directory=cache_directory)
            for (district_id, _), result in zip(batch, results):
                results_by_district[district_id].append(result)
        for district in districts:
            point_results = tuple(results_by_district[district.district_id])
            aggregates = _aggregate_source(
                point_results,
                issue_local=issue_local,
                lead_days=tuple(forecast_config["lead_days"]),
                minimum_coverage=float(geography_config["sampling"]["minimum_coverage_fraction"]),
                timezone_name=forecast_config["source_timezone"],
                accumulation_start_hour=int(forecast_config["daily_accumulation_start_hour"]),
                target_start_day_offset=int(forecast_config["target_start_day_offset"]),
                required_hours=int(forecast_config["required_hours_per_day"]),
            )
            provenance_digest = hashlib.sha256("".join(sorted({point.response_sha256 for point in point_results})).encode()).hexdigest()
            weather = _aggregate_weather(
                point_results, issue_local=issue_local, lead_days=tuple(forecast_config["lead_days"]),
                minimum_coverage=float(geography_config["sampling"]["minimum_coverage_fraction"]),
                timezone_name=forecast_config["source_timezone"], accumulation_start_hour=int(forecast_config["daily_accumulation_start_hour"]),
                target_start_day_offset=int(forecast_config["target_start_day_offset"]), required_hours=int(forecast_config["required_hours_per_day"]),
                expected_units=forecast_config["variable_units"],
            )
            for lead, aggregate in aggregates.items():
                value = aggregate["value"]
                valid_date = _target_date(issue_local, lead, int(forecast_config["target_start_day_offset"]))
                valid_start_local = datetime.combine(valid_date, time(hour=int(forecast_config["daily_accumulation_start_hour"])), zone)
                valid_end_local = valid_start_local + timedelta(days=1)
                per_district_forecasts[district.district_id][lead][source["id"]] = value
                source_rows.append({
                    "cycle_id": cycle_id, "district_id": district.district_id,
                    "source_id": source["id"], "requested_model_id": source["api_model"],
                    "source_class": source["source_class"], "blend_role": "locked_blend_source",
                    "retrieved_at_utc": max(point.retrieved_at_utc for point in point_results),
                    "issue_time_utc": issue_at.isoformat(),
                    "upstream_run_time_utc": None, "run_identity_status": "not_exposed_by_latest_endpoint_stitched_data_possible",
                    "valid_start_utc": valid_start_local.astimezone(timezone.utc).isoformat(),
                    "valid_end_utc": valid_end_local.astimezone(timezone.utc).isoformat(),
                    "lead_days": lead, "unit": forecast_config["unit"], "precipitation_mm": value,
                    "hourly_completeness": aggregate["hourly_completeness"],
                    "complete_point_count": aggregate["complete_point_count"], "required_point_count": aggregate["required_point_count"],
                    "point_coverage": aggregate["point_coverage"], "status": aggregate["status"],
                    "raw_response_sha256": provenance_digest,
                })
                for (variable_id, statistic), item in weather[lead].items():
                    variable_values[district.district_id][lead][(variable_id, statistic)][source["id"]] = item["value"]
                    source_variable_rows.append({
                        "cycle_id": cycle_id, "district_id": district.district_id, "source_id": source["id"],
                        "requested_model_id": source["api_model"], "variable_id": variable_id, "statistic": statistic,
                        "retrieved_at_utc": max(point.retrieved_at_utc for point in point_results), "issue_time_utc": issue_at.isoformat(),
                        "upstream_run_time_utc": None, "run_identity_status": "not_exposed_by_latest_endpoint_stitched_data_possible",
                        "valid_start_utc": valid_start_local.astimezone(timezone.utc).isoformat(), "valid_end_utc": valid_end_local.astimezone(timezone.utc).isoformat(),
                        "lead_days": lead, "unit": item["unit"], "value": item["value"],
                        "hourly_completeness": item["hourly_completeness"], "complete_point_count": item["complete_point_count"],
                        "required_point_count": item["required_point_count"], "point_coverage": item["point_coverage"],
                        "status": item["status"], "raw_response_sha256": provenance_digest,
                    })
    ensemble = config.data["ensemble"]
    if ensemble["enabled"]:
        flattened = [(district.district_id, point) for district in districts for point in district_points[district.district_id]]
        ensemble_by_district: dict[str, list[EnsemblePointForecast]] = defaultdict(list); ensemble_error = None
        try:
            for start in range(0, len(flattened), int(forecast_config["coordinate_batch_size"])):
                batch = flattened[start:start + int(forecast_config["coordinate_batch_size"])]
                results = fetch_ensemble_points(source=ensemble, coordinates=tuple(point for _, point in batch), forecast=forecast_config, raw_directory=raw_directory / "ensemble", cache_directory=cache_directory / "ensemble")
                for (district_id, _), result in zip(batch, results): ensemble_by_district[district_id].append(result)
        except (RuntimeError, ValueError, OSError) as error:
            ensemble_error = type(error).__name__
        for district in districts:
            points = tuple(ensemble_by_district[district.district_id])
            aggregates = _aggregate_ensemble(points, issue_local=issue_local, lead_days=tuple(forecast_config["lead_days"]), minimum_coverage=float(geography_config["sampling"]["minimum_coverage_fraction"]), timezone_name=forecast_config["source_timezone"], accumulation_start_hour=int(forecast_config["daily_accumulation_start_hour"]), target_start_day_offset=int(forecast_config["target_start_day_offset"]), required_hours=int(forecast_config["required_hours_per_day"])) if points else {lead: {"value": None, "spread": None, "hourly_completeness": 0.0, "complete_point_count": 0, "required_point_count": math.ceil(float(geography_config["sampling"]["minimum_coverage_fraction"]) * len(district_points[district.district_id])), "point_coverage": 0.0, "status": f"unavailable:{ensemble_error or 'no_points'}"} for lead in forecast_config["lead_days"]}
            digest = hashlib.sha256("".join(sorted({point.response_sha256 for point in points})).encode()).hexdigest()
            for lead, aggregate in aggregates.items():
                valid_date = _target_date(issue_local, lead, int(forecast_config["target_start_day_offset"])); valid_start_local = datetime.combine(valid_date, time(hour=int(forecast_config["daily_accumulation_start_hour"])), zone); valid_end_local = valid_start_local + timedelta(days=1)
                source_rows.append({"cycle_id": cycle_id, "district_id": district.district_id, "source_id": ensemble["id"], "source_class": ensemble["source_class"], "blend_role": ensemble["blend_role"], "requested_model_id": ensemble["api_model"], "retrieved_at_utc": max((point.retrieved_at_utc for point in points), default=retrieved_at.isoformat()), "issue_time_utc": issue_at.isoformat(), "upstream_run_time_utc": None, "run_identity_status": "not_exposed_by_latest_ensemble_mean_endpoint", "valid_start_utc": valid_start_local.astimezone(timezone.utc).isoformat(), "valid_end_utc": valid_end_local.astimezone(timezone.utc).isoformat(), "lead_days": lead, "unit": forecast_config["unit"], "precipitation_mm": aggregate["value"], "ensemble_spread_mm": aggregate["spread"], "spread_semantics": ensemble["spread_aggregation"], "hourly_completeness": aggregate["hourly_completeness"], "complete_point_count": aggregate["complete_point_count"], "required_point_count": aggregate["required_point_count"], "point_coverage": aggregate["point_coverage"], "status": aggregate["status"], "raw_response_sha256": digest})
    blend_rows = []
    statewide_ids = tuple(district.district_id for district in districts)
    regional_ids = {district.district_id: tuple(peer.district_id for peer in districts if peer.division == district.division) for district in districts}
    with closing(sqlite3.connect(database_path)) as connection:
        for district in districts:
            for lead, forecasts in per_district_forecasts[district.district_id].items():
                valid_date = _target_date(issue_local, lead, int(forecast_config["target_start_day_offset"]))
                valid_start_local = datetime.combine(valid_date, time(hour=int(forecast_config["daily_accumulation_start_hour"])), zone)
                valid_end_local = valid_start_local + timedelta(days=1)
                errors, hierarchy_fallback = hierarchical_historical_errors(
                    connection, district_id=district.district_id, region_district_ids=regional_ids[district.district_id], statewide_district_ids=statewide_ids, lead_days=lead,
                    source_ids=tuple(forecasts), available_before_utc=retrieved_at.isoformat(),
                    limit=int(blend_config["rolling_window_days"]),
                )
                blend = blend_forecasts(
                    forecasts, errors, power=float(blend_config["inverse_mae_power"]),
                    mae_floor=float(blend_config["mae_floor_mm"]),
                    minimum_sources=int(blend_config["minimum_sources_to_publish"]),
                )
                method = "equal_weight_baseline" if blend.fallback == "equal_weight_no_complete_history" else "inverse_mae_baseline"
                fallback = blend.fallback or hierarchy_fallback
                predicted_errors = {}
                artifact_version = feature_schema_sha256 = None
                if lead == int(xgb_settings["lead_days"]) and loaded_xgb is not None and tuple(forecasts) == tuple(xgb_settings["source_ids"]) and all(value is not None for value in forecasts.values()):
                    predicted_errors, weights = infer_expected_errors(
                        loaded_xgb, forecasts={source: float(forecasts[source]) for source in xgb_settings["source_ids"]},
                        district_id=district.district_id, region=district.division or "unassigned", month=valid_date.month,
                        floor_mm=float(xgb_settings["predicted_error_floor_mm"]),
                    )
                    blend_forecast = fsum(float(forecasts[source]) * weights[source] for source in weights)
                    blend = type(blend)(max(0.0, blend_forecast), weights, blend.status, None)
                    method, fallback = "xgboost_expected_absolute_error", None
                    artifact_version, feature_schema_sha256 = loaded_xgb.artifact_version, loaded_xgb.feature_schema_sha256
                elif lead == int(xgb_settings["lead_days"]) and xgb_settings["enabled"]:
                    fallback = f"{method}_fallback:{xgb_load_error or 'incomplete_source_contract'}"
                blend_rows.append({
                    "cycle_id": cycle_id, "district_id": district.district_id,
                    "valid_start_utc": valid_start_local.astimezone(timezone.utc).isoformat(),
                    "valid_end_utc": valid_end_local.astimezone(timezone.utc).isoformat(),
                    "lead_days": lead, "forecast_mm": blend.forecast_mm,
                    "status": blend.status, "fallback": fallback, "method": method,
                    "predicted_errors": predicted_errors, "artifact_version": artifact_version,
                    "feature_schema_sha256": feature_schema_sha256,
                    "weights": blend.weights, "issued_at_utc": retrieved_at.isoformat(),
                })
    variable_blend_rows = []
    for district in districts:
        for lead in forecast_config["lead_days"]:
            valid_date = _target_date(issue_local, lead, int(forecast_config["target_start_day_offset"]))
            valid_start_local = datetime.combine(valid_date, time(hour=int(forecast_config["daily_accumulation_start_hour"])), zone)
            valid_end_local = valid_start_local + timedelta(days=1)
            for (variable_id, statistic), forecasts in variable_values[district.district_id][lead].items():
                complete = {source: value for source, value in forecasts.items() if value is not None}
                if len(complete) == len(config.enabled_sources):
                    if variable_id == "wind_direction_10m":
                        blended = circular_mean_degrees(list(complete.values()))
                    else:
                        blended = fsum(complete.values()) / len(complete)
                    status, fallback = "complete", None
                else:
                    blended, status, fallback = None, "incomplete", "requires_all_three_sources"
                variable_blend_rows.append({
                    "cycle_id": cycle_id, "district_id": district.district_id, "valid_start_utc": valid_start_local.astimezone(timezone.utc).isoformat(),
                    "valid_end_utc": valid_end_local.astimezone(timezone.utc).isoformat(), "lead_days": lead,
                    "variable_id": variable_id, "statistic": statistic, "unit": forecast_config["variable_units"][variable_id],
                    "value": blended, "status": status, "method": "equal_weight_experimental_forecast_only",
                    "blend_eligibility": "forecast_only_no_statewide_district_observations", "fallback": fallback,
                    "weights": {source: 1 / len(complete) for source in complete} if complete else {}, "issued_at_utc": retrieved_at.isoformat(),
                })
    cycle = {"cycle_id": cycle_id, "retrieved_at_utc": retrieved_at.isoformat(), "configuration_sha256": config.sha256, "mode": config.data["project"]["mode"], "created_at_utc": retrieved_at.isoformat()}
    write_cycle(path=database_path, cycle=cycle, source_rows=source_rows, blend_rows=blend_rows, source_variable_rows=source_variable_rows, variable_blend_rows=variable_blend_rows)
    summary = {
        "cycle_id": cycle_id,
        "issued_at_utc": retrieved_at.isoformat(),
        "districts": len(districts),
        "sampling_points": sum(len(points) for points in district_points.values()),
        "source_rows": len(source_rows),
        "source_variable_rows": len(source_variable_rows), "variable_blend_rows": len(variable_blend_rows),
        "blend_rows": len(blend_rows),
        "complete_blends": sum(row["status"] == "complete" for row in blend_rows),
        "degraded_blends": sum(row["status"] == "degraded" for row in blend_rows),
        "insufficient_blends": sum(row["status"] == "insufficient_sources" for row in blend_rows),
    }
    if len(districts) == 1:
        summary.update({"district_id": districts[0].district_id, "district": districts[0].name, "blends": blend_rows})
    return summary


def run_district_cycle(config: OperationalConfig, district_selector: str) -> dict:
    return _run_cycle(config, (_select_district(config, district_selector),))


def run_statewide_cycle(config: OperationalConfig) -> dict:
    geography = config.data["geography"]
    districts = load_districts(config.resolve(geography["boundary_path"]), geography)
    return _run_cycle(config, districts)
