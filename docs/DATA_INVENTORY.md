# Data inventory

## Checked-in forecast and observation data

| Asset | Rows / period | Variables and role | Important constraint |
|---|---:|---|---|
| `datasets/synapse_wx_statewide_forecast_synoptic_master.csv` | 17,435; 2025-02-01..2026-08-31 | District IMD rainfall; GFS, IFS HRES, AIFS rainfall; coverage fields; matched SYNOP temperature, dew point, pressure, wind, visibility, cloud and weather | Common complete three-source period begins 2025-10-01; SYNOP coverage failed and is not a training input |
| `datasets/karnataka_imd_daily.csv` | 17,435; 2025-02-01..2026-08-31 | 31-district rainfall observations, normals and departure | Observation, not an issue-time predictor |
| `datasets/karnataka_imd_train.csv` | 10,059; 2025-02-01..2025-12-31 | Rainfall observations | Broader than the three-model training overlap |
| `datasets/karnataka_imd_validation.csv` | 3,627; 2026-01-01..2026-04-30 | Rainfall observations | Verification/model-selection period |
| `datasets/karnataka_imd_test.csv` | 3,749; 2026-05-01..2026-08-31 | Rainfall observations | Frozen held-out verification period |
| `datasets/decoded_synop.csv` | 22,152 observations | Station temperature, dew point, pressure, wind, visibility, cloud, weather and precipitation | Not statewide/district-complete; timestamps are observations, not future forecasts |
| Coastal CSV family | 3 districts; 2025-02-01..2026-08-31 | Coastal subsets, baselines and frozen predictions | Not sufficient for Karnataka-only statewide acceptance |
| `datasets/adapt_wx_karnataka_district_boundaries.geojson` | Karnataka district polygons | Geometry candidate | Example config incorrectly points to a missing generated copy in `outputs/` |

The common statewide three-model usable overlap is 2025-10-01..2026-08-31: train 92 days, validation 120 days, test 123 days, across 31 districts after complete-case filtering. The master has rainfall forecasts only for GFS, IFS HRES and AIFS. It has no ensemble members, quantiles/probabilities, forecast temperature, forecast wind, issue timestamp, or explicit lead beyond the legacy Day-1 interpretation.

## Temperature and wind observation audit

`decoded_synop.csv` is the only checked-in source of measured air temperature and surface wind. The district join uses three stations: 43003 (19.1167 N, 72.8500 E), 43128 (17.4500 N, 78.4667 E), and 43295 (12.9667 N, 77.5833 E), from 2025-02-24 00:00 UTC through 2026-08-31 21:00 UTC. Their respective row/temperature/wind-speed/wind-direction counts are 4,431/4,342/4,340/4,339; 4,430/4,369/4,369/2,925; and 4,428/4,264/4,283/4,091. Station 43003 is outside Karnataka and the other two are only point stations. Under the existing 150 km spatial rule, only 10 of 31 districts pass. These observations therefore cannot serve as statewide district Tmax/Tmin or maximum-wind truth and no temperature/wind XGBoost model is trained.

The station verification path (`verify-synop`) now ingests decoded stations with IDs, coordinates, UTC observation times, normalized °C and m/s units, range/missing-value checks and SHA-256 provenance. A forecast is eligible only at the same valid timestamp and when request provenance proves that the station coordinate was explicitly requested; no nearest-district substitution is allowed. Returned grid coordinates and station-to-grid distance are retained as representativeness fields. A bounded 2026-08-31 probe for Bengaluru WMO 43295 requested 12.966667 N, 77.583333 E and returned GFS 12.945007/77.578125 (2.474 km), IFS HRES 12.970123/77.563640 (2.168 km), and AIFS 13.0/77.5 (9.760 km). All sources returned 24 hourly temperature and wind fields. The eight three-hourly SYNOP reports yielded 48 point pairs (eight per source and variable). The `previous_day1` product proves prior-day forecast semantics, but exact historical run/issue timestamps are not exposed; every pair and metric is labelled `unverified_exact_previous_day1_product`.

