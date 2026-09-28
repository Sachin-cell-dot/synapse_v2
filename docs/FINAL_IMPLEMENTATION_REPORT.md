# PS81 Karnataka MVP final implementation report

## Status

- **Implemented:** Karnataka-only 31-district geometry contract; append-only operational storage; GFS, IFS HRES and AIFS rainfall; locked Day-1 XGBoost expected-error weighting; GEFS mean/spread display; dashboard map/selectors; rainfall verification; duplicate-safe routine CLI.
- **Experimental:** temperature Tmax/Tmin, maximum 10 m wind, circular mean direction, their equal-weight forecast-only blends, and configurable rainfall/heat/wind threshold guidance.
- **Pending:** authoritative statewide district temperature/wind observations, calibrated probabilities, exact latest-endpoint run identity, verified Days 2–6 adaptive skill, external monitoring/alerting and installed scheduling.

## Frozen rainfall evidence

Artifact `2e070898b34c589b` and feature-schema SHA-256 `174016864c787f3dd50d04486c7f29302663d2f4f0178ca923438d499ee2ed0e` remain frozen. The May–August 2026 test contains 3,749 identical paired district-days for every method. XGBoost has the best overall MAE at 3.939 mm. AIFS has the best RMSE (8.258 mm), absolute bias (0.000486 mm), and heavy-rain CSI at the predeclared 50 mm threshold (0.1607). Historical run identity remains unverified legacy daily alignment, not an exact-run backtest.

## Statewide operational evidence

Cycle `bc5f2a5972e54e5da32d11f893c6b229` was issued once with 31 districts and 249 points. It contains 186/186 complete rainfall blends, 744 complete source rows, 2,232 complete deterministic temperature/wind statistic rows, and 744 complete experimental variable blends. Day 1 has 31 locked XGBoost rows with only GFS/IFS HRES/AIFS weights; Days 2–6 have 155 equal-weight baseline rows. GEFS has 186 complete rows labelled `display_only_not_in_locked_blend`.

The target is six complete 00:00–00:00 IST days. Issuance at `2026-09-26T02:03:44.472455Z` preceded the Day-1 start at `2026-09-26T18:30:00Z` by 16.44 hours. “Full-day target” describes the 24-hour accumulation interval; it does not imply 24 hours of advance notice.

## Exact reproduction and validation commands

```powershell
python -m backend.api.cli --config configs/ps81.karnataka.json validate-config
python -m backend.api.cli --config configs/ps81.karnataka.json load-xgboost
python -m backend.api.cli --config configs/ps81.karnataka.json routine --dry-run
python -m pytest -q
Push-Location frontend
& 'C:\Program Files\nodejs\npm.cmd' test
& 'C:\Program Files\nodejs\npm.cmd' run build
Pop-Location
git diff --check
```

The mutating scheduled command is intentionally separate and must not be used merely to reproduce tests:

```powershell
python -m backend.api.cli --config configs/ps81.karnataka.json routine
```

The current dry run detects statewide cycle `bc5f2a5972e54e5da32d11f893c6b229` in the `2026-09-26T06:00[Asia/Kolkata]` slot and will not issue a duplicate. No Task Scheduler task is installed by this project.
