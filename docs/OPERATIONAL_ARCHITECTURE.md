# SYNAPSE-WX operational extension

The audited historical hindcast remains unchanged. Operational forecasts are a separate, append-only product with explicit provenance.

## Data flow

1. Load and validate a versioned configuration.
2. Determine a compatible forecast cycle for every enabled source.
3. Retrieve each source independently and preserve the raw response and its hash.
4. Aggregate matched precipitation intervals to configured district geometries.
5. Read only verification records available before issuance.
6. Calculate weights independently by district and lead time.
7. Persist source values, exact weights, configuration hash, status, and blended output immutably.
8. Add verification later without rewriting the issued forecast.

## Historical skill bootstrap

The audited historical master can seed operational skill through the configured `historical_bootstrap` contract. Imported rows live in a dedicated immutable skill-history table and retain the source artifact hash, provider, classification, availability time, and lead time. Re-importing an identical artifact is idempotent; a conflicting record aborts rather than overwriting history.

Only Day-1 forecasts exist in the current audited master. Consequently, Day 1 can use the rolling adaptive weights immediately, while Days 2–6 must remain equal-weight cold starts until lead-specific histories are available.

## Run identity limitation

The Open-Meteo latest-forecast endpoint does not provide enough metadata to infer an exact upstream initialization time safely. The operational collector must therefore either use an explicitly selected archived/single run or store the run time as null with `run_identity_status=not_exposed_by_endpoint`. Retrieval time must never be presented as model initialization time.

## Configuration boundary

Operational values live in validated configuration. Application code operates on configured collections; it does not assume three models, 31 districts, specific dates, or Karnataka-specific labels. Frozen scientific parameters remain configurable but versioned, and every issued forecast records the configuration hash.

## Verified vertical slice

The Open-Meteo adapter and a one-district cycle have been verified against live GFS, IFS HRES, and AIFS responses. District sampling is geometry-driven, coordinate retrieval is batched using a configured limit, daily accumulation uses the configured timezone and start hour, and the resulting source values and blends are stored append-only. The latest-forecast endpoint still does not expose a trustworthy upstream initialization time, so the stored run identity is explicitly marked unavailable rather than inferred.

The statewide command uses one issue identifier across all configured districts and writes no cycle until every retrieval and calculation has completed. API rate limits are handled through configured pacing, bounded exponential retry, and a short-lived request cache. Failed partial collections therefore do not appear as issued forecasts.

## Routine operation

`python -m backend.api.cli --config configs/ps81.karnataka.json routine` is a thin orchestration command around the existing CLI functions. It validates the frozen artifact and all 31 geometries, computes the most recent configured IST issue slot, refuses a second 31-district issuance in that slot, runs one append-only statewide cycle, exports it, attempts the latest eligible IMD product, evaluates earlier cycles with imported verification, and appends one concise JSON event to `outputs/operational/logs/routine-health.jsonl`. IMD unavailability is recorded without invalidating a successfully issued forecast. Forecast/source failure is logged and propagated; the pipeline does not insert a partial cycle.

The no-write preflight is:

```powershell
python -m backend.api.cli --config configs/ps81.karnataka.json routine --dry-run
```

The configured daily slot is 06:00 IST. The following is documentation only; replace `<REPO>` and `<PYTHON>` with absolute paths, ensure the Windows machine timezone is India Standard Time, and run it manually if installation is desired. This repository does not register the task:

```powershell
schtasks /Create /TN "SYNAPSE-WX Karnataka Daily" /SC DAILY /ST 06:00 /TR "cmd /d /c \"cd /d <REPO> && <PYTHON> -m backend.api.cli --config configs\ps81.karnataka.json routine\"" /F
```

## Verified statewide cycle

Cycle `bc5f2a5972e54e5da32d11f893c6b229` contains 31 districts, 249 sampling points, 186 complete rainfall blends, 744 source rows, 2,232 temperature/wind source-statistic rows and 744 experimental variable blends. Day 1 uses the locked three-source XGBoost artifact; Days 2–6 use named equal-weight baselines; all 186 GEFS rows remain display-only. Issuance was `2026-09-26T02:03:44.472455Z` and the Day-1 full-day target began at `2026-09-26T18:30:00Z`, providing 16.44 hours of advance notice. The target interval is a complete 24-hour IST day, but the forecast was not issued 24 hours before its start.

Remaining operational gaps are external alerting, installed scheduling, reproducible exact upstream run selection, and verified lead-specific history for Days 2–6.
