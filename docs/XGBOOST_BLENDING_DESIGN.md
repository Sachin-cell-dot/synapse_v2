# Day-1 XGBoost rainfall blending design

## Purpose

The model predicts each source's expected absolute Day-1 rainfall error in millimetres. It does not predict blend weights directly. Positive predicted errors are floored and converted to normalized inverse-error weights; the issued blend is the weighted sum of the same complete GFS, IFS HRES and AIFS district totals used by the operational pipeline.

Feature importance, if inspected separately, describes model sensitivity and must never be displayed or stored as a blend weight.

## Historical contract and split

Training input is the quality-approved common three-model portion of `datasets/synapse_wx_statewide_forecast_synoptic_master.csv`. Every accepted district/day must have all three forecast values, IMD verification, passing source coverage flags, a unique district/date key and a configured Karnataka district.

- Fit period: 2025-10-01 through 2025-12-31.
- Hyperparameter selection: 2026-01-01 through 2026-04-30.
- Held-out period: 2026-05-01 through 2026-08-31. The builder skips these rows before parsing forecasts or targets; this period is not evaluated, tuned on, or fitted in this segment.
- Final operational artifact: refit on the approved fit plus validation rows after selecting parameters on validation.

The historical file has daily aligned values but lacks trustworthy issue timestamps and upstream initialization identities. Artifact metadata therefore labels it `unknown_legacy_daily_forecast_alignment_not_exact_run`. Results from this data must not be described as an exact-run backtest.

## Row and feature schema

Each district/day expands to one row per source. The target is `abs(source rainfall - IMD rainfall)`. Forecast-available features are source rainfall, all three aligned source values, model spread, source identity, district, region, and cyclical month. IMD rainfall, realized error, verification availability, target date, split, and post-event fields are excluded from features.

No rolling-error feature is used in version 1. The legacy master cannot prove that observations were available before a simulated issuance. A later rolling feature may be added only from records with `verification_available_at_utc < simulated_issue_time_utc`.

## Artifact and inference contract

`train-xgboost` writes a native XGBoost JSON model, immutable metadata JSON and an `active.json` manifest under the configured ignored artifact directory. Metadata records the content-derived artifact version, model and schema hashes, category schema, source contract, periods, row counts, selected parameters, validation expected-error MAE, historical uncertainty label and the fact that feature importance is not a weight.

The loader verifies both metadata and model hashes plus the exact feature-schema hash. Day-1 operational inference runs only when all three configured sources are complete and ordered exactly as the artifact contract. It persists:

- source-specific predicted errors;
- normalized weights actually used;
- blended rainfall;
- method and named fallback;
- artifact version and feature-schema hash.

If the artifact is missing/invalid or the three-source contract is incomplete, Day 1 uses a named inverse-MAE or equal-weight fallback. Days 2–6 retain their separately named inverse-MAE/equal-weight baselines.

## Commands

```powershell
python -m backend.api.cli --config configs/ps81.karnataka.json train-xgboost
python -m backend.api.cli --config configs/ps81.karnataka.json load-xgboost
python -m backend.api.cli --config configs/ps81.karnataka.json infer-xgboost --district 506 --month 9 --gfs 0 --ifs-hres 0.38 --aifs 0
```

The explicit inference command is read-only. Persistence occurs atomically when a new operational district or statewide cycle is issued; it never rewrites an existing cycle.

## Frozen held-out evaluation

Artifact `2e070898b34c589b` and feature schema `174016864c787f3dd50d04486c7f29302663d2f4f0178ca923438d499ee2ed0e` are frozen in the PS81 configuration. The training command refuses to retrain while that lock is active. The one-time evaluation command is idempotent and will not regenerate an existing version:

```powershell
python -m backend.api.cli --config configs/ps81.karnataka.json evaluate-xgboost-holdout
```

See `docs/HOLDOUT_RESULTS_2e070898b34c589b.md` for the locked May–August results and limitations.
