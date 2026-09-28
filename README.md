# SYNAPSE-WX

SYNAPSE-WX is a leakage-safe Karnataka district-wise 24-hour rainfall forecasting MVP. It combines separate GFS, IFS HRES, and AIFS rainfall forecasts using district-specific dynamic trust weights derived only from earlier IMD realised-rainfall errors.

## Frozen model-only MVP

- Common three-model period begins 2025-10-01.
- Training: 2025-10-01 to 2025-12-31.
- Validation: 2026-01-01 to 2026-04-30.
- Final held-out test: 2026-05-01 to 2026-08-31.
- Rolling performance window: 60 days.
- Inverse-MAE power: 2.0.
- SYNOP features are excluded from training because the available station dataset failed statewide spatial and temporal coverage checks.

## Historical hindcast pipeline

The frozen historical/reproducibility scripts are retained under
`backend/legacy_hindcast_pipeline/`. They are separate from the operational
forecast package and preserve the original model-only evaluation flow.

```powershell
python -m backend.legacy_hindcast_pipeline.collect_synapse_wx_statewide --help
python -m backend.legacy_hindcast_pipeline.train_synapse_wx_model_only
python -m backend.legacy_hindcast_pipeline.verify_synapse_wx_model_only_outputs
python -m backend.legacy_hindcast_pipeline.generate_synapse_wx_detailed_reports
python -m backend.legacy_hindcast_pipeline.verify_synapse_wx_detailed_reports
```

Input datasets, raw API responses, and generated outputs are intentionally excluded from Git. The collectors and reports preserve leakage controls and provenance so those artifacts can be reproduced locally.

## Important interpretation

The saved evaluation output is a historical hindcast, not a live operational forecast. Final test observations are used only for verification after the model configuration is frozen.

## Operational extension (in development)

The configuration-driven operational pipeline lives in `backend/`. It is
separate from the frozen historical scripts and outputs. No issued operational
forecast may overwrite another, and all scientific and deployment values are
loaded from versioned configuration.

The operational layout is:

```text
backend/
├── api/                         CLI entry points and future service adapters
├── data/{raw,processed,external}/  operational data boundaries and bootstrap
├── ingestion/                   Open-Meteo and IMD ingestion
├── preprocessing/               geography and sampling helpers
├── training/                    operational training extension point
├── inference/                   forecast cycles, blending, archive, export
├── models/                      model artifact extension point
├── evaluation/                  verification and cycle evaluation
├── utils/                       configuration and persistence
└── legacy_hindcast_pipeline/   frozen historical/reproducibility scripts
```

```powershell
python -m backend.api.cli --config configs/operational.example.json validate-config
python -m backend.api.cli --config configs/operational.example.json init-db
python -m backend.api.cli --config configs/operational.example.json bootstrap-history
python -m backend.api.cli --config configs/ps81.karnataka.json train-xgboost
python -m backend.api.cli --config configs/ps81.karnataka.json load-xgboost
python -m backend.api.cli --config configs/ps81.karnataka.json infer-xgboost --district 506 --month 9 --gfs 0 --ifs-hres 0.38 --aifs 0
python -m backend.api.cli --config configs/ps81.karnataka.json evaluate-xgboost-holdout
python -m backend.api.cli --config configs/operational.example.json run-district --district "Bengaluru Urban"
python -m backend.api.cli --config configs/operational.example.json run-statewide
python -m backend.api.cli --config configs/operational.example.json export-cycle --cycle-id CYCLE_ID
python -m backend.api.cli --config configs/operational.example.json evaluate-cycle --cycle-id CYCLE_ID
python -m backend.api.cli --config configs/operational.example.json import-verification --file PATH_TO_IMD_CSV --dry-run
python -m backend.api.cli --config configs/operational.example.json import-verification --file PATH_TO_IMD_CSV
python -m backend.api.cli --config configs/operational.example.json ingest-imd --date 2026-09-04
python -m backend.api.cli --config configs/operational.example.json ingest-latest-imd
python -m unittest discover -s tests -v
```

`run-district` is the bounded vertical slice: it derives sampling points from the configured boundary, retrieves every enabled source, forms configured lead-day accumulations, and appends the issued source and blended forecasts to SQLite. With no eligible verification history it records the configured equal-weight cold-start fallback.

For the PS81 configuration, Day 1 uses a hash-validated source-specific XGBoost expected-absolute-error artifact when the complete GFS/IFS HRES/AIFS contract is present. Predicted errors are converted to inverse-error weights; feature importance is not a weight. Missing artifacts or incomplete inputs retain explicitly named inverse-MAE/equal-weight fallbacks. See `docs/XGBOOST_BLENDING_DESIGN.md`.

`bootstrap-history` imports configured source and verification columns into a separate immutable skill-history table. The current audited dataset supplies Day-1 history only, so longer forecast leads correctly retain the equal-weight cold start until lead-specific history is collected or backfilled. The configured verification-availability lag controls which historical dates are eligible at issuance.

`bootstrap-archived-skill` derives its date window from the verified historical master and configured rolling window, retrieves Previous Runs fields for every configured lead, and adds only missing immutable district/model/lead skill observations. `reconstruct-missing-cycles` detects the gap between the historical master and the first live cycle, retrieves exact Single Runs, and exports those dates with the configured archived-reconstruction mode. Neither command contains a fixed date or cycle identifier.

Operational weighting uses the first complete evidence level in this order: district and lead, regional and lead, statewide and lead, then the configured no-history fallback. The selected fallback level is persisted with every blend.

Run the IMD verification import with `--dry-run` first. CSV column names, district identifiers, units, provider, classification, and optional availability timestamp column are declared in configuration. If no availability timestamp column is configured, the first successful import time is retained as the leakage-safe availability time. Identical re-imports are harmless; conflicting values are rejected.

The IMD commands retrieve the official real-time daily 0.25-degree rainfall binary grid, preserve it unchanged with a manifest and SHA-256 hash, and derive district values from grid points inside the configured polygons. These are SYNAPSE-WX district aggregates of an IMD gridded product, not an IMD-published district table. `ingest-latest-imd` uses the configured availability-day offset and is scheduler-friendly; no system scheduler is installed by the project.

`evaluate-cycle` matches verification by provider, district, and exact valid interval. It exports row-level source and blend errors plus MAE, RMSE, and bias summaries by lead time. Unpublished verification remains explicitly pending and is never replaced with a proxy or zero.

See `docs/OPERATIONAL_ARCHITECTURE.md` for the data contract, provenance rules, and the next implementation slice. Copy the example configuration to a deployment-specific file before operational use; do not put credentials in version control.

## Historical hindcast dashboard

The canonical local dashboard is in `frontend/`. It presents the frozen
model-only MVP; IMD realised rainfall appears only under **Verification only**.

```powershell
cd frontend
npm install
npm run dev
```

The earlier Vinext/Cloudflare dashboard is archived at `_review/dashboard/` and
is not deleted. Its `outputs/` → `public/data/` copy step is retained there as
a reference if that behavior is later needed in `frontend/`.

If `npm` is not on your PowerShell path, use `C:\Program Files\nodejs\npm.cmd` instead.

Data sources:

- `outputs/synapse_wx_dashboard_forecasts.csv` — 3,749 frozen May–August 2026 district-day hindcasts.
- `outputs/synapse_wx_dashboard_districts.geojson` — one polygon feature for each of Karnataka's 31 districts.

The canonical Vite app serves the configured local outputs directly during
development and includes its checked-in map assets. The forecast datasets
remain local and are not published in Git.
