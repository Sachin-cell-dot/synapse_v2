"""Leakage-safe SYNAPSE-WX rainfall collector and uploaded-SYNOP join.

Open-Meteo is queried only for precipitation_previous_day1. Uploaded SYNOP
observations are lagged to strictly before each valid IST calendar day.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

ENDPOINT = "https://previous-runs-api.open-meteo.com/v1/forecast"
MODELS = {"gfs": "ncep_gfs_seamless", "ifs_hres": "ecmwf_ifs", "aifs": "ecmwf_aifs025_single"}
VARIABLE = "precipitation_previous_day1"
SYNOPTIC_VALUE_COLUMNS = [
    "air_temperature_c", "dew_point_temperature_c", "station_pressure_hpa",
    "mslp_hpa", "wind_direction_deg", "wind_speed_ms",
    "horizontal_visibility_km", "total_cloud_amount_oktas",
    "cloud_base_height_m", "present_weather_code", "past_weather_code",
    "past_weather_2_code", "pressure_tendency_characteristic_code",
    "pressure_tendency_change_hpa", "precipitation_amount_mm",
    "precipitation_period_hours",
]


def point_in_ring(x, y, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][:2]; xj, yj = ring[j][:2]
        if ((yi > y) != (yj > y)) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-15) + xi:
            inside = not inside
        j = i
    return inside


def point_in_geometry(x, y, geometry):
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    for poly in polygons:
        if poly and point_in_ring(x, y, poly[0]) and not any(point_in_ring(x, y, hole) for hole in poly[1:]):
            return True
    return False


def all_coords(geometry):
    polys = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    return [p for poly in polys for ring in poly for p in ring]


def sample_polygon(feature, spacing, min_points, max_points):
    geometry = feature["geometry"]
    coords = all_coords(geometry)
    minx, maxx = min(p[0] for p in coords), max(p[0] for p in coords)
    miny, maxy = min(p[1] for p in coords), max(p[1] for p in coords)
    points = []
    y = math.floor(miny / spacing) * spacing + spacing / 2
    while y <= maxy:
        x = math.floor(minx / spacing) * spacing + spacing / 2
        while x <= maxx:
            if point_in_geometry(x, y, geometry): points.append((round(y, 6), round(x, 6)))
            x += spacing
        y += spacing
    centroid = (sum(p[1] for p in coords) / len(coords), sum(p[0] for p in coords) / len(coords))
    if point_in_geometry(centroid[1], centroid[0], geometry): points.append((round(centroid[0], 6), round(centroid[1], 6)))
    if len(points) < min_points:
        for div in (2, 4, 8):
            fine = spacing / div
            y = miny + fine / 2
            while y <= maxy and len(points) < min_points:
                x = minx + fine / 2
                while x <= maxx and len(points) < min_points:
                    p = (round(y, 6), round(x, 6))
                    if p not in points and point_in_geometry(x, y, geometry): points.append(p)
                    x += fine
                y += fine
    points = sorted(set(points))
    if len(points) > max_points:
        idx = [round(i * (len(points) - 1) / (max_points - 1)) for i in range(max_points)]
        points = [points[i] for i in sorted(set(idx))]
    return points, {"bbox": [minx, miny, maxx, maxy], "grid_spacing_degrees": spacing,
                    "weighting": "cos(latitude) area-proxy weighted mean over deterministic interior grid points"}


def sha256(data): return hashlib.sha256(data).hexdigest()


def fetch(url, attempts=2):
    last = None
    for attempt in range(attempts):
        try:
            req = Request(url, headers={"User-Agent": "SYNAPSE-WX/1.0"})
            with urlopen(req, timeout=35) as response:
                return response.read(), response.status, dict(response.headers.items())
        except Exception as exc:
            last = exc
            if attempt + 1 < attempts: time.sleep(2 ** attempt)
    raise last


def fetch_point(model, api_model, lat, lon, start, end, raw_path, resume):
    params = [("latitude", lat), ("longitude", lon), ("hourly", VARIABLE),
              ("start_date", start), ("end_date", end), ("timezone", "Asia/Kolkata"), ("models", api_model)]
    url = ENDPOINT + "?" + urlencode(params)
    if resume and raw_path.exists():
        body = raw_path.read_bytes(); status = 200; headers = {}; source = "cache"
    else:
        body, status, headers = fetch(url); raw_path.parent.mkdir(parents=True, exist_ok=True); raw_path.write_bytes(body); source = "api"
    payload = json.loads(body)
    values = payload.get("hourly", {}).get(VARIABLE, [])
    times = payload.get("hourly", {}).get("time", [])
    if len(values) != len(times): raise ValueError("hourly time/value length mismatch")
    frame = pd.DataFrame({"time": times, "rain": values})
    frame["date"] = pd.to_datetime(frame["time"], errors="coerce").dt.date.astype(str)
    daily = frame.groupby("date", as_index=False).rain.sum(min_count=1)
    meta = {"exact_url": url, "requested_model": model, "api_model": api_model,
            "variable": VARIABLE, "date_range": {"start": start, "end": end},
            "point": {"latitude": lat, "longitude": lon}, "http_status": status,
            "retrieval_source": source, "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            "response_sha256": sha256(body), "response_bytes": len(body),
            "response_metadata": {k: payload.get(k) for k in ["latitude", "longitude", "elevation", "generationtime_ms", "utc_offset_seconds", "timezone", "timezone_abbreviation", "model"]},
            "hourly_units": payload.get("hourly_units"), "hourly_rows": len(times),
            "non_null_hourly_values": sum(v is not None for v in values), "response_headers": headers}
    return daily, meta


def haversine_km(a, b):
    lat1, lon1 = map(math.radians, a); lat2, lon2 = map(math.radians, b)
    dlat, dlon = lat2-lat1, lon2-lon1
    x = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 6371.0088 * 2 * math.asin(math.sqrt(x))


def aggregate_model(point_frames, points, dates, min_coverage):
    rows = []
    indexed = [f.set_index("date").rain for f in point_frames]
    for date in dates:
        vals = []
        for series, (lat, lon) in zip(indexed, points):
            v = series.get(date, float("nan"))
            if pd.notna(v): vals.append((float(v), math.cos(math.radians(lat))))
        coverage = len(vals) / len(points) if points else 0
        value = sum(v*w for v,w in vals) / sum(w for _,w in vals) if vals else float("nan")
        rows.append((date, value, len(vals), coverage, "pass" if coverage >= min_coverage else "fail"))
    return rows


def synoptic_for_district(synop, center, dates, max_age_hours, max_distance_km):
    stations = synop[["station_id", "latitude", "longitude"]].drop_duplicates()
    stations["distance_km"] = stations.apply(lambda r: haversine_km(center, (r.latitude, r.longitude)), axis=1)
    station = stations.sort_values(["distance_km", "station_id"]).iloc[0]
    local = synop[synop.station_id == station.station_id].copy().sort_values("observation_time_utc")
    out = []
    for date in dates:
        cutoff = pd.Timestamp(date, tz="Asia/Kolkata").tz_convert("UTC")
        eligible = local[local.observation_time_utc < cutoff]
        latest = eligible.iloc[-1] if len(eligible) else None
        row = {"date": date, "synop_station_id": station.station_id,
               "synop_station_distance_km": round(float(station.distance_km), 3),
               "synop_cutoff_utc": cutoff.isoformat(), "synop_observation_time_utc": None,
               "synop_age_hours": None, "synop_temporal_status": "fail_no_prior_observation",
               "synop_spatial_status": "pass" if station.distance_km <= max_distance_km else "fail_station_too_distant"}
        if latest is not None:
            age = (cutoff - latest.observation_time_utc).total_seconds()/3600
            row.update({"synop_observation_time_utc": latest.observation_time_utc.isoformat(),
                        "synop_age_hours": round(age, 3),
                        "synop_temporal_status": "pass" if 0 < age <= max_age_hours else "fail_stale"})
            for c in SYNOPTIC_VALUE_COLUMNS: row["synop_" + c] = latest.get(c)
        out.append(row)
    return pd.DataFrame(out), station.to_dict()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    ap.add_argument("--synoptic-csv", type=Path, required=True)
    ap.add_argument("--district", default=None)
    ap.add_argument("--start-date", required=True); ap.add_argument("--end-date", required=True)
    ap.add_argument("--grid-spacing-degrees", type=float, default=0.25)
    ap.add_argument("--min-points", type=int, default=5); ap.add_argument("--max-points", type=int, default=12)
    ap.add_argument("--min-rainfall-point-coverage", type=float, default=0.8)
    ap.add_argument("--max-synop-age-hours", type=float, default=12)
    ap.add_argument("--max-synop-distance-km", type=float, default=150)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()
    root=args.project_root; out=root/"outputs"; raw=out/"synapse_wx_raw_api_forecasts"
    boundaries=json.loads((out/"adapt_wx_karnataka_district_boundaries.geojson").read_text(encoding="utf-8"))
    features=boundaries["features"]
    if args.district:
        features=[f for f in features if f["properties"]["imd_district"].casefold()==args.district.casefold() or str(f["properties"]["imd_district_code"])==args.district]
        if len(features)!=1: raise SystemExit(f"district selector matched {len(features)} features")
    imd=pd.read_csv(out/"karnataka_imd_daily.csv", dtype={"district_code":int})
    imd=imd[(imd.date>=args.start_date)&(imd.date<=args.end_date)]
    synop=pd.read_csv(args.synoptic_csv)
    synop["observation_time_utc"]=pd.to_datetime(synop.observation_time_utc,utc=True,errors="coerce")
    manifest={"endpoint":ENDPOINT,"rainfall_variable":VARIABLE,"lead_time":"previous_day1 (24 hours before each valid hour)","synoptic_source":str(args.synoptic_csv),"requests":[],"failures":[]}
    spatial={"method":"deterministic interior lon/lat grid; cosine-latitude area-proxy weighted mean","districts":[]}
    result=[]
    for feature in sorted(features,key=lambda f:int(f["properties"]["imd_district_code"])):
        p=feature["properties"]; code=int(p["imd_district_code"]); district=p["imd_district"]
        points, sampling=sample_polygon(feature,args.grid_spacing_degrees,args.min_points,args.max_points)
        coords=all_coords(feature["geometry"]); center=(sum(x[1] for x in coords)/len(coords),sum(x[0] for x in coords)/len(coords))
        dates=sorted(imd.loc[imd.district_code==code,"date"].unique())
        base=imd[(imd.district_code==code)][["date","district_code","district","division","imd_actual_mm"]].copy()
        model_quality={}
        for model,api_model in MODELS.items():
            frames=[]
            for i,(lat,lon) in enumerate(points):
                path=raw/str(code)/model/f"{args.start_date}_{args.end_date}_p{i:03d}.json"
                try:
                    daily,meta=fetch_point(model,api_model,lat,lon,args.start_date,args.end_date,path,args.resume)
                    frames.append(daily); manifest["requests"].append(meta)
                except Exception as exc:
                    manifest["failures"].append({"district_code":code,"district":district,"model":model,"point_index":i,"latitude":lat,"longitude":lon,"error":repr(exc)})
                    frames.append(pd.DataFrame(columns=["date","rain"]))
            agg=aggregate_model(frames,points,dates,args.min_rainfall_point_coverage)
            af=pd.DataFrame(agg,columns=["date",f"{model}_rain_mm",f"{model}_point_count",f"{model}_point_coverage",f"{model}_coverage_status"])
            base=base.merge(af,on="date",how="left",validate="one_to_one")
            model_quality[model]={"requested_points":len(points),"dates":len(dates),"failed_date_rows":int((af[f"{model}_coverage_status"]!="pass").sum())}
        syn,station=synoptic_for_district(synop,center,dates,args.max_synop_age_hours,args.max_synop_distance_km)
        base=base.merge(syn,on="date",how="left",validate="one_to_one")
        base["sample_point_count"]=len(points)
        base["spatial_coverage_status"]=base[[f"{m}_coverage_status" for m in MODELS]].apply(lambda r:"pass" if (r=="pass").all() else "fail",axis=1)
        base["common_coverage_status"]=base.apply(lambda r:"pass" if r.spatial_coverage_status=="pass" and r.synop_temporal_status=="pass" and r.synop_spatial_status=="pass" else "fail",axis=1)
        result.append(base)
        spatial["districts"].append({"district_code":code,"district":district,"sample_points":[{"latitude":a,"longitude":b,"weight":math.cos(math.radians(a))} for a,b in points],"sampling":sampling,"synoptic_station":station,"model_quality":model_quality})
    master=pd.concat(result,ignore_index=True).sort_values(["date","district_code"])
    master_path=out/"synapse_wx_statewide_forecast_synoptic_master.csv"; master.to_csv(master_path,index=False,float_format="%.4f")
    split_specs={"train":("2025-02-01","2025-12-31"),"validation":("2026-01-01","2026-04-30"),"test":("2026-05-01","2026-08-31")}
    split_summary={}
    for split,(lo,hi) in split_specs.items():
        part=master[(master.date>=lo)&(master.date<=hi)].copy()
        part["split"]=split
        part.to_csv(out/f"synapse_wx_statewide_{split}.csv",index=False,float_format="%.4f")
        split_summary[split]={"start":lo,"end":hi,"rows":len(part),"common_pass_rows":int((part.common_coverage_status=="pass").sum())}
    synop_archive=out/"synapse_wx_raw_api_synoptic"; synop_archive.mkdir(parents=True,exist_ok=True)
    archived_synop=synop_archive/"uploaded_decoded_synop.csv"
    if args.synoptic_csv.resolve()!=archived_synop.resolve(): shutil.copy2(args.synoptic_csv,archived_synop)
    synop_bytes=archived_synop.read_bytes()
    (synop_archive/"SOURCE_MANIFEST.json").write_text(json.dumps({
        "source_type":"user-uploaded decoded surface SYNOP observations",
        "open_meteo_used_for_synoptic":False,"source_path_at_collection":str(args.synoptic_csv),
        "archived_file":archived_synop.name,"sha256":sha256(synop_bytes),"bytes":len(synop_bytes),
        "rows":len(synop),"date_range_utc":{"start":synop.observation_time_utc.min().isoformat(),"end":synop.observation_time_utc.max().isoformat()},
        "fields":list(synop.columns)},indent=2),encoding="utf-8")
    quality={"status":"pass" if (master.common_coverage_status=="pass").all() and not manifest["failures"] else "fail",
             "training_allowed":False,"training_block_reason":"Training remains disabled until a full-scope common master passes all rainfall, synoptic temporal, and synoptic spatial checks.",
             "scope":{"districts":len(features),"start":args.start_date,"end":args.end_date,"rows":len(master)},
             "checks":{"duplicate_district_dates":int(master.duplicated(["date","district_code"]).sum()),
                       "imd_missing":int(master.imd_actual_mm.isna().sum()),
                       "rainfall_missing":{m:int(master[f"{m}_rain_mm"].isna().sum()) for m in MODELS},
                       "rainfall_spatial_fail_rows":int((master.spatial_coverage_status!="pass").sum()),
                       "synoptic_temporal_fail_rows":int((master.synop_temporal_status!="pass").sum()),
                       "synoptic_spatial_fail_rows":int((master.synop_spatial_status!="pass").sum()),
                       "synoptic_value_missing":{c:int(master["synop_"+c].isna().sum()) for c in SYNOPTIC_VALUE_COLUMNS},
                       "synoptic_observation_not_strictly_before_cutoff":int((pd.to_datetime(master.synop_observation_time_utc,utc=True)>=pd.to_datetime(master.synop_cutoff_utc,utc=True)).fillna(False).sum())},
             "chronological_splits":split_summary,
             "uploaded_synoptic_fields":SYNOPTIC_VALUE_COLUMNS,
             "unavailable_requested_upper_air_fields":["wind_850_u","wind_850_v","rh_850","z500","cape","precipitable_water"],
             "note":"Uploaded SYNOP contains surface station observations only. No unavailable upper-air fields were fabricated."}
    (out/"synapse_wx_api_request_manifest.json").write_text(json.dumps(manifest,indent=2,default=str),encoding="utf-8")
    (out/"synapse_wx_spatial_aggregation_report.json").write_text(json.dumps(spatial,indent=2,default=str),encoding="utf-8")
    (out/"synapse_wx_api_data_quality_report.json").write_text(json.dumps(quality,indent=2,default=str),encoding="utf-8")
    print(json.dumps(quality,indent=2))

if __name__=="__main__": main()
