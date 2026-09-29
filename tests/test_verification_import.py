import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from backend.utils.config import OperationalConfig
from backend.evaluation.verification import import_verification


class VerificationImportTests(unittest.TestCase):
    def test_import_is_idempotent_and_conflicts_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            boundary = {
                "type": "FeatureCollection",
                "features": [{
                    "type": "Feature",
                    "properties": {"district_code": 1, "district": "Test"},
                    "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
                }],
            }
            (root / "districts.geojson").write_text(json.dumps(boundary), encoding="utf-8")
            source = root / "verification.csv"
            source.write_text("date,district_code,imd_actual_mm\n2026-09-01,1,4.5\n", encoding="utf-8")
            data = {
                "storage": {"database_path": "operational.sqlite3"},
                "geography": {"boundary_path": "districts.geojson", "district_id_property": "district_code", "district_name_property": "district", "division_property": None},
                "forecast": {"source_timezone": "Asia/Kolkata", "daily_accumulation_start_hour": 0},
                "verification": {"provider": "IMD", "classification": "observation", "unit": "mm", "date_column": "date", "district_id_column": "district_code", "value_column": "imd_actual_mm", "availability_time_column": None, "reject_unknown_districts": True},
            }
            config = OperationalConfig(root / "config.json", root, data, hashlib.sha256(b"test").hexdigest())
            dry_run = import_verification(config, source, dry_run=True)
            self.assertEqual(dry_run["validated_rows"], 1)
            self.assertFalse((root / "operational.sqlite3").exists())
            first = import_verification(config, source)
            second = import_verification(config, source)
            self.assertEqual(first["inserted"], 1)
            self.assertEqual(second["inserted"], 0)
            self.assertEqual(second["unchanged"], 1)
            source.write_text("date,district_code,imd_actual_mm\n2026-09-01,1,9.0\n", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                import_verification(config, source)
            with closing(sqlite3.connect(root / "operational.sqlite3")) as connection:
                self.assertEqual(connection.execute("SELECT value_mm FROM verification").fetchone()[0], 4.5)


if __name__ == "__main__":
    unittest.main()
