"""Build, quality-gate, train, and evaluate statewide model-only SYNAPSE-WX."""
from __future__ import annotations

import csv
import json
from collections import defaultdict, deque
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs"
OUT.mkdir(parents=True, exist_ok=True)
SOURCE_CANDIDATES = (
    OUT / "synapse_wx_statewide_forecast_synoptic_master.csv",
    ROOT / "datasets" / "synapse_wx_statewide_forecast_synoptic_master.csv",
)
MASTER = OUT / "synapse_wx_model_only_master.csv"
QUALITY = OUT / "synapse_wx_model_only_quality_report.json"
MODELS = ("gfs_rain_mm", "ifs_hres_rain_mm", "aifs_rain_mm")
LABELS = {"gfs_rain_mm": "GFS", "ifs_hres_rain_mm": "IFS HRES", "aifs_rain_mm": "AIFS"}
FIELDS = ("date", "district_code", "district", "division", "imd_actual_mm", *MODELS)
SPLITS = {
    "train": ("2025-10-01", "2025-12-31"),
    "validation": ("2026-01-01", "2026-04-30"),
    "test": ("2026-05-01", "2026-08-31"),
}


def build_master():
    source_path = next((path for path in SOURCE_CANDIDATES if path.is_file()), None)
    if source_path is None:
        searched = "\n  - ".join(str(path) for path in SOURCE_CANDIDATES)
        raise FileNotFoundError(
            "Statewide forecast master not found. Searched:\n  - " + searched
        )
    source = pd.read_csv(source_path, dtype={"district_code": int})
    missing_columns = [c for c in FIELDS if c not in source.columns]
    if missing_columns:
        raise RuntimeError(f"Missing required columns: {missing_columns}")
    eligible_period = source[source.date >= "2025-10-01"].copy()
    nulls_before = {c: int(eligible_period[c].isna().sum()) for c in ("imd_actual_mm", *MODELS)}
    model_only = eligible_period[list(FIELDS)].dropna(subset=["imd_actual_mm", *MODELS]).copy()
    model_only = model_only.sort_values(["date", "district_code"])
    duplicates = int(model_only.duplicated(["date", "district_code"]).sum())
    invalid_keys = int(model_only[["date", "district_code", "district"]].isna().any(axis=1).sum())
    district_name_conflicts = int((model_only.groupby("district_code").district.nunique() > 1).sum())
    complete_nulls = {c: int(model_only[c].isna().sum()) for c in ("imd_actual_mm", *MODELS)}
    split_info = {}
    for name, (start, end) in SPLITS.items():
        part = model_only[(model_only.date >= start) & (model_only.date <= end)]
        split_info[name] = {"start": start, "end": end, "rows": int(len(part)),
                            "districts": int(part.district_code.nunique()),
                            "first_date": part.date.min() if len(part) else None,
                            "last_date": part.date.max() if len(part) else None}
    allowed = (len(model_only) > 0 and duplicates == 0 and invalid_keys == 0 and
               district_name_conflicts == 0 and not any(complete_nulls.values()) and
               all(v["rows"] > 0 and v["districts"] == 31 for v in split_info.values()))
    report = {
        "quality_gate": "statewide model-only historical-performance trust",
        "training_allowed": False,
        "synoptic_training_allowed": False,
        "model_only_training_allowed": bool(allowed),
        "source": str(source_path.relative_to(ROOT)),
        "output": MASTER.name,
        "eligibility_start": "2025-10-01",
        "eligibility_rules": [
            "imd_actual_mm present", "gfs_rain_mm present", "ifs_hres_rain_mm present",
            "aifs_rain_mm present", "unique matching district/date key", "no SYNOP fields used",
        ],
        "source_rows_in_period": int(len(eligible_period)),
        "eligible_rows": int(len(model_only)),
        "districts": int(model_only.district_code.nunique()),
        "date_range": {"start": model_only.date.min(), "end": model_only.date.max()},
        "source_missing_values_in_period": nulls_before,
        "eligible_missing_values": complete_nulls,
        "duplicate_district_date_keys": duplicates,
        "invalid_district_date_keys": invalid_keys,
        "district_name_conflicts": district_name_conflicts,
        "columns": list(model_only.columns),
        "synoptic_columns_in_output": [c for c in model_only.columns if c.startswith("synop_")],
        "chronological_splits": split_info,
    }
    model_only.to_csv(MASTER, index=False, float_format="%.4f")
    QUALITY.write_text(json.dumps(report, indent=2), encoding="utf-8")
    synoptic_quality_path = OUT / "synapse_wx_api_data_quality_report.json"
    if synoptic_quality_path.is_file():
        synoptic_quality = json.loads(synoptic_quality_path.read_text(encoding="utf-8"))
        if synoptic_quality.get("training_allowed") is not False or synoptic_quality.get("status") != "fail":
            raise RuntimeError("Refusing to alter an unexpected synoptic quality result")
        synoptic_quality["training_allowed"] = False
        synoptic_quality["synoptic_training_allowed"] = False
        synoptic_quality["model_only_training_allowed"] = bool(allowed)
        synoptic_quality_path.write_text(json.dumps(synoptic_quality, indent=2), encoding="utf-8")
    return report, model_only


