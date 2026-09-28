import json
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
audit = json.loads((HERE / "inventory_audit.json").read_text(encoding="utf-8"))
xls = json.loads((HERE / "imd_zip_audit.json").read_text(encoding="utf-8"))

def esc(v):
    return str(v).replace("|", "\\|").replace("\n", " ")

def missing_text(r):
    m = r.get("missing", {})
    nz = [f"{k}: {v}" for k, v in m.items() if v]
    if r.get("missing_properties"):
        nz += [f"{k}: {v}" for k, v in r["missing_properties"].items() if v]
    return "; ".join(nz) or "None"

def dates_text(r):
    d = r.get("date_ranges", {})
    return "; ".join(f"{k}: {v['min'][:10]} to {v['max'][:10]}" for k,v in d.items()) or "N/A"

purposes = {
"outputs/adapt_wx_current_dataset_manifest.csv":"Packaging manifest describing 13 current datasets/raw-report bundle entries.",
"outputs/coastal_adapt_wx_dashboard_forecasts.csv":"Dashboard-ready replay of the held-out coastal test period; source forecasts, adaptive weights, and blend; not live forecasts.",
"adapt-wx-dashboard/public/coastal_adapt_wx_dashboard_forecasts.csv":"Deployed public copy of the dashboard hindcast CSV (byte-identical role to outputs copy).",
"outputs/coastal_adapt_wx_test_predictions.csv":"Existing held-out coastal test predictions and dynamic weights.",
"outputs/coastal_adapt_wx_validation_predictions.csv":"Existing coastal validation predictions and dynamic weights.",
"outputs/coastal_karnataka_baseline_metrics.csv":"Per-model and baseline verification metrics by comparison scope/district.",
"outputs/coastal_karnataka_imd_daily.csv":"Coastal IMD realised rainfall observations with fixed point coordinates.",
"outputs/coastal_karnataka_master_24h.csv":"Coastal IMD observations joined to GFS, IFS HRES, and provisional AIFS ~24 h forecasts.",
"outputs/coastal_karnataka_train_all_records.csv":"Coastal chronological training split retaining partial model coverage.",
"outputs/coastal_karnataka_train_common_three_models.csv":"Coastal training subset where all three model forecasts exist.",
"outputs/coastal_karnataka_validation_all_records.csv":"Coastal chronological validation split retaining partial model coverage.",
"outputs/coastal_karnataka_validation_common_three_models.csv":"Coastal validation subset where all three model forecasts exist.",
"outputs/coastal_karnataka_test_all_records.csv":"Coastal chronological test split retaining partial model coverage.",
"outputs/coastal_karnataka_test_common_three_models.csv":"Coastal test subset where all three model forecasts exist.",
"outputs/karnataka_imd_daily.csv":"Statewide IMD realised rainfall observations for 31 districts.",
"outputs/karnataka_imd_train.csv":"Statewide observation-only training split.",
"outputs/karnataka_imd_validation.csv":"Statewide observation-only validation split.",
"outputs/karnataka_imd_test.csv":"Statewide observation-only test split.",
"work/api_smoke.csv":"First partial API smoke-test merge; only 6 GFS cells populated.",
"work/api_smoke2.csv":"Second partial API smoke-test merge; 6 GFS and 6 IFS cells populated.",
"work/api_smoke3.csv":"Third API smoke-test merge; 6 cells populated for each model.",
}

json_purposes = {
".vscode/tasks.json":"VS Code task configuration; invokes the existing training script.",
"adapt-wx-dashboard/.openai/hosting.json":"Sites hosting project identifier and optional D1/R2 bindings.",
"adapt-wx-dashboard/.oxfmtrc.json":"Formatter configuration.",
"adapt-wx-dashboard/.oxlintrc.json":"Lint configuration.",
"adapt-wx-dashboard/components.json":"shadcn/UI component generator configuration.",
"adapt-wx-dashboard/package.json":"Dashboard package metadata, scripts, and dependencies.",
"adapt-wx-dashboard/package-lock.json":"Locked npm dependency graph.",
"outputs/adapt_wx_karnataka_district_mapping.json":"31-row source-to-IMD/LGD district name/code mapping.",
"outputs/karnataka_district_boundary_mapping.json":"Duplicate/current 31-row district name/code mapping.",
"outputs/coastal_adapt_wx_model_config.json":"Existing adaptive ensemble configuration.",
"outputs/coastal_adapt_wx_model_report.json":"Existing validation/test metrics and leakage-control report.",
"outputs/coastal_karnataka_baseline_audit.json":"Forecast coverage and baseline provenance audit.",
"outputs/coastal_karnataka_imd_quality.json":"Coastal IMD extraction completeness/quality report.",
"outputs/coastal_karnataka_split_manifest.json":"Coastal split periods, row counts, coverage, and leakage rule.",
"outputs/karnataka_imd_quality.json":"Statewide IMD completeness, missing dates/districts, duplicates, and conflicts.",
"outputs/karnataka_imd_split_manifest.json":"Statewide split periods, row counts, and leakage rule.",
"outputs/openmeteo_request_manifest.json":"URLs/model identifiers/lead-time manifest for the full coastal forecast fetch.",
"work/api_smoke.json":"Request manifest for first smoke test.",
"work/api_smoke2.json":"Request manifest for second smoke test.",
"work/api_smoke3.json":"Request manifest for third smoke test.",
"work/overpass_karnataka_districts.json":"Empty failed/placeholder Overpass output.",
"work/git-helper/package.json":"Dependency declaration for the site-push helper.",
"work/git-helper/package-lock.json":"Locked dependency graph for the site-push helper.",
}

