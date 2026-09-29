"""Prepare audited dashboard data for the frozen historical-hindcast MVP."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs"
FORECAST_INPUT = OUT / "synapse_wx_final_hindcast_forecast.csv"
METRICS_INPUT = OUT / "synapse_wx_final_test_detailed_metrics.csv"
BOUNDARY_INPUT = ROOT / "datasets" / "adapt_wx_karnataka_district_boundaries.geojson"
FORECAST_OUTPUT = OUT / "synapse_wx_dashboard_forecasts.csv"
GEOJSON_OUTPUT = OUT / "synapse_wx_dashboard_districts.geojson"
AUDIT_OUTPUT = OUT / "synapse_wx_dashboard_data_audit.json"
WEIGHT_TOLERANCE = 1e-12

FORECAST_COLUMNS = [
    "date", "district_code", "district", "division",
    "gfs_forecast_mm", "ifs_hres_forecast_mm", "aifs_forecast_mm",
    "weight_gfs", "weight_ifs_hres", "weight_aifs",
    "synapse_wx_forecast_mm", "rainfall_category", "confidence_level",
    "model_agreement_mm", "trust_explanation", "imd_actual_mm",
    "absolute_error_mm", "data_mode",
]
REQUIRED_NON_NULL = [
    "date", "district_code", "district", "division",
    "gfs_forecast_mm", "ifs_hres_forecast_mm", "aifs_forecast_mm",
    "weight_gfs", "weight_ifs_hres", "weight_aifs",
    "synapse_wx_forecast_mm", "rainfall_category", "confidence_level",
    "model_agreement_mm", "trust_explanation",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(name, passed, actual, expected):
    return {"name": name, "passed": bool(passed), "actual": actual, "expected": expected}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for path in (FORECAST_INPUT, METRICS_INPUT, BOUNDARY_INPUT):
        if not path.is_file():
            raise FileNotFoundError(path)

    source = pd.read_csv(FORECAST_INPUT, dtype={"district_code": int})
    metrics = pd.read_csv(METRICS_INPUT)
    boundaries = json.loads(BOUNDARY_INPUT.read_text(encoding="utf-8"))

    missing_input_columns = [c for c in FORECAST_COLUMNS[:-1] if c not in source.columns]
    if missing_input_columns:
        raise RuntimeError(f"Hindcast input lacks required columns: {missing_input_columns}")

    dashboard = source[FORECAST_COLUMNS[:-1]].copy()
    dashboard["data_mode"] = "historical_hindcast"
    dashboard = dashboard[FORECAST_COLUMNS].sort_values(["date", "district_code"])

    clean_features = []
    for feature in boundaries.get("features", []):
        props = feature.get("properties", {})
        clean_features.append({
            "type": "Feature",
            "properties": {
                "district_code": int(props["imd_district_code"]),
                "district": str(props["imd_district"]),
                "division": str(props["karnataka_division"]),
            },
            "geometry": feature.get("geometry"),
        })
    clean_geojson = {
        "type": "FeatureCollection",
        "name": "SYNAPSE-WX Karnataka districts",
        "crs": boundaries.get("crs"),
        "features": sorted(clean_features, key=lambda f: f["properties"]["district_code"]),
    }

    geo_keys = {(f["properties"]["district_code"], f["properties"]["district"]) for f in clean_features}
    forecast_keys = set(zip(dashboard.district_code.astype(int), dashboard.district.astype(str)))
    unmatched_rows = int(sum((int(r.district_code), str(r.district)) not in geo_keys for r in dashboard.itertuples()))
    weight_sum = dashboard[["weight_gfs", "weight_ifs_hres", "weight_aifs"]].sum(axis=1)
    max_weight_error = float(np.max(np.abs(weight_sum - 1.0)))
    missing_required = {c: int(dashboard[c].isna().sum()) for c in REQUIRED_NON_NULL}
    duplicates = int(dashboard.duplicated(["date", "district_code"]).sum())

    adaptive_metric = metrics[
        (metrics["split"] == "final_test") &
        (metrics["scope_type"] == "overall") &
        (metrics["metric_family"] == "continuous") &
        (metrics["model"] == "SYNAPSE-WX adaptive trust")
    ]
    if len(adaptive_metric) != 1:
        raise RuntimeError("Expected one overall final-test adaptive metric row")
    direct_error = dashboard.synapse_wx_forecast_mm - dashboard.imd_actual_mm
    direct_mae = float(direct_error.abs().mean())
    direct_rmse = float(np.sqrt(np.mean(direct_error ** 2)))
    metric_mae = float(adaptive_metric.iloc[0].mae_mm)
    metric_rmse = float(adaptive_metric.iloc[0].rmse_mm)

    checks = [
        check("geojson_unique_districts", len(geo_keys) == 31 and len(clean_features) == 31,
              {"features": len(clean_features), "unique_district_keys": len(geo_keys)}, 31),
        check("forecast_row_count", len(dashboard) == 3749, len(dashboard), 3749),
        check("duplicate_district_date_rows", duplicates == 0, duplicates, 0),
        check("forecast_date_range", dashboard.date.min() == "2026-05-01" and dashboard.date.max() == "2026-08-31",
              {"start": dashboard.date.min(), "end": dashboard.date.max()},
              {"start": "2026-05-01", "end": "2026-08-31"}),
        check("missing_required_forecast_weight_or_district_fields", not any(missing_required.values()),
              missing_required, "all zero"),
        check("historical_hindcast_mode", dashboard.data_mode.eq("historical_hindcast").all(),
              dashboard.data_mode.value_counts().to_dict(), {"historical_hindcast": 3749}),
        check("weights_sum_to_one", max_weight_error <= WEIGHT_TOLERANCE,
              max_weight_error, f"<= {WEIGHT_TOLERANCE}"),
        check("all_forecast_rows_match_geojson", unmatched_rows == 0,
              {"unmatched_rows": unmatched_rows, "forecast_district_keys": len(forecast_keys)},
              {"unmatched_rows": 0, "forecast_district_keys": 31}),
        check("geojson_has_only_stable_properties",
              all(set(f["properties"]) == {"district_code", "district", "division"} for f in clean_features),
              sorted(set().union(*(set(f["properties"]) for f in clean_features))),
              ["district_code", "district", "division"]),
        check("adaptive_metrics_reconcile", abs(direct_mae - metric_mae) < 1e-12 and abs(direct_rmse - metric_rmse) < 1e-12,
              {"dashboard_mae": direct_mae, "metrics_mae": metric_mae,
               "dashboard_rmse": direct_rmse, "metrics_rmse": metric_rmse}, "exact within 1e-12"),
    ]

    dashboard.to_csv(FORECAST_OUTPUT, index=False, float_format="%.15g")
    GEOJSON_OUTPUT.write_text(json.dumps(clean_geojson, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    audit = {
        "status": "pass" if all(c["passed"] for c in checks) else "fail",
        "dashboard_scope": "historical hindcast only; not a live forecast and not synoptic-aware",
        "frozen_model_changed": False,
        "retraining_or_tuning_performed": False,
        "field_roles": {
            "imd_actual_mm": "Verification only",
            "absolute_error_mm": "Verification only; derived after the hindcast forecast",
            "forecast_inputs": ["gfs_forecast_mm", "ifs_hres_forecast_mm", "aifs_forecast_mm"],
            "trust_inputs": ["prior-date model errors represented by the frozen dynamic weights"],
            "synoptic_inputs": [],
        },
        "input_files": {
            str(FORECAST_INPUT.relative_to(ROOT)): sha256(FORECAST_INPUT),
            str(METRICS_INPUT.relative_to(ROOT)): sha256(METRICS_INPUT),
            str(BOUNDARY_INPUT.relative_to(ROOT)): sha256(BOUNDARY_INPUT),
        },
        "output_files": {
            str(FORECAST_OUTPUT.relative_to(ROOT)): {"rows": len(dashboard), "columns": FORECAST_COLUMNS},
            str(GEOJSON_OUTPUT.relative_to(ROOT)): {"features": len(clean_features), "properties": ["district_code", "district", "division"]},
        },
        "weight_tolerance": WEIGHT_TOLERANCE,
        "checks": checks,
    }
    AUDIT_OUTPUT.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"status": audit["status"], "checks": {c["name"]: c["passed"] for c in checks}}, indent=2))
    if audit["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