def rows_for(frame):
    rows = frame.to_dict("records")
    for row in rows:
        row["date"] = date.fromisoformat(row["date"])
        row["district_code"] = int(row["district_code"])
        for field in ("imd_actual_mm", *MODELS): row[field] = float(row[field])
    return sorted(rows, key=lambda r: (r["date"], r["district_code"]))


def weights(errors, power):
    maes = np.array([np.mean(errors[m]) if errors[m] else np.nan for m in MODELS])
    if np.isnan(maes).any(): return np.full(len(MODELS), 1 / len(MODELS))
    raw = 1 / np.power(np.maximum(maes, 0.1), power)
    return raw / raw.sum()


def adaptive_predict(history, target, window, power):
    by_district = defaultdict(lambda: deque(maxlen=window))
    for row in history: by_district[row["district_code"]].append(row)
    predictions = []
    for forecast_date in sorted({r["date"] for r in target}):
        todays = [r for r in target if r["date"] == forecast_date]
        for row in todays:
            prior = list(by_district[row["district_code"]])
            errors = {m: [abs(p[m] - p["imd_actual_mm"]) for p in prior] for m in MODELS}
            w = weights(errors, power); values = np.array([row[m] for m in MODELS])
            predictions.append({**row, "equal_weight_forecast_mm": float(values.mean()),
                                "adaptive_forecast_mm": max(0.0, float(values @ w)),
                                "history_days_used": len(prior),
                                **{f"weight_{m.replace('_rain_mm','')}": float(x) for m, x in zip(MODELS, w)}})
        for row in todays: by_district[row["district_code"]].append(row)
    return predictions


def static_predict(history, target, power):
    grouped = defaultdict(list)
    for row in history: grouped[row["district_code"]].append(row)
    result = []
    for row in target:
        prior = grouped[row["district_code"]]
        errors = {m: [abs(p[m] - p["imd_actual_mm"]) for p in prior] for m in MODELS}
        w = weights(errors, power)
        result.append({**row, "static_forecast_mm": max(0.0, float(np.array([row[m] for m in MODELS]) @ w))})
    return result


def metric(rows, field):
    actual = np.array([r["imd_actual_mm"] for r in rows]); predicted = np.array([r[field] for r in rows])
    error = predicted - actual
    bias_unrounded = float(np.mean(error))
    return {"n": len(rows), "mae_mm": round(float(np.mean(np.abs(error))), 3),
            "rmse_mm": round(float(np.sqrt(np.mean(error ** 2))), 3),
            "bias_mm_unrounded": bias_unrounded,
            "bias_mm": round(bias_unrounded, 3)}


def write_predictions(name, rows):
    fields = [*FIELDS, "equal_weight_forecast_mm", "adaptive_forecast_mm", "history_days_used",
              "weight_gfs", "weight_ifs_hres", "weight_aifs"]
    with (OUT / name).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader()
        for row in rows:
            record = {f: row.get(f) for f in fields}; record["date"] = row["date"].isoformat(); writer.writerow(record)


def rainfall_category(mm):
    """IMD daily rainfall intensity thresholds, applied to the hindcast value."""
    if mm <= 0.0: return "no_rain"
    if mm <= 2.4: return "very_light"
    if mm <= 15.5: return "light"
    if mm <= 64.4: return "moderate"
    if mm <= 115.5: return "heavy"
    if mm <= 204.4: return "very_heavy"
    return "extremely_heavy"