source_purposes = {
"README.md":"Project scope, data refresh sequence, leakage rule, and current limitations.",
"requirements.txt":"Python runtime dependencies (numpy, pandas, xlrd).",
"fetch_openmeteo_previous_runs.py":"Fetches model previous-run precipitation and aggregates hourly values to IST days.",
"extract_imd_karnataka.py":"Extracts/quality-checks 31-district IMD observations from raw XLS reports.",
"extract_imd_coastal.py":"Extracts/quality-checks three coastal districts from raw XLS reports.",
"make_imd_splits.py":"Creates statewide chronological observation splits.",
"make_experiment_splits.py":"Creates coastal all-record and common-three-model chronological splits.",
"verify_coastal_baselines.py":"Computes individual-model and simple ensemble baseline metrics.",
"train_adapt_wx_mvp.py":"Existing rolling inverse-MAE adaptive ensemble training/evaluation script; inspected, not run.",
"build_dashboard_dataset.py":"Builds leakage-safe dashboard hindcast rows from existing split data.",
"test_dashboard_dataset.py":"Unit tests for weight leakage/order and sum-to-one behavior.",
"map_karnataka_boundaries.py":"Maps boundary names/LGD codes to the 31 IMD district names/codes.",
"package_current_datasets.py":"Packages current CSV datasets and raw IMD reports.",
"adapt-wx-dashboard/app/page.tsx":"Main client dashboard: parses hindcast CSV, filters district/date, charts forecasts and trust weights.",
"adapt-wx-dashboard/app/layout.tsx":"Dashboard metadata and root HTML layout.",
"adapt-wx-dashboard/app/globals.css":"Global dashboard theme and responsive layout styles.",
"adapt-wx-dashboard/hooks/use-mobile.ts":"Mobile-breakpoint React hook.",
"adapt-wx-dashboard/lib/utils.ts":"Shared CSS class-name merge helper.",
"adapt-wx-dashboard/next.config.ts":"Minimal Next/Vinext configuration.",
"adapt-wx-dashboard/next-env.d.ts":"Generated Next/Vinext TypeScript declarations.",
"adapt-wx-dashboard/vite.config.ts":"Vite/Vinext/Sites/Cloudflare build and local binding configuration.",
"work/git-helper/push-site.mjs":"Helper that commits/pushes the dashboard site using isomorphic-git.",
}

