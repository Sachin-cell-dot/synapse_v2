import sqlite3
import unittest

from backend.utils.store import SCHEMA, hierarchical_historical_errors


class HierarchicalSkillTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.executescript(SCHEMA)
        for district in ("regional-a", "regional-b"):
            for source, error in (("gfs", 1.0), ("ifs_hres", 2.0), ("aifs", 3.0)):
                self.connection.execute(
                    """INSERT INTO skill_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (district, source, 0, "2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00", error, 0.0, "IMD", "test", "2026-01-03T00:00:00+00:00", "sha", "2026-01-04T00:00:00+00:00"),
                )

    def tearDown(self):
        self.connection.close()

    def test_uses_regional_lead_skill_when_district_is_new(self):
        errors, fallback = hierarchical_historical_errors(
            self.connection, district_id="new-district", region_district_ids=("regional-a", "regional-b"),
            statewide_district_ids=("regional-a", "regional-b"), lead_days=0,
            source_ids=("gfs", "ifs_hres", "aifs"), available_before_utc="2027-01-01T00:00:00+00:00", limit=60,
        )
        self.assertEqual(fallback, "regional_lead_skill_fallback")
        self.assertTrue(all(errors[source] for source in ("gfs", "ifs_hres", "aifs")))


if __name__ == "__main__":
    unittest.main()