Reproduction: first run `python -m backend.api.cli --config configs/ps81.karnataka.json fetch-synop-station --station-id 43295 --latitude 12.966667 --longitude 77.583333 --date 2026-08-31`, then pass its forecast CSV to `verify-synop --observations datasets/decoded_synop.csv --station-id 43295 --start 2026-08-31 --end 2026-08-31 --forecasts <csv>`. Issue times after valid times are rejected; forecast/observation values never enter operational blending or model training.

The extended Bengaluru comparison uses resumable 31-day batches over all 554 observed dates (2025-02-24..2026-08-31). It generated 54 successful source/batch responses, no request failures, and 21,998 matched station-time/source/variable pairs. GFS and AIFS each match 4,264 temperature and 4,283 wind observations across 553 days. IFS HRES matches 2,427 temperature observations on 319 days and 2,477 wind observations on 324 days; its response fields are null for temperature during 2025-02-24..2025-09-30 and 2026-03-26..2026-04-09, and for wind during 2025-02-24..2025-09-30 and 2026-04-27..2026-05-06. On 2026-07-04 the SYNOP station has no usable temperature or wind observation. The forecast field is explicitly 10 m wind, but the decoded SYNOP file and its raw message column contain no instrument-height metadata; wind-height comparability is therefore unverified and the wind scores are point comparisons with that limitation, not fully harmonized 10 m validation.

The station metric export contains two explicit scopes. `full_availability` retains every eligible source-specific pair. `common_three_source` requires GFS, IFS HRES and AIFS forecasts plus the identical SYNOP value at the same timestamp: temperature N=2,427 over 319 days and wind N=2,477 over 324 days for each source. Both scopes retain `unverified_exact_previous_day1_product`; wind rows additionally retain `synop_height_unverified_vs_forecast_10m`.

The operational deterministic adapter now requests `temperature_2m`, `wind_speed_10m`, and `wind_direction_10m` alongside rainfall. A bounded Bengaluru probe on 2026-09-26 returned °C, m/s, and degrees for GFS `ncep_gfs_seamless`, IFS HRES `ecmwf_ifs`, and AIFS `ecmwf_aifs025_single`, with 48 hourly values and no missing values for those fields. `wind_gusts_10m` is excluded because AIFS returned 48/48 nulls. Temperature/wind operational outputs are forecast-only; the equal-weight blends are explicitly experimental and are not verified skill products.

## Generated/local-only contracts

All operational state and UI forecast assets are intentionally excluded from Git under `outputs/`. The missing files required for a useful fresh run are the configured boundary, historical bootstrap master, dashboard forecast CSV, and (after execution) SQLite/raw/cache/export artifacts. The backend can create database directories, but it cannot infer or generate the configured boundary/master automatically.

## Genuine ensemble product

The operational configuration now includes NOAA GEFS 0.25° ensemble mean (`ncep_gefs025_ensemble_mean`) through the Open-Meteo Ensemble Mean API. A bounded Bengaluru/coastal probe returned hourly `precipitation` and `precipitation_spread` in mm without missing values. Returned grid points differed from requested coordinates, so ensemble point coverage is evaluated independently. An actual historical request succeeded for 2026-06-24; the API rejected 2026-03-15 and reported 2026-06-24 as the allowed start at probe time. This is insufficient for a chronologically matched four-source version of the frozen evaluation. See `docs/GFS_ENS_PROBE_MANIFEST.json`.

GEFS mean and spread are stored as display-only guidance. Daily spread is explicitly labelled as a root-sum-square hourly-standard-deviation independence proxy; it is neither a calibrated probability nor verified four-source skill.

## Safe modeling contract for the next segment

Every training row should be keyed by district, issue time, valid interval, lead and source-run identity. Features must be reproducible as-of issue time. Observations and realized errors may be labels/history only when their recorded availability precedes the prediction issue time. Fit preprocessing and XGBoost exclusively on training dates; use validation for selection/calibration; touch the frozen test period only once for final evaluation.
