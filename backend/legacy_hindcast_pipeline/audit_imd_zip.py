import io
import json
import os
import zipfile
import pandas as pd

ZIP_PATH = r"C:\Users\Sachin\Documents\Codex\2026-09-02\so\outputs\adapt_wx_raw_imd_xls_reports.zip"
OUT = r"C:\Users\Sachin\Desktop\SYNAPSE WX\imd_zip_audit.json"

records = []
with zipfile.ZipFile(ZIP_PATH) as z:
    for info in z.infolist():
        if info.is_dir() or not info.filename.lower().endswith((".xls", ".xlsx")):
            continue
        item = {"path": info.filename, "bytes": info.file_size}
        try:
            blob = z.read(info)
            book = pd.ExcelFile(io.BytesIO(blob))
            item["sheets"] = []
            for sheet in book.sheet_names:
                df = pd.read_excel(io.BytesIO(blob), sheet_name=sheet)
                item["sheets"].append({
                    "name": sheet,
                    "rows": int(len(df)),
                    "columns": [str(c) for c in df.columns],
                    "missing": {str(k): int(v) for k, v in df.isna().sum().items()},
                })
            period = pd.read_excel(io.BytesIO(blob), sheet_name="PERIOD", header=None)
            dated = None
            for r in range(period.shape[0]):
                for c in range(period.shape[1] - 1):
                    if str(period.iat[r, c]).strip().upper() == "DATED":
                        value = pd.to_datetime(period.iat[r, c + 1], dayfirst=True, errors="coerce")
                        if not pd.isna(value):
                            dated = value.date().isoformat()
            item["dated"] = dated
        except Exception as e:
            item["error"] = repr(e)
        records.append(item)

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(records, f, indent=2, ensure_ascii=False)
print(f"audited {len(records)} workbooks; errors={sum('error' in r for r in records)}")