lines = ["# SYNAPSE-WX data inventory", "", "Generated from a read-only inspection of the project on 2026-09-03. No model training was run. Counts exclude header rows. `N/A` means a date range is not meaningful for that file; it does not mean a range was guessed.", "",
"## Executive findings", "",
"- The only joined model dataset is Coastal Karnataka (Dakshina Kannada, Udupi, Uttara Kannada), not all 31 districts.",
"- IMD target: `imd_actual_mm` in derived CSVs; raw IMD XLS field: `ACTUAL`. Units are millimetres and the target is daily realised rainfall.",
"- Model fields: `gfs_rain_mm`, `ifs_hres_rain_mm`, `aifs_rain_mm` in modelling tables; dashboard copies rename these to `gfs_forecast_mm`, `ifs_hres_forecast_mm`, `aifs_forecast_mm`.",
"- Forecasts come from Open-Meteo `precipitation_previous_day1`, summed by Asia/Kolkata calendar date. The manifest describes this as approximately 24 hours before valid time. Exact issue/run timestamps are not stored.",
"- No current synoptic-condition variables are present. There are no pressure, wind, humidity, circulation, monsoon-trough, low-pressure-system, radar, satellite, or other synoptic fields. The current trust engine therefore uses historical errors only.",
"- `imd_normal_mm` is climatological normal, not current synoptic state. `imd_departure_percent` uses realised rainfall and is unavailable at issue time.",
"- AIFS is explicitly marked provisional. The saved requests name `ecmwf_aifs025_single`, but every manifest `api_response_model` is null; raw API responses are not preserved.",
"- Existing prediction/model-report files prove training occurred previously, but this inventory task did not execute training.", "",
"## Canonical keys and fields", "",
"| Role | Fields | Notes |", "|---|---|---|",
"| Date | `date` | Valid/local calendar date; exact model issue timestamp absent. |",
"| District name/code | `district`, `district_code` | CSV keys; coastal codes 492, 493, 494. |",
"| Statewide boundary mapping | `imd_district`, `imd_district_code`, `source_district_name`, `lgd_district_code` | GeoJSON additionally retains source boundary attributes. |",
"| Raw IMD identity | `CODE`, `NAME` | In each raw XLS `DATA` sheet. |",
"| Target | `imd_actual_mm` / raw `ACTUAL` | Realised daily rainfall, unavailable at forecast issue time. |",
"| Base forecasts | `gfs_rain_mm`, `ifs_hres_rain_mm`, `aifs_rain_mm` | Coastal master/splits. |",
"| Dashboard aliases | `gfs_forecast_mm`, `ifs_hres_forecast_mm`, `aifs_forecast_mm` | Same model roles in hindcast export. |",
"| Static/context | `division` or `region`, `latitude`, `longitude`, `imd_normal_mm` | Geography/static climatology, not synoptic state. |",
"| Post-event | `imd_departure_percent`, `source_report` | Not available at forecast issue time. |", "",
"## Train / validation / test periods", "",
"| Scope | Split | Dates | Rows | Model coverage |", "|---|---|---:|---:|---|",
"| Statewide IMD only | Train | 2025-02-01 to 2025-12-31 | 10,059 | No model fields |",
"| Statewide IMD only | Validation | 2026-01-01 to 2026-04-30 | 3,627 | No model fields |",
"| Statewide IMD only | Test | 2026-05-01 to 2026-08-31 | 3,749 | No model fields |",
"| Coastal all records | Train | 2025-02-01 to 2025-12-31 | 993 | GFS 993; IFS 276; AIFS 942 |",
"| Coastal common-three | Train | 2025-10-01 to 2025-12-31 | 276 | All three complete |",
"| Coastal all/common-three | Validation | 2026-01-01 to 2026-04-30 | 354 | All three complete |",
"| Coastal all/common-three | Test | 2026-05-01 to 2026-08-31 | 369 | All three complete |", "",
"The statewide observation files are not a usable three-model training set. Coastal split manifests prohibit validation/test rows from choosing weights or methodology; for date T, the existing adaptive code uses only errors from dates before T.", "",
"## Forecast-issue-time availability", "",
"| Field/group | Available at issue time? | Evidence/caveat |", "|---|---|---|",
"| `district_code`, `district`, `region`/`division`, latitude/longitude, boundary fields | Yes | Static identifiers/geography. |",
"| `imd_normal_mm` | Yes, if the normal table is preloaded | Static climatological normal; not synoptic. |",
"| GFS `gfs_rain_mm` / dashboard alias | Yes, approximately | `previous_day1`; exact run/issue timestamp absent. |",
"| IFS HRES `ifs_hres_rain_mm` / dashboard alias | Yes, approximately | `previous_day1`; available only from 2025-10-01 in the current coastal master. |",
"| AIFS `aifs_rain_mm` / dashboard alias | Provisionally yes | `previous_day1`; model identity provenance incomplete (`api_response_model: null`). |",
"| Prior-date IMD errors and derived weights | Yes, conditionally | Only if the operational store contains observations strictly before valid date T. |",
"| `imd_actual_mm` | No | Ground truth becomes known after the forecast period. |",
"| `imd_departure_percent` | No | Derived using realised `ACTUAL`. |",
"| `source_report` | No | Verification provenance for the realised report. |",
"| Existing validation/test prediction outputs | No for a live forecast | Hindcast/evaluation artifacts, not operational inputs. |",
"| Current synoptic variables | Not present | No synoptic fields/files exist to assess availability. |", "",
"## CSV and GeoJSON inventory", "", "| File | Purpose | Rows/features | Date range | Columns/property fields | Missing values |", "|---|---|---:|---|---|---|"]

for r in audit:
    if r.get("kind") not in {"csv", "geojson"}: continue
    p = r["path"]
    purpose = purposes.get(p, "District boundary geometry with IMD/LGD mapping." if r.get("kind") == "geojson" else "Tabular project artifact.")
    count = r.get("rows") if r.get("rows") is not None else r.get("feature_count")
    cols = r.get("columns") or r.get("property_fields") or []
    lines.append(f"| `{esc(p)}` | {esc(purpose)} | {count} | {dates_text(r)} | `{esc(', '.join(cols))}` | {esc(missing_text(r))} |")

