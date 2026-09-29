"""Generate frozen validation and final held-out test evaluation reports."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs"
CONFIG = OUT / "synapse_wx_model_only_adaptive_config.json"
MASTER = OUT / "synapse_wx_model_only_master.csv"
VALIDATION = OUT / "synapse_wx_model_only_validation_predictions.csv"
TEST = OUT / "synapse_wx_model_only_test_predictions.csv"
THRESHOLDS = (1.0, 10.0, 25.0, 50.0)
MODEL_FIELDS = {
    "GFS": "gfs_rain_mm",
    "IFS HRES": "ifs_hres_rain_mm",
    "AIFS": "aifs_rain_mm",
    "Equal-weight blend": "equal_weight_forecast_mm",
    "Static inverse-MAE blend": "static_forecast_mm",
    "SYNAPSE-WX adaptive trust": "adaptive_forecast_mm",
}


def inverse_mae_weights(history, power):
    fields = ("gfs_rain_mm", "ifs_hres_rain_mm", "aifs_rain_mm")
    maes = np.array([(history[f] - history.imd_actual_mm).abs().mean() for f in fields])
    if np.isnan(maes).any(): return np.full(3, 1 / 3)
    raw = 1 / np.power(np.maximum(maes, 0.1), power)
    return raw / raw.sum()


def add_static_forecast(target, history, power):
    result = target.copy()
    weights = {}
    for code, group in history.groupby("district_code"):
        weights[int(code)] = inverse_mae_weights(group, power)
    values = result[["gfs_rain_mm", "ifs_hres_rain_mm", "aifs_rain_mm"]].to_numpy(float)
    matrix = np.vstack([weights[int(code)] for code in result.district_code])
    result["static_forecast_mm"] = np.maximum(0.0, np.sum(values * matrix, axis=1))
    return result


def safe_div(a, b):
    return float(a / b) if b else np.nan


def continuous_row(split, scope_type, scope_value, model_name, actual, forecast):
    error = forecast.to_numpy(float) - actual.to_numpy(float)
    bias = float(np.mean(error))
    return {
        "split": split, "scope_type": scope_type, "scope_value": scope_value,
        "metric_family": "continuous", "threshold_mm": np.nan, "model": model_name,
        "n": len(error), "mae_mm": float(np.mean(np.abs(error))),
        "rmse_mm": float(np.sqrt(np.mean(error ** 2))),
        "bias_mm": round(bias, 3), "bias_mm_unrounded": bias,
        "observed_events": np.nan, "forecast_events": np.nan, "hits": np.nan,
        "misses": np.nan, "false_alarms": np.nan, "correct_negatives": np.nan,
        "pod_recall": np.nan, "false_alarm_ratio": np.nan, "csi": np.nan,
        "precision": np.nan, "f1_score": np.nan,
    }


def event_row(split, model_name, actual, forecast, threshold):
    observed = actual.to_numpy(float) >= threshold
    predicted = forecast.to_numpy(float) >= threshold
    hits = int(np.sum(observed & predicted)); misses = int(np.sum(observed & ~predicted))
    false_alarms = int(np.sum(~observed & predicted)); correct_negatives = int(np.sum(~observed & ~predicted))
    event_error = forecast.to_numpy(float)[observed] - actual.to_numpy(float)[observed]
    bias = float(np.mean(event_error)) if len(event_error) else np.nan
    precision = safe_div(hits, hits + false_alarms); recall = safe_div(hits, hits + misses)
    return {
        "split": split, "scope_type": "event_threshold", "scope_value": f">={threshold:g} mm",
        "metric_family": "event", "threshold_mm": threshold, "model": model_name,
        "n": len(actual), "mae_mm": float(np.mean(np.abs(event_error))) if len(event_error) else np.nan,
        "rmse_mm": float(np.sqrt(np.mean(event_error ** 2))) if len(event_error) else np.nan,
        "bias_mm": round(bias, 3) if not np.isnan(bias) else np.nan,
        "bias_mm_unrounded": bias, "observed_events": int(observed.sum()),
        "forecast_events": int(predicted.sum()), "hits": hits, "misses": misses,
        "false_alarms": false_alarms, "correct_negatives": correct_negatives,
        "pod_recall": recall, "false_alarm_ratio": safe_div(false_alarms, hits + false_alarms),
        "csi": safe_div(hits, hits + misses + false_alarms), "precision": precision,
        "f1_score": safe_div(2 * precision * recall, precision + recall) if not np.isnan(precision) and not np.isnan(recall) else np.nan,
    }


def build_metrics(frame, split, include_month):
    rows = []
    scopes = [("overall", "All rows", frame)]
    scopes += [("district", name, group) for name, group in frame.groupby("district", sort=True)]
    scopes += [("division", name, group) for name, group in frame.groupby("division", sort=True)]
    if include_month:
        month = pd.to_datetime(frame.date).dt.strftime("%Y-%m")
        scopes += [("month", name, frame[month == name]) for name in sorted(month.unique())]
    for scope_type, scope_value, group in scopes:
        for model_name, field in MODEL_FIELDS.items():
            rows.append(continuous_row(split, scope_type, scope_value, model_name,
                                       group.imd_actual_mm, group[field]))
    for threshold in THRESHOLDS:
        for model_name, field in MODEL_FIELDS.items():
            rows.append(event_row(split, model_name, frame.imd_actual_mm, frame[field], threshold))
    return pd.DataFrame(rows)


def f3(value):
    return "—" if pd.isna(value) else f"{value:.3f}"


def markdown_table(frame, columns, labels=None):
    labels = labels or columns
    lines = ["| " + " | ".join(labels) + " |", "|" + "|".join(["---"] * len(columns)) + "|"]
    for _, row in frame.iterrows():
        cells = []
        for col in columns:
            value = row[col]
            if col in {"mae_mm", "rmse_mm", "bias_mm", "pod_recall", "false_alarm_ratio", "csi", "precision", "f1_score"}:
                cells.append(f3(value))
            elif col == "bias_mm_unrounded": cells.append("—" if pd.isna(value) else repr(float(value)))
            else: cells.append(str(value))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def write_report(metrics, split, source_rows, output_path, final_test):
    title = "SYNAPSE-WX Final Test Evaluation" if final_test else "SYNAPSE-WX Validation Evaluation"
    date_start, date_end = source_rows.date.min(), source_rows.date.max()
    intro = (
        "**Final held-out test evaluation. No test data was used for model selection or hyperparameter tuning.**"
        if final_test else
        "**Validation evaluation used for documented model selection. It does not include final-test rows.**"
    )
    lines = [f"# {title}", "", intro, "", "## Evaluation contract", "",
             f"- Period: {date_start} to {date_end}.", f"- Rows: {len(source_rows):,}; districts: {source_rows.district_code.nunique()}.",
             "- Frozen adaptive configuration: 60-day district-specific rolling history; inverse-MAE power 2.0.",
             "- Forecast error is forecast minus IMD actual rainfall. Positive bias is overprediction.",
             "- Static inverse-MAE weights use training history for validation and training plus validation history for test.",
             "- Event detection uses both observed and forecast rainfall at the stated threshold. Event-row MAE/RMSE/bias are calculated only where IMD actual rainfall meets that threshold.",
             "- POD equals recall; FAR is false alarms divided by forecast events; CSI is hits divided by hits + misses + false alarms.", "",
             "## Overall metrics", ""]
    overall = metrics[(metrics.scope_type == "overall") & (metrics.metric_family == "continuous")]
    lines.append(markdown_table(overall, ["model", "n", "mae_mm", "rmse_mm", "bias_mm", "bias_mm_unrounded"],
                                ["Model", "N", "MAE mm", "RMSE mm", "Bias mm", "Full-precision bias mm"]))
    lines += ["", "## Metrics by Karnataka division", ""]
    div = metrics[(metrics.scope_type == "division") & (metrics.metric_family == "continuous")]
    lines.append(markdown_table(div, ["scope_value", "model", "n", "mae_mm", "rmse_mm", "bias_mm"],
                                ["Division", "Model", "N", "MAE mm", "RMSE mm", "Bias mm"]))
    if final_test:
        lines += ["", "## Metrics by month", ""]
        month = metrics[(metrics.scope_type == "month") & (metrics.metric_family == "continuous")]
        lines.append(markdown_table(month, ["scope_value", "model", "n", "mae_mm", "rmse_mm", "bias_mm"],
                                    ["Month", "Model", "N", "MAE mm", "RMSE mm", "Bias mm"]))
    lines += ["", "## Rainfall-event threshold metrics", ""]
    event = metrics[metrics.metric_family == "event"]
    lines.append(markdown_table(event, ["scope_value", "model", "observed_events", "forecast_events", "hits", "misses", "false_alarms", "pod_recall", "false_alarm_ratio", "csi", "f1_score", "mae_mm", "rmse_mm", "bias_mm"],
                                ["Threshold", "Model", "Observed", "Forecast", "Hits", "Misses", "False alarms", "POD", "FAR", "CSI", "F1", "Event MAE", "Event RMSE", "Event bias"]))
    lines += ["", "## Metrics by district", ""]
    district = metrics[(metrics.scope_type == "district") & (metrics.metric_family == "continuous")]
    lines.append(markdown_table(district, ["scope_value", "model", "n", "mae_mm", "rmse_mm", "bias_mm"],
                                ["District", "Model", "N", "MAE mm", "RMSE mm", "Bias mm"]))
    lines += ["", "## Interpretation limits", "",
              "These are historical hindcast verification results, not live forecasts. Trust weights describe relative recent model performance; they are not calibrated probabilities. No SYNOP variables or XGBoost model are used.", ""]
    output_path.write_text("\n".join(lines), encoding="utf-8")


def main():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if config.get("rolling_window_days") != 60 or config.get("inverse_mae_power") != 2.0:
        raise RuntimeError("Frozen configuration mismatch; refusing to evaluate")
    if config.get("synoptic_features_used") != []:
        raise RuntimeError("Model-only configuration unexpectedly contains synoptic features")
    master = pd.read_csv(MASTER, dtype={"district_code": int})
    validation = pd.read_csv(VALIDATION, dtype={"district_code": int})
    test = pd.read_csv(TEST, dtype={"district_code": int})
    train_history = master[(master.date >= "2025-10-01") & (master.date <= "2025-12-31")]
    validation = add_static_forecast(validation, train_history, 2.0)
    test_history = master[(master.date >= "2025-10-01") & (master.date <= "2026-04-30")]
    test = add_static_forecast(test, test_history, 2.0)
    validation_metrics = build_metrics(validation, "validation", include_month=False)
    test_metrics = build_metrics(test, "final_test", include_month=True)
    validation_metrics.to_csv(OUT / "synapse_wx_validation_detailed_metrics.csv", index=False, float_format="%.15g")
    test_metrics.to_csv(OUT / "synapse_wx_final_test_detailed_metrics.csv", index=False, float_format="%.15g")
    write_report(validation_metrics, "validation", validation, OUT / "synapse_wx_validation_report.md", False)
    write_report(test_metrics, "final_test", test, OUT / "synapse_wx_final_test_report.md", True)
    print(json.dumps({"frozen_config": config,
                      "validation": {"rows": len(validation), "metrics_rows": len(validation_metrics)},
                      "final_test": {"rows": len(test), "metrics_rows": len(test_metrics)}}, indent=2))


if __name__ == "__main__": main()