def write_final_hindcast(rows, validation_rows):
    """Export held-out historical hindcasts; no same-day IMD value affects a forecast."""
    validation_spreads = np.array([
        max(row[m] for m in MODELS) - min(row[m] for m in MODELS)
        for row in validation_rows
    ])
    low_cutoff, high_cutoff = (float(x) for x in np.quantile(validation_spreads, [1/3, 2/3]))
    fields = ["date", "district_code", "district", "division",
              "gfs_forecast_mm", "ifs_hres_forecast_mm", "aifs_forecast_mm",
              "weight_gfs", "weight_ifs_hres", "weight_aifs",
              "synapse_wx_forecast_mm", "imd_actual_mm", "absolute_error_mm",
              "rainfall_category", "model_agreement_mm", "confidence_level", "trust_explanation"]
    with (OUT / "synapse_wx_final_hindcast_forecast.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader()
        for row in rows:
            forecasts = [row[m] for m in MODELS]
            spread = float(max(forecasts) - min(forecasts))
            confidence = "high" if spread <= low_cutoff else "moderate" if spread <= high_cutoff else "low"
            weight_values = [row["weight_gfs"], row["weight_ifs_hres"], row["weight_aifs"]]
            best = int(np.argmax(weight_values))
            explanation = (
                "Historical hindcast—not a live forecast. "
                f"{LABELS[MODELS[best]]} has the highest dynamic trust weight "
                f"({weight_values[best]:.1%}); weights use only earlier district-date IMD errors. "
                f"Model spread is {spread:.3f} mm; {confidence} confidence is a relative "
                "agreement label based on validation-period spread terciles, not a probability."
            )
            forecast = float(row["adaptive_forecast_mm"])
            writer.writerow({
                "date": row["date"].isoformat(), "district_code": row["district_code"],
                "district": row["district"], "division": row["division"],
                "gfs_forecast_mm": row["gfs_rain_mm"], "ifs_hres_forecast_mm": row["ifs_hres_rain_mm"],
                "aifs_forecast_mm": row["aifs_rain_mm"], "weight_gfs": row["weight_gfs"],
                "weight_ifs_hres": row["weight_ifs_hres"], "weight_aifs": row["weight_aifs"],
                "synapse_wx_forecast_mm": forecast, "imd_actual_mm": row["imd_actual_mm"],
                "absolute_error_mm": abs(forecast - row["imd_actual_mm"]),
                "rainfall_category": rainfall_category(forecast), "model_agreement_mm": spread,
                "confidence_level": confidence, "trust_explanation": explanation,
            })
    return {"method": "max minus min of the three model rainfall forecasts",
            "confidence_basis": "validation-period model-agreement terciles",
            "high_max_mm": low_cutoff, "moderate_max_mm": high_cutoff,
            "label": "historical hindcast—not a live forecast"}


def main():
    quality, master = build_master()
    if not quality["model_only_training_allowed"]: raise RuntimeError("Model-only quality gate failed; training aborted")
    parts = {name: rows_for(master[(master.date >= start) & (master.date <= end)]) for name, (start, end) in SPLITS.items()}
    candidates = [(window, power) for window in (14, 30, 60, 90) for power in (0.5, 1.0, 1.5, 2.0)]
    runs = []
    for window, power in candidates:
        pred = adaptive_predict(parts["train"], parts["validation"], window, power)
        runs.append((metric(pred, "adaptive_forecast_mm")["mae_mm"], window, power, pred))
    _, window, power, validation = min(runs, key=lambda x: x[0])
    static_validation = static_predict(parts["train"], parts["validation"], power)
    test = adaptive_predict(parts["train"] + parts["validation"], parts["test"], window, power)
    static_test = static_predict(parts["train"] + parts["validation"], parts["test"], power)
    write_predictions("synapse_wx_model_only_validation_predictions.csv", validation)
    write_predictions("synapse_wx_model_only_test_predictions.csv", test)
    hindcast_definition = write_final_hindcast(test, validation)
    report = {
        "project": "SYNAPSE-WX statewide Karnataka model-only adaptive trust",
        "model_type": "leakage-safe district-specific rolling inverse-MAE ensemble",
        "model_only_training_allowed": True,
        "synoptic_features_used": [],
        "output_label": "historical hindcast—not a live forecast",
        "models": [LABELS[m] for m in MODELS],
        "leakage_control": "For valid date T, weights use IMD errors only from dates strictly before T; validation selects hyperparameters and test remains held out until final evaluation.",
        "splits": quality["chronological_splits"],
        "selected_hyperparameters_from_validation": {"rolling_window_days": window, "inverse_mae_power": power},
        "hindcast_classification": hindcast_definition,
        "validation_metrics": {"adaptive_model_trust": metric(validation, "adaptive_forecast_mm"),
                               "equal_weight_blend": metric(validation, "equal_weight_forecast_mm"),
                               "static_inverse_mae_blend": metric(static_validation, "static_forecast_mm"),
                               **{LABELS[m]: metric(validation, m) for m in MODELS}},
        "final_test_metrics": {"adaptive_model_trust": metric(test, "adaptive_forecast_mm"),
                               "equal_weight_blend": metric(test, "equal_weight_forecast_mm"),
                               "static_inverse_mae_blend": metric(static_test, "static_forecast_mm"),
                               **{LABELS[m]: metric(test, m) for m in MODELS}},
    }
    (OUT / "synapse_wx_model_only_adaptive_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (OUT / "synapse_wx_model_only_adaptive_config.json").write_text(json.dumps({
        "model_type": report["model_type"], "models": report["models"], "synoptic_features_used": [],
        **report["selected_hyperparameters_from_validation"]}, indent=2), encoding="utf-8")
    print(json.dumps({"quality": quality, "training_report": report}, indent=2))


if __name__ == "__main__": main()
