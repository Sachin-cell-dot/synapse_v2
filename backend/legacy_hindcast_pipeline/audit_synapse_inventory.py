import csv
import json
import math
import os
import re
import sys
from collections import Counter
from datetime import datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
OUT = os.path.join(ROOT, "inventory_audit.json")
SKIP_PARTS = {"node_modules", "node-runtime", "vendor", ".next", "__pycache__", "dist", ".vinext", ".wrangler"}
DATA_EXTS = {".csv", ".xls", ".xlsx", ".json", ".geojson"}
SOURCE_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".css", ".html", ".md", ".txt", ".toml", ".yaml", ".yml"}
NULL_STRINGS = {"", "na", "n/a", "nan", "null", "none", "missing"}
DATE_HINT = re.compile(r"date|time|valid|issue|run|start|end|period", re.I)


def rel(path):
    return os.path.relpath(path, ROOT).replace("\\", "/")


def scalar_dates(values):
    parsed = []
    for value in values:
        if value is None:
            continue
        s = str(value).strip()
        if not s or s.lower() in NULL_STRINGS:
            continue
        candidate = s[:19].replace("Z", "")
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
            try:
                parsed.append(datetime.strptime(candidate, fmt))
                break
            except ValueError:
                pass
    if not parsed:
        return None
    return {"min": min(parsed).isoformat(sep=" "), "max": max(parsed).isoformat(sep=" ")}


def audit_csv(path):
    with open(path, "r", encoding="utf-8-sig", newline="", errors="replace") as f:
        reader = csv.DictReader(f)
        cols = reader.fieldnames or []
        missing = Counter({c: 0 for c in cols})
        samples = {c: [] for c in cols}
        rows = 0
        for row in reader:
            rows += 1
            for c in cols:
                v = row.get(c)
                if v is None or str(v).strip().lower() in NULL_STRINGS:
                    missing[c] += 1
                elif len(samples[c]) < 100000:
                    samples[c].append(v)
    date_ranges = {}
    for c in cols:
        if DATE_HINT.search(c):
            dr = scalar_dates(samples[c])
            if dr:
                date_ranges[c] = dr
    return {"kind": "csv", "rows": rows, "columns": cols, "missing": dict(missing), "date_ranges": date_ranges}


def json_stats(obj):
    result = {"top_type": type(obj).__name__}
    if isinstance(obj, dict):
        result["top_keys"] = list(obj.keys())
        if obj.get("type") == "FeatureCollection" and isinstance(obj.get("features"), list):
            feats = obj["features"]
            result["feature_count"] = len(feats)
            props = []
            for f in feats:
                if isinstance(f, dict) and isinstance(f.get("properties"), dict):
                    props.append(f["properties"])
            keys = sorted({k for p in props for k in p})
            result["property_fields"] = keys
            result["missing_properties"] = {k: sum(1 for p in props if p.get(k) is None or str(p.get(k, "")).strip().lower() in NULL_STRINGS) for k in keys}
        else:
            for k, v in obj.items():
                if isinstance(v, list):
                    result.setdefault("list_lengths", {})[k] = len(v)
    elif isinstance(obj, list):
        result["rows"] = len(obj)
        dicts = [x for x in obj if isinstance(x, dict)]
        if dicts:
            keys = sorted({k for x in dicts for k in x})
            result["fields"] = keys
            result["missing"] = {k: sum(1 for x in dicts if x.get(k) is None or str(x.get(k, "")).strip().lower() in NULL_STRINGS) for k in keys}
    return result


def audit_json(path):
    if os.path.getsize(path) == 0:
        return {"kind": "json", "empty_file": True}
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        obj = json.load(f)
    result = {"kind": "geojson" if path.lower().endswith(".geojson") else "json"}
    result.update(json_stats(obj))
    result["content"] = obj if os.path.getsize(path) < 12000 else None
    return result


def audit_xls(path):
    try:
        import pandas as pd
        book = pd.ExcelFile(path)
        sheets = []
        for name in book.sheet_names:
            df = pd.read_excel(path, sheet_name=name)
            sheets.append({"name": name, "rows": int(len(df)), "columns": [str(c) for c in df.columns], "missing": {str(k): int(v) for k, v in df.isna().sum().items()}})
        return {"kind": "excel", "sheets": sheets}
    except Exception as e:
        return {"kind": "excel", "error": repr(e)}


def audit_source(path):
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        text = f.read()
    lines = text.splitlines()
    interesting = []
    for line in lines:
        s = line.strip()
        if s and (s.startswith(("def ", "class ", "export ", "function ", "const ", "interface ", "type ", "# ", "## ")) or "read_csv" in s or "to_csv" in s):
            interesting.append(s[:300])
        if len(interesting) >= 30:
            break
    return {"kind": "source", "lines": len(lines), "nonblank": sum(bool(x.strip()) for x in lines), "signals": interesting}


records = []
for base, dirs, files in os.walk(ROOT):
    dirs[:] = [d for d in dirs if d not in SKIP_PARTS]
    for name in files:
        path = os.path.join(base, name)
        ext = os.path.splitext(name)[1].lower()
        if ext not in DATA_EXTS | SOURCE_EXTS:
            continue
        item = {"path": rel(path), "bytes": os.path.getsize(path)}
        try:
            if ext == ".csv": item.update(audit_csv(path))
            elif ext in {".json", ".geojson"}: item.update(audit_json(path))
            elif ext in {".xls", ".xlsx"}: item.update(audit_xls(path))
            else: item.update(audit_source(path))
        except Exception as e:
            item.update({"error": repr(e)})
        records.append(item)

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(records, f, indent=2, ensure_ascii=False, default=str)
print(f"wrote {len(records)} records to {OUT}")