lines += ["", "Notes: `coastal_karnataka_master_24h.csv` has 1,716 rows over 2025-02-01 to 2026-08-31; missing cells are `imd_normal_mm` 1, IFS HRES 717, AIFS 51, GFS 0. Statewide `karnataka_imd_daily.csv` has 17,435 rows and one missing `imd_normal_mm`. The quality report records 332 missing statewide district-days, five fully missing report dates, and additional partial dates.", "",
"## JSON inventory", "", "| File | Purpose | Structure / count |", "|---|---|---|"]
for r in audit:
    if r.get("kind") != "json": continue
    p=r["path"]
    purpose=json_purposes.get(p,"Project configuration or metadata JSON.")
    if r.get("empty_file"): struct="Empty file (0 bytes)"
    elif r.get("rows") is not None: struct=f"Array; {r['rows']} records; fields: {', '.join(r.get('fields',[]))}; missing values: {missing_text(r)}"
    else: struct=f"Object; keys: {', '.join(r.get('top_keys',[]))}"
    lines.append(f"| `{esc(p)}` | {esc(purpose)} | {esc(struct)} |")

lines += ["", "## Excel inventory", "",
"### Standalone workbook", "",
"- `work/sample.xls`: one representative IMD daily report. `PERIOD` has 1 data row and fields `STARTS, ENDS, OFFICE, FILENO, DATED` (no missing values); `DATA` has 44 rows and fields `CODE, NAME, ACTUAL, NORMAL, DEP` (missing counts 9, 4, 9, 9, 9 respectively, largely non-district/blank report rows); `MESSAGE` has 13 rows with one unnamed column (7 blanks).", "",
"### Raw-report archive", "",
"- `outputs/adapt_wx_raw_imd_xls_reports.zip`: 573 original IMD `.xls` daily reports, all opened successfully. Workbook dates span 2025-02-01 to 2026-08-31. Every workbook contains `PERIOD`, `DATA`, and `MESSAGE`; `DATA` provides raw district fields `CODE`, `NAME`, target `ACTUAL`, normal `NORMAL`, and departure `DEP`. Row/missing counts below are the physical parsed-sheet counts, including report headings/totals/blanks; the extractors retain only recognised Karnataka district codes.", "",
"| Archived XLS file | Report date | PERIOD rows | DATA rows | MESSAGE rows | DATA missing (`CODE/NAME/ACTUAL/NORMAL/DEP`) |", "|---|---:|---:|---:|---:|---|"]
for r in xls:
    by={s['name']:s for s in r.get('sheets',[])}
    data=by.get('DATA',{})
    miss=data.get('missing',{})
    m='/'.join(str(miss.get(k,'N/A')) for k in ['CODE','NAME','ACTUAL','NORMAL','DEP'])
    lines.append(f"| `{esc(r['path'])}` | {r.get('dated') or 'unresolved'} | {by.get('PERIOD',{}).get('rows','N/A')} | {data.get('rows','N/A')} | {by.get('MESSAGE',{}).get('rows','N/A')} | {m} |")

lines += ["", "## Source-file inventory", "", "Generated build output (`dist/`), dependency trees (`node_modules/`, `work/vendor/`, `work/node-runtime/`), caches, binary images, and ZIP binaries are not source files and are excluded from this source table. Every project-owned source/configuration file outside those directories was read.", "", "| File | Lines | Purpose |", "|---|---:|---|"]
for r in audit:
    if r.get("kind") != "source": continue
    p=r['path']
    if p.startswith('outputs/') and p.endswith('.md'):
        purpose="Generated coastal baseline metric report."
    elif '/components/ui/' in p:
        purpose=f"Reusable shadcn UI primitive: {Path(p).stem.replace('-', ' ')}."
    else:
        purpose=source_purposes.get(p,"Project source/support file.")
    lines.append(f"| `{esc(p)}` | {r.get('lines','N/A')} | {esc(purpose)} |")

lines += ["", "## Trust-engine gap", "",
"Historical performance inputs exist (prior IMD errors, rolling window, inverse-MAE weights). Current synoptic-condition inputs do not. Before implementing the required two-part trust engine, a separately sourced, issue-time-stamped synoptic dataset and explicit availability policy are required. No synoptic columns should be inferred from rainfall forecasts, climatological normals, realised departures, dashboard labels, or model weights.", "",
"## Provenance cautions", "",
"- `openmeteo_request_manifest.json` preserves request URLs but not raw responses; `api_response_model` is null for every request.",
"- The raw forecasts are point extractions at fixed coastal coordinates, not district-polygon averages.",
"- Dashboard files are held-out hindcast replays dated 2026-05-01 through 2026-08-31, not live operational forecasts.",
"- Smoke-test CSVs are incomplete test artifacts and must not be used as training data.",
"- Existing trained outputs and the VS Code training task were inspected only; neither was executed during this inventory.", ""]

(HERE / "DATA_INVENTORY.md").write_text("\n".join(lines), encoding="utf-8")
print(f"wrote {len(lines)} lines")
