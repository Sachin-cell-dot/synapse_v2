# Day-1 held-out results — artifact 2e070898b34c589b

This is the one-time May–August 2026 historical-proxy evaluation of the frozen Day-1 XGBoost artifact. The artifact, feature schema, hyperparameters and validation selection were locked before these scores were generated. No held-out value was used to tune, select features, retrain, or choose a preferred method.

Historical forecast run identity remains unverified. The source file contains aligned daily forecasts but lacks trustworthy issue and initialization timestamps, so these results are not an exact-run backtest.

## Paired overall results

All methods use the same 3,749 quality-approved district-days.

| Method | MAE mm | RMSE mm | Bias mm |
|---|---:|---:|---:|
| XGBoost error-weighted | 3.939 | 9.116 | -1.917 |
| Inverse-MAE | 4.066 | 8.676 | -0.722 |
| Equal weight | 4.176 | 8.801 | -0.632 |
| AIFS | 4.231 | 8.258 | 0.0005 |
| GFS | 4.513 | 10.457 | -2.178 |
| IFS HRES | 4.721 | 9.347 | 0.282 |

XGBoost has the lowest overall MAE, but AIFS beats it on RMSE and absolute bias. Inverse-MAE and equal weighting also beat XGBoost on RMSE and absolute bias. Across the 44 district, region, month and observed-intensity MAE scopes, XGBoost wins 29, AIFS 11, inverse-MAE 2, equal weight 1 and GFS 1. These held-out results do not trigger method reselection.

## Predeclared event thresholds

| Threshold mm | Method | Observed | Forecast | Hits | Misses | False alarms | POD | FAR | CSI |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | XGBoost | 1,724 | 1,737 | 1,126 | 598 | 611 | 0.653 | 0.352 | 0.482 |
| 1 | AIFS | 1,724 | 2,847 | 1,556 | 168 | 1,291 | 0.903 | 0.453 | 0.516 |
| 10 | XGBoost | 525 | 251 | 188 | 337 | 63 | 0.358 | 0.251 | 0.320 |
| 10 | AIFS | 525 | 425 | 272 | 253 | 153 | 0.518 | 0.360 | 0.401 |
| 25 | XGBoost | 186 | 67 | 42 | 144 | 25 | 0.226 | 0.373 | 0.199 |
| 25 | AIFS | 186 | 86 | 60 | 126 | 26 | 0.323 | 0.302 | 0.283 |
| 50 | XGBoost | 45 | 20 | 7 | 38 | 13 | 0.156 | 0.650 | 0.121 |
| 50 | AIFS | 45 | 20 | 9 | 36 | 11 | 0.200 | 0.550 | 0.161 |

AIFS has the highest CSI at every declared threshold. The versioned scorecard contains all six methods and represents undefined ratios as unavailable empty fields.

## Coverage and leakage rule

The calendar contains 3,813 possible district-days; the source master contains 3,749 observed rows. All three sources pass the quality contract on all 3,749 observed rows, giving 100% coverage of observed inputs and 98.32% coverage of the full calendar. The comparison denominator is the same 3,749 rows for every method.

For inverse-MAE, a historical observation is eligible only when `valid interval end + 120 hours < simulated Day-1 issue time`. This is deliberately conservative because actual historical IMD release timestamps are unavailable. The XGBoost validation expected-error MAE is a target-model diagnostic and is not the blended rainfall MAE reported here.

Versioned machine-readable outputs are under `outputs/operational/exports/holdout/` with prefix `day1-holdout-v1-2e070898b34c589b`.
