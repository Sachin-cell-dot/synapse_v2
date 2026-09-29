# SYNAPSE-WX

SYNAPSE-WX is a Karnataka district-level weather forecasting MVP for SIH PS81. The Python backend retrieves and archives forecast evidence, while the React/TypeScript dashboard presents operational forecasts and clearly separated historical-proxy verification.

## What is implemented

- Rainfall forecasts from GFS, ECMWF IFS HRES, and ECMWF AIFS for all 31 Karnataka districts.
- A locked Day-1 XGBoost expected-absolute-error model (`2e070898b34c589b`) that converts predicted source errors into normalized inverse-error weights.
- Named equal-weight baselines for Days 2-6; they are not labelled as XGBoost forecasts.
- GEFS ensemble mean and spread as display-only evidence outside the locked three-source blend.
- Experimental forecast-only daily Tmax, Tmin, and maximum 10 m wind.
- Append-only cycle storage, exports, routine-operation preflight, and prospective SYNOP point-forecast archiving.
- A Vite dashboard with Historical proxy evaluation and Operational forecast modes, district maps, verification, thresholds, and a persisted-evidence "Why this blend?" explanation.

## Repository layout

```text
backend/       Python ingestion, inference, storage, evaluation, and operations
configs/       Versioned example and Karnataka operational configuration
datasets/      Checked-in Karnataka district boundary only
docs/          Audit, architecture, data inventory, and model documentation
frontend/      React/Vite application written in TypeScript
tests/         Python contract, leakage, persistence, and integration tests
```

## Setup

Python 3.11+ and Node.js/npm are required.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m backend.api.cli --config configs/ps81.karnataka.json validate-config
python -m unittest discover -s tests -p "test_*.py"

cd frontend
npm.cmd ci
npm.cmd test
npm.cmd run build
npm.cmd run dev
```

Open `http://127.0.0.1:5173/` after starting Vite.

## Demo data and model requirements

Generated and downloaded material is intentionally excluded from Git: `.env` files, databases, model binaries, raw API responses, forecast/observation CSVs, operational exports, caches, `node_modules`, and `dist`.

The UI builds without these artifacts, but forecast cards require retained or newly generated files under `outputs/`. Locked Day-1 inference additionally requires the hash-validated XGBoost artifact and metadata under `outputs/operational/models/day1-xgboost/`. See `docs/FINAL_IMPLEMENTATION_REPORT.md` and `docs/OPERATIONAL_ARCHITECTURE.md` for exact reproduction and operational commands. Never commit credentials or generated weather data.

## Validated results

The frozen May-August 2026 rainfall holdout contains 3,749 paired district-day rows. On identical paired rows, the locked XGBoost blend achieved the best overall MAE at **3.939 mm**. AIFS achieved the better RMSE, absolute bias, and 50 mm heavy-rain CSI. These are different metrics; SYNAPSE-WX does not claim one universally best source.

The held-out observations were not used for feature selection, fitting, tuning, or method choice. Full evidence and metric definitions are recorded in `docs/HOLDOUT_RESULTS_2e070898b34c589b.md` and `docs/XGBOOST_BLENDING_DESIGN.md`.

## Important limitations

- The historical rows are proxy evaluations because exact upstream model-run identity remains unverified; they are not reconstructed past operational issuances.
- GEFS spread is not a calibrated probability and is not a fourth XGBoost weight.
- Temperature and wind products are experimental forecast-only outputs. Available SYNOP observations are point observations, not 31-district truth; historical issue-time provenance and SYNOP wind measurement height remain unverified.
- The locked XGBoost skill result applies only to the three rainfall sources and Day 1. Rainfall weights are never reused for temperature or wind.
- Threshold guidance is configurable and experimental, not an official warning product.
- A full valid day means 00:00-00:00 IST coverage; it does not by itself imply 24 hours of advance notice.

Read `docs/PS81_REQUIREMENT_MATRIX.md` for implemented, experimental, and pending PS81 capabilities and `docs/DATA_INVENTORY.md` for source coverage and provenance.

## Operational entry points

```powershell
python -m backend.api.cli --config configs/ps81.karnataka.json load-xgboost
python -m backend.api.cli --config configs/ps81.karnataka.json run-district --district "Bengaluru Urban"
python -m backend.api.cli --config configs/ps81.karnataka.json run-statewide
python -m backend.api.cli --config configs/ps81.karnataka.json export-cycle --cycle-id CYCLE_ID
python -m backend.api.cli --config configs/ps81.karnataka.json routine --dry-run
```

These forecast commands can contact configured providers and write operational state. Review the configuration and use the dry-run/preflight path first.
