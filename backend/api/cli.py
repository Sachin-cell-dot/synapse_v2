from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

from ..data.bootstrap import import_skill_history
from ..inference.archive import bootstrap_archived_skill, reconstruct_missing_cycles
from ..utils.config import load_config
from ..evaluation.evaluate import evaluate_cycle
from ..inference.export import export_cycle
from ..ingestion.imd_realtime import fetch_imd_district_rainfall
from ..ingestion.open_meteo import fetch_point
from ..inference.pipeline import run_district_cycle, run_statewide_cycle
from ..utils.store import initialize_database
from ..evaluation.verification import import_verification
from ..training.xgboost_blend import train_model
from ..models.xgboost_blend import infer_expected_errors, load_active_model, load_frozen_model
from ..evaluation.xgboost_holdout import evaluate_frozen_holdout
from ..operations.routine import run_routine
from ..evaluation.synop import ingest_and_verify_synop
from ..ingestion.synop_previous_runs import fetch_station_previous_day, fetch_station_previous_runs


def main() -> None:
    parser = argparse.ArgumentParser(description="SYNAPSE-WX operational pipeline")
    parser.add_argument("--config", type=Path, required=True)
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("validate-config")
    subcommands.add_parser("init-db")
    subcommands.add_parser("bootstrap-history")
    subcommands.add_parser("bootstrap-archived-skill")
    subcommands.add_parser("reconstruct-missing-cycles")
    subcommands.add_parser("train-xgboost")
    subcommands.add_parser("load-xgboost")
    subcommands.add_parser("evaluate-xgboost-holdout")
    routine_parser = subcommands.add_parser("routine")
    routine_parser.add_argument("--dry-run", action="store_true")
    infer_xgb = subcommands.add_parser("infer-xgboost")
    infer_xgb.add_argument("--district", required=True)
    infer_xgb.add_argument("--month", type=int, required=True)
    infer_xgb.add_argument("--gfs", type=float, required=True)
    infer_xgb.add_argument("--ifs-hres", type=float, required=True)
    infer_xgb.add_argument("--aifs", type=float, required=True)
    fetch_parser = subcommands.add_parser("fetch-point")
    fetch_parser.add_argument("--source", required=True)
    fetch_parser.add_argument("--latitude", type=float, required=True)
    fetch_parser.add_argument("--longitude", type=float, required=True)
    district_parser = subcommands.add_parser("run-district")
    district_parser.add_argument("--district", required=True)
    subcommands.add_parser("run-statewide")
    export_parser = subcommands.add_parser("export-cycle")
    export_parser.add_argument("--cycle-id", required=True)
    evaluate_parser = subcommands.add_parser("evaluate-cycle")
    evaluate_parser.add_argument("--cycle-id", required=True)
    verification_parser = subcommands.add_parser("import-verification")
    verification_parser.add_argument("--file", type=Path, required=True)
    verification_parser.add_argument("--dry-run", action="store_true")
    imd_parser = subcommands.add_parser("fetch-imd")
    imd_parser.add_argument("--date", type=date.fromisoformat, required=True)
    ingest_imd_parser = subcommands.add_parser("ingest-imd")
    ingest_imd_parser.add_argument("--date", type=date.fromisoformat, required=True)
    subcommands.add_parser("ingest-latest-imd")
    synop_parser = subcommands.add_parser("verify-synop")
    synop_parser.add_argument("--observations", type=Path, required=True)
    synop_parser.add_argument("--forecasts", type=Path)
    synop_parser.add_argument("--start")
    synop_parser.add_argument("--end")
    synop_parser.add_argument("--station-id")
    synop_fetch = subcommands.add_parser("fetch-synop-station")
    synop_fetch.add_argument("--station-id", required=True)
    synop_fetch.add_argument("--latitude", type=float, required=True)
    synop_fetch.add_argument("--longitude", type=float, required=True)
    synop_fetch.add_argument("--date")
    synop_fetch.add_argument("--start")
    synop_fetch.add_argument("--end")
    synop_fetch.add_argument("--batch-days", type=int, default=31)
    synop_fetch.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    config = load_config(args.config)
    if args.command == "validate-config":
        result = {
            "status": "pass",
            "schema_version": config.data["schema_version"],
            "configuration_sha256": config.sha256,
            "enabled_sources": [source["id"] for source in config.enabled_sources],
        }
    elif args.command == "init-db":
        database_path = config.resolve(config.data["storage"]["database_path"])
        initialize_database(database_path)
        result = {"status": "pass", "database": str(database_path), "configuration_sha256": config.sha256}
    elif args.command == "bootstrap-history":
        result = import_skill_history(config)
    elif args.command == "bootstrap-archived-skill":
        result = bootstrap_archived_skill(config)
    elif args.command == "reconstruct-missing-cycles":
        result = reconstruct_missing_cycles(config)
    elif args.command == "train-xgboost":
        result = train_model(config)
    elif args.command == "load-xgboost":
        xgb = config.data["xgboost"]
        loaded = load_frozen_model(config.resolve(xgb["artifact_directory"]), artifact_version=xgb["frozen_artifact_version"], feature_schema_sha256=xgb["frozen_feature_schema_sha256"]) if xgb["training_frozen"] else load_active_model(config.resolve(xgb["artifact_directory"]))
        result = {"status": "pass", "artifact_version": loaded.artifact_version, "model_type": loaded.metadata["model_type"], "feature_schema_sha256": loaded.feature_schema_sha256, "source_ids": loaded.metadata["source_ids"], "held_out_period_not_loaded_for_fit_or_tuning": loaded.metadata["held_out_period_not_loaded_for_fit_or_tuning"]}
    elif args.command == "evaluate-xgboost-holdout":
        result = evaluate_frozen_holdout(config)
    elif args.command == "routine":
        result = run_routine(config, dry_run=args.dry_run)
    elif args.command == "infer-xgboost":
        xgb = config.data["xgboost"]
        loaded = load_frozen_model(config.resolve(xgb["artifact_directory"]), artifact_version=xgb["frozen_artifact_version"], feature_schema_sha256=xgb["frozen_feature_schema_sha256"]) if xgb["training_frozen"] else load_active_model(config.resolve(xgb["artifact_directory"]))
        geography = config.data["geography"]
        from ..preprocessing.geography import load_districts
        district_matches = [district for district in load_districts(config.resolve(geography["boundary_path"]), geography) if district.district_id.casefold() == args.district.casefold() or district.name.casefold() == args.district.casefold()]
        if len(district_matches) != 1:
            parser.error("--district must match exactly one configured district")
        district = district_matches[0]
        forecasts = {"gfs": args.gfs, "ifs_hres": args.ifs_hres, "aifs": args.aifs}
        predicted, weights = infer_expected_errors(loaded, forecasts=forecasts, district_id=district.district_id, region=district.division or "unassigned", month=args.month, floor_mm=float(config.data["xgboost"]["predicted_error_floor_mm"]))
        result = {"status": "pass", "district_id": district.district_id, "month": args.month, "method": "xgboost_expected_absolute_error", "artifact_version": loaded.artifact_version, "feature_schema_sha256": loaded.feature_schema_sha256, "predicted_errors_mm": predicted, "weights": weights, "blend_mm": sum(forecasts[source] * weights[source] for source in weights), "fallback": None}
    elif args.command == "fetch-point":
        matching_sources = [source for source in config.enabled_sources if source["id"] == args.source]
        if len(matching_sources) != 1:
            parser.error(f"--source must name one enabled configured source: {[source['id'] for source in config.enabled_sources]}")
        point = fetch_point(
            source=matching_sources[0],
            latitude=args.latitude,
            longitude=args.longitude,
            forecast=config.data["forecast"],
            raw_directory=config.resolve(config.data["storage"]["raw_response_directory"]),
            cache_directory=config.resolve(config.data["forecast"]["request_cache_directory"]),
        )
        result = {
            "status": "pass",
            "source": point.source_id,
            "requested_model_id": point.requested_model_id,
            "hourly_rows": len(point.times),
            "first_valid_time": point.times[0] if point.times else None,
            "last_valid_time": point.times[-1] if point.times else None,
            "non_null_values": sum(value is not None for value in point.precipitation),
            "raw_response_sha256": point.response_sha256,
            "raw_path": str(point.raw_path),
        }
    elif args.command == "run-district":
        result = run_district_cycle(config, args.district)
    elif args.command == "run-statewide":
        result = run_statewide_cycle(config)
    elif args.command == "export-cycle":
        result = export_cycle(config, args.cycle_id)
    elif args.command == "evaluate-cycle":
        result = evaluate_cycle(config, args.cycle_id)
    elif args.command == "import-verification":
        result = import_verification(config, args.file, dry_run=args.dry_run)
    elif args.command == "fetch-imd":
        result = fetch_imd_district_rainfall(config, args.date)
    elif args.command == "verify-synop":
        result = ingest_and_verify_synop(config, args.observations, args.forecasts, start=args.start, end=args.end, station_id=args.station_id)
    elif args.command == "fetch-synop-station":
        if args.date:
            result = fetch_station_previous_day(station_id=args.station_id, latitude=args.latitude, longitude=args.longitude, day=args.date,
                sources=config.enabled_sources, output_directory=config.resolve(config.data["storage"]["export_directory"])/"synop"/"probe",
                endpoint=config.data["archive"]["previous_runs_api_base_url"], user_agent=config.data["forecast"]["user_agent"])
        elif args.start and args.end:
            result = fetch_station_previous_runs(station_id=args.station_id, latitude=args.latitude, longitude=args.longitude, start=args.start, end=args.end,
                sources=config.enabled_sources, output_directory=config.resolve(config.data["storage"]["export_directory"])/"synop"/"probe",
                endpoint=config.data["archive"]["previous_runs_api_base_url"], user_agent=config.data["forecast"]["user_agent"], batch_days=args.batch_days, resume=args.resume)
        else:
            parser.error("fetch-synop-station requires --date or both --start and --end")
    else:
        target_date = args.date if args.command == "ingest-imd" else datetime.now(ZoneInfo(config.data["forecast"]["source_timezone"])).date() - timedelta(days=int(config.data["imd_realtime"]["latest_available_day_offset"]))
        fetched = fetch_imd_district_rainfall(config, target_date)
        imported = import_verification(config, Path(fetched["district_csv_path"]))
        result = {"status": "pass", "fetch": fetched, "import": imported}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
