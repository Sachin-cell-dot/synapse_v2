from __future__ import annotations

import csv
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

VARIABLES = {"temperature_2m_previous_day1": "temperature_2m", "wind_speed_10m_previous_day1": "wind_speed_10m"}


def fetch_station_previous_day(*, station_id: str, latitude: float, longitude: float, day: str, sources: list[dict], output_directory: Path, endpoint: str, user_agent: str) -> dict:
    return fetch_station_previous_runs(station_id=station_id, latitude=latitude, longitude=longitude, start=day, end=day,
        sources=sources, output_directory=output_directory, endpoint=endpoint, user_agent=user_agent, batch_days=1, resume=True)


def fetch_station_previous_runs(*, station_id: str, latitude: float, longitude: float, start: str, end: str, sources: list[dict], output_directory: Path, endpoint: str, user_agent: str, batch_days: int = 31, resume: bool = True) -> dict:
    output_directory.mkdir(parents=True, exist_ok=True); raw_directory = output_directory / "raw"; raw_directory.mkdir(exist_ok=True)
    rows, requests = [], []
    failures=[]; cursor=date.fromisoformat(start); final=date.fromisoformat(end)
    if batch_days < 1: raise ValueError("batch_days must be positive")
    while cursor <= final:
        batch_end=min(final,cursor+timedelta(days=batch_days-1)); batch_start_text=cursor.isoformat(); batch_end_text=batch_end.isoformat()
        for source in sources:
            params = {"latitude":latitude,"longitude":longitude,"hourly":",".join(VARIABLES),"start_date":batch_start_text,"end_date":batch_end_text,"timezone":"UTC","wind_speed_unit":"ms","models":source["api_model"]}
            url = f"{endpoint}?{urlencode(params)}"; request_url_sha256 = hashlib.sha256(url.encode()).hexdigest()
            cache_path=raw_directory/f"{source['id']}-{batch_start_text}-{batch_end_text}-{request_url_sha256}.json"
            try:
                if resume and cache_path.exists(): body=cache_path.read_bytes(); status=200; retrieval="cache"
                else:
                    with urlopen(Request(url, headers={"User-Agent":user_agent}), timeout=35) as response: body=response.read(); status=response.status
                    cache_path.write_bytes(body); retrieval="api"
                digest=hashlib.sha256(body).hexdigest(); payload=json.loads(body); returned_lat=float(payload["latitude"]); returned_lon=float(payload["longitude"])
                times=payload["hourly"]["time"]; units=payload["hourly_units"]
                for api_variable, variable in VARIABLES.items():
                    values=payload["hourly"].get(api_variable, [])
                    for valid, value in zip(times, values):
                        rows.append({"station_id":station_id,"source_id":source["id"],"requested_model_id":source["api_model"],
                            "requested_latitude":latitude,"requested_longitude":longitude,"returned_grid_latitude":returned_lat,"returned_grid_longitude":returned_lon,
                            "latitude":returned_lat,"longitude":returned_lon,"issue_time_utc":"","issue_time_status":"unverified_exact_previous_day1_product",
                            "valid_time_utc":valid+":00Z","variable_id":variable,"value":"" if value is None else value,"unit":units.get(api_variable,""),
                            "request_url_sha256":request_url_sha256,"response_sha256":digest})
                requests.append({"station_id":station_id,"source_id":source["id"],"requested_model_id":source["api_model"],"batch_start":batch_start_text,"batch_end":batch_end_text,"requested_coordinates":[latitude,longitude],
                    "returned_grid_coordinates":[returned_lat,returned_lon],"http_status":status,"retrieval":retrieval,"retrieved_at_utc":datetime.now(timezone.utc).isoformat(),
                    "request_url":url,"request_url_sha256":request_url_sha256,"response_sha256":digest,"raw_path":str(cache_path),"hourly_units":units,
                    "fields":list(payload["hourly"]),"issue_time_status":"unverified_exact_previous_day1_product"})
            except Exception as error:
                failures.append({"source_id":source["id"],"batch_start":batch_start_text,"batch_end":batch_end_text,"error":repr(error),"request_url_sha256":request_url_sha256})
        cursor=batch_end+timedelta(days=1)
    csv_path=output_directory/f"synop_station_{station_id}_{start}_{end}_previous_day1.csv"
    with csv_path.open("w",encoding="utf-8",newline="") as handle:
        writer=csv.DictWriter(handle,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    manifest_path=output_directory/f"synop_station_{station_id}_{start}_{end}_manifest.json"
    manifest_path.write_text(json.dumps({"classification":"station-requested gridded forecast","station_id":station_id,"start":start,"end":end,"batch_days":batch_days,"requests":requests,"failures":failures},indent=2),encoding="utf-8")
    return {"status":"pass" if not failures else "partial","rows":len(rows),"forecast_csv":str(csv_path),"manifest":str(manifest_path),"requests":requests,"failures":failures}
