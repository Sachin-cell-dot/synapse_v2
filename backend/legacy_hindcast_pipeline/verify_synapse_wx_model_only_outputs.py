"""Reproducible integrity audit for model-only predictions and final hindcast."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import train_synapse_wx_model_only as model

OUT = Path(__file__).resolve().parents[2] / "outputs"
master = pd.read_csv(OUT / "synapse_wx_model_only_master.csv")
saved = pd.read_csv(OUT / "synapse_wx_model_only_test_predictions.csv")
hindcast = pd.read_csv(OUT / "synapse_wx_final_hindcast_forecast.csv")
config = json.loads((OUT / "synapse_wx_model_only_adaptive_config.json").read_text(encoding="utf-8"))

train = model.rows_for(master[(master.date >= "2025-10-01") & (master.date <= "2025-12-31")])
validation = model.rows_for(master[(master.date >= "2026-01-01") & (master.date <= "2026-04-30")])
test = model.rows_for(master[(master.date >= "2026-05-01") & (master.date <= "2026-08-31")])
replayed = pd.DataFrame(model.adaptive_predict(
    train + validation, test, config["rolling_window_days"], config["inverse_mae_power"]
))
replayed["date"] = replayed.date.astype(str)

key = ["date", "district_code"]
joined = saved.merge(replayed, on=key, suffixes=("_saved", "_replayed"), validate="one_to_one")
numeric = ["imd_actual_mm", *model.MODELS, "adaptive_forecast_mm", "equal_weight_forecast_mm",
           "weight_gfs", "weight_ifs_hres", "weight_aifs"]
replay_errors = {c: float(np.max(np.abs(joined[f"{c}_saved"] - joined[f"{c}_replayed"]))) for c in numeric}
weight_error = float(np.max(np.abs(saved[["weight_gfs", "weight_ifs_hres", "weight_aifs"]].sum(axis=1) - 1)))

expected_columns = ["date", "district_code", "district", "division", "gfs_forecast_mm",
                    "ifs_hres_forecast_mm", "aifs_forecast_mm", "weight_gfs", "weight_ifs_hres",
                    "weight_aifs", "synapse_wx_forecast_mm", "imd_actual_mm", "absolute_error_mm",
                    "rainfall_category", "model_agreement_mm", "confidence_level", "trust_explanation"]
assert list(hindcast.columns) == expected_columns
assert not hindcast.duplicated(key).any()
assert not hindcast.isna().any().any()
assert len(hindcast) == len(saved) == len(test)
assert weight_error < 1e-12
assert max(replay_errors.values()) < 1e-12
assert np.max(np.abs(hindcast.synapse_wx_forecast_mm - saved.adaptive_forecast_mm)) < 1e-12
assert np.max(np.abs(hindcast.imd_actual_mm - saved.imd_actual_mm)) < 1e-12
assert np.max(np.abs(hindcast.absolute_error_mm - np.abs(hindcast.synapse_wx_forecast_mm - hindcast.imd_actual_mm))) < 1e-12
spread = hindcast[["gfs_forecast_mm", "ifs_hres_forecast_mm", "aifs_forecast_mm"]].max(axis=1) - hindcast[["gfs_forecast_mm", "ifs_hres_forecast_mm", "aifs_forecast_mm"]].min(axis=1)
assert np.max(np.abs(hindcast.model_agreement_mm - spread)) < 1e-12
assert hindcast.trust_explanation.str.startswith("Historical hindcast—not a live forecast.").all()

report = {
    "status": "pass",
    "rows": len(hindcast),
    "date_range": {"start": hindcast.date.min(), "end": hindcast.date.max()},
    "districts": int(hindcast.district_code.nunique()),
    "duplicate_keys": int(hindcast.duplicated(key).sum()),
    "missing_cells": int(hindcast.isna().sum().sum()),
    "maximum_weight_sum_error": weight_error,
    "maximum_replay_difference_by_field": replay_errors,
    "leakage_check": "pass: saved test weights/forecasts exactly match a replay initialized only with train+validation rows and updated after each complete test date",
    "imd_verification_check": "pass: hindcast IMD values match held-out prediction IMD values",
    "absolute_error_check": "pass",
    "model_agreement_check": "pass",
    "historical_hindcast_label_check": "pass",
}
(OUT / "synapse_wx_final_hindcast_audit.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
