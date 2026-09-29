# PS81 Karnataka MVP implementation audit

## Scope and runnable paths

This audit is documentation-only. The frozen legacy hindcast and its generated artifacts were not changed.

- The operational entry point is `python -m backend.api.cli --config configs/operational.example.json <command>`. The implemented flow is config validation -> geometry sampling -> Open-Meteo collection -> daily rainfall accumulation -> leakage-gated historical-error lookup -> blend -> append-only SQLite -> CSV export/evaluation.
- The historical reproducibility path is `python -m backend.legacy_hindcast_pipeline.train_synapse_wx_model_only`. It reads the checked-in statewide master as a fallback, but writes derived artifacts to ignored `outputs/`; report and dashboard preparation scripts then consume those outputs.
- The canonical frontend is `frontend/`. Vite mounts `../outputs` as its public directory and the adapter fetches `synapse_wx_dashboard_forecasts.csv` plus optional `synapse_wx_latest_operational_cycle.csv`. A fresh clone therefore cannot display forecast data.

## Blocking dependencies

`outputs/` is absent from the fresh checkout. The example operational config expects the following missing paths:

- `outputs/synapse_wx_dashboard_districts.geojson` (operational geometry)
- `outputs/synapse_wx_model_only_master.csv` (historical skill bootstrap)
- `outputs/operational/synapse_wx.sqlite3` and its raw/cache/export trees
- `outputs/synapse_wx_dashboard_forecasts.csv` (historical UI)
- `outputs/synapse_wx_latest_operational_cycle.csv` (live UI, optional)

A checked-in boundary exists at `datasets/adapt_wx_karnataka_district_boundaries.geojson`, and the checked-in statewide master can regenerate the frozen model-only outputs, but neither substitution is configured in the example file.

## Model and leakage audit

- No XGBoost package, model class, training module, serialized artifact, feature contract, or inference path exists. `backend/training/` and `backend/models/` contain only package markers.
- Both live and legacy blending use inverse-MAE weights. Operational fallback hierarchy is district+lead, region+lead, statewide+lead, then equal weights. Season and weather-regime conditioning are absent.
- The legacy split is chronological: train 2025-10-01..2025-12-31, validation 2026-01-01..2026-04-30, held-out test 2026-05-01..2026-08-31. Within each target day, weights are calculated before that day's observations are appended.
- Operational history queries require `verification_available_at_utc < issued_at_utc`, which is the key leakage barrier. Bootstrap assigns a configured 120-hour availability lag. Risks remain if provider timestamps are wrong, if XGBoost features are later built without issue-time/as-of joins, or if validation/test observations are allowed into fitting, preprocessing, regime definitions, or calibration.
- Historical source columns are labelled as forecasts but contain no issue time, upstream run time, ensemble member, or lead metadata. They support the frozen Day-1 hindcast claim, not independently auditable run selection. The live latest endpoint explicitly stores upstream run identity as unavailable.
- Forecast inputs are rainfall only. Temperature and wind exist as SYNOP observations, but failed statewide spatial/temporal coverage checks and were excluded. Extreme guidance is categorical/threshold verification, not calibrated probability or ensemble-tail guidance.

## Verification and operations

SQLite preserves cycles, sources, weights, configuration hash, raw hashes, availability times, and verification separately. Evaluation implements rainfall MAE/RMSE/bias by lead; legacy reports add event thresholds 1/10/25/50 mm. The CLI supports statewide issue/export/evaluate and scheduler-friendly IMD ingestion, but the repository installs no scheduler and tests do not cover XGBoost, temperature, wind, ensemble probabilities, seasonal/regime adaptation, or frontend end-to-end loading.

## Next segment: exact files and commands

Create or change only these implementation surfaces next: `requirements.txt`, `configs/operational.example.json` (or a new deployment config), `backend/training/`, `backend/models/`, `backend/inference/pipeline.py`, `backend/inference/export.py`, `backend/utils/store.py`, `backend/utils/config.py`, and new focused tests under `tests/`. Preserve `backend/legacy_hindcast_pipeline/` and all existing frozen outputs.

Recommended starting commands:

```powershell
Copy-Item configs/operational.example.json configs/ps81.karnataka.json
python -m backend.api.cli --config configs/ps81.karnataka.json validate-config
python -m unittest discover -s tests -v
rg -n "rolling_inverse_mae|hierarchical_historical_errors|lead_days|available_before_utc" backend tests configs
```

Before model work, define an issue-time feature table and temporal fit/validation/test contract, select an actual ensemble product with member/spread fields, and decide authoritative temperature/wind forecast and verification sources.
