import _testenv  # noqa: F401  (isolated test database; must be first)
"""
Tests for research/dataset.py. All rows below are TEST FIXTURES with made-up
values; they are not the MPhil research dataset.
"""
import io
import json
import os
import shutil
import tempfile
import unittest

import pandas as pd

from research.dataset import validate, write_artifact, load_mapping, sha256_bytes
from research import ingest

MAPPING_PATH = os.path.join(os.path.dirname(__file__), "..", "research", "config", "research_30sku_mapping.json")
HEADER = "SKU_ID,Segment,Annual_Demand_Dbar,Demand_StdDev_Sigma,Unit_Cost_c,Ordering_Cost_s,Holding_Cost_h,Shortage_Cost_pi,Lead_Time_L,Unit_Volume_v\n"
GOOD = ["FX-01,Snacks,12000,900,150,500,30,60,7,0.0015",
        "FX-02,Biscuits,3650,400,80.25,500,16,40,14,0.001",
        "FX-03,Noodles,24000,1500,45,750,9,25,10,0.0022"]


def csv(rows):
    return (HEADER + "\n".join(rows) + "\n").encode()


def mapping(**units):
    m = load_mapping(MAPPING_PATH)
    m["expected_rows"] = 3
    m["units"].update(units)
    return m


CONFIRMED = dict(currency="PKR", demand_std_period="year", holding_cost_basis="per_unit_per_year",
                 shortage_cost_basis="per_unit_short", lead_time="days", unit_volume="m3")


class TestValidation(unittest.TestCase):
    def rules(self, res, level=None):
        return {i.rule for i in res.issues if level is None or i.level == level}

    def test_valid_file(self):
        res = validate(csv(GOOD), "fx.csv", mapping(**CONFIRMED))
        self.assertTrue(res.is_valid, res.report()["issues"])
        self.assertTrue(res.units_confirmed)
        self.assertEqual(len(res.data), 3)

    def test_values_not_altered(self):
        """Exact numeric values survive; nothing rounded, scaled or imputed."""
        res = validate(csv(GOOD), "fx.csv", mapping(**CONFIRMED))
        self.assertEqual(res.data.loc[1, "unit_cost"], 80.25)
        self.assertEqual(res.data.loc[0, "unit_volume"], 0.0015)
        self.assertEqual(list(res.data["sku_id"]), ["FX-01", "FX-02", "FX-03"])
        self.assertEqual(res.data["annual_demand"].tolist(), [12000, 3650, 24000])

    def test_units_unconfirmed_flagged_not_assumed(self):
        res = validate(csv(GOOD), "fx.csv", load_mapping(MAPPING_PATH))
        self.assertTrue(res.is_valid)                      # data itself is fine
        self.assertFalse(res.units_confirmed)
        self.assertIn("units_unconfirmed", self.rules(res, "warning"))
        self.assertIn("demand_std_period", res.report()["unconfirmed_units"])

    def test_missing_column(self):
        bad = (HEADER.replace("Lead_Time_L", "LeadTime") + GOOD[0] + "\n").encode()
        res = validate(bad, "fx.csv", mapping(**CONFIRMED))
        self.assertFalse(res.is_valid)
        self.assertIn("missing_column", self.rules(res, "error"))

    def test_each_error_rule(self):
        cases = {
            "missing_value": "FX-09,Snacks,,900,150,500,30,60,7,0.0015",
            "not_a_number": "FX-09,Snacks,12k,900,150,500,30,60,7,0.0015",
            "non_positive": "FX-09,Snacks,0,900,150,500,30,60,7,0.0015",
            "negative": "FX-09,Snacks,1000,-5,150,500,30,60,7,0.0015",
        }
        for rule, row in cases.items():
            res = validate(csv(GOOD[:2] + [row]), "fx.csv", mapping(**CONFIRMED))
            self.assertFalse(res.is_valid, rule)
            self.assertIn(rule, self.rules(res, "error"), rule)
            issue = next(i for i in res.issues if i.rule == rule)
            self.assertEqual(issue.rows, [3], rule)       # 1-based data row
        for col, row in (("unit_cost", "FX-09,S,10,1,0,500,30,60,7,0.001"),
                         ("ordering_cost", "FX-09,S,10,1,5,-1,30,60,7,0.001"),
                         ("holding_cost", "FX-09,S,10,1,5,5,0,60,7,0.001"),
                         ("lead_time", "FX-09,S,10,1,5,5,1,60,0,0.001"),
                         ("unit_volume", "FX-09,S,10,1,5,5,1,60,7,0")):
            res = validate(csv(GOOD[:2] + [row]), "fx.csv", mapping(**CONFIRMED))
            self.assertFalse(res.is_valid, col)

    def test_duplicate_sku(self):
        res = validate(csv(GOOD + [GOOD[0]]), "fx.csv", mapping(**CONFIRMED))
        self.assertIn("duplicate_sku", self.rules(res, "error"))
        self.assertEqual(len(res.data), 4)                 # nothing deleted

    def test_warnings_do_not_block(self):
        rows = ["FX-01,Snacks,12000,0,150,500,30,0,400,0.0015",       # zero std, zero pi, >1y lead time
                "FX-02,Biscuits,100,500,10,500,16,40,14,0.001",       # CV > 1 and h > c
                GOOD[2]]
        res = validate(csv(rows), "fx.csv", mapping(**CONFIRMED))
        self.assertTrue(res.is_valid)
        for r in ("zero_std", "zero_shortage_cost", "long_lead_time", "high_cv", "holding_ratio"):
            self.assertIn(r, self.rules(res, "warning"), r)

    def test_row_count_and_whitespace_warnings(self):
        res = validate(csv(GOOD[:2] + [" FX-03 ,Noodles,24000,1500,45,750,9,25,10,0.0022"]), "fx.csv",
                       dict(mapping(**CONFIRMED), expected_rows=30))
        self.assertIn("row_count", self.rules(res, "warning"))
        self.assertIn("surrounding_whitespace", self.rules(res, "warning"))
        self.assertEqual(res.data.loc[2, "sku_id"], " FX-03 ")   # kept as-is

    def test_xlsx_and_csv_equivalent(self):
        df = pd.read_csv(io.BytesIO(csv(GOOD)), dtype=str)
        buf = io.BytesIO(); df.to_excel(buf, index=False)
        a = validate(csv(GOOD), "fx.csv", mapping(**CONFIRMED)).data
        b = validate(buf.getvalue(), "fx.xlsx", mapping(**CONFIRMED)).data
        pd.testing.assert_frame_equal(a, b)

    def test_mapping_validation(self):
        m = load_mapping(MAPPING_PATH)
        with self.assertRaises(ValueError):
            load_mapping(dict(m, provenance="real"))
        bad = json.loads(json.dumps(m)); bad["units"]["lead_time"] = "fortnights"
        with self.assertRaises(ValueError):
            load_mapping(bad)


class TestArtifacts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_artifact_contents_and_fingerprint(self):
        raw = csv(GOOD)
        res = validate(raw, "fx.csv", mapping(**CONFIRMED))
        folder = write_artifact(res, raw, self.tmp)
        self.assertEqual(open(os.path.join(folder, "source.csv"), "rb").read(), raw)   # byte-identical
        meta = json.load(open(os.path.join(folder, "metadata.json")))
        self.assertEqual(meta["source_sha256"], sha256_bytes(raw))
        self.assertEqual(meta["provenance"], "research")
        self.assertTrue(meta["units_confirmed"])
        self.assertTrue(os.path.exists(os.path.join(folder, "validation_report.md")))
        again = write_artifact(validate(raw, "fx.csv", mapping(**CONFIRMED)), raw, self.tmp)
        self.assertEqual(folder, again)                    # deterministic location
        self.assertEqual(json.load(open(os.path.join(again, "metadata.json")))["processed_sha256"],
                         meta["processed_sha256"])

    def test_cli_exit_codes(self):
        good = os.path.join(self.tmp, "good.csv"); open(good, "wb").write(csv(GOOD))
        bad = os.path.join(self.tmp, "bad.csv"); open(bad, "wb").write(csv(GOOD + [GOOD[0]]))
        out = os.path.join(self.tmp, "out")
        self.assertEqual(ingest.main([good, "--out", out]), 0)
        self.assertEqual(ingest.main([bad, "--out", out]), 1)
        import contextlib
        with contextlib.redirect_stderr(io.StringIO()):     # expected error message
            self.assertEqual(ingest.main([os.path.join(self.tmp, "missing.csv"), "--out", out]), 2)


if __name__ == "__main__":
    unittest.main()


class TestResearchApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from main import app
        cls.c = TestClient(app); cls.c.__enter__()
        tok = cls.c.post("/api/auth/login", json={"username": "admin", "password": "admin123"}).json()["token"]
        cls.h = {"Authorization": f"Bearer {tok}"}

    @classmethod
    def tearDownClass(cls):
        cls.c.__exit__(None, None, None)

    def test_validate_endpoint(self):
        r = self.c.post("/api/research/validate", headers=self.h,
                        files={"file": ("fx.csv", io.BytesIO(csv(GOOD + [GOOD[0]])), "text/csv")})
        self.assertEqual(r.status_code, 200, r.text)
        rep = r.json()
        self.assertFalse(rep["is_valid"])
        self.assertFalse(rep["units_confirmed"])
        self.assertEqual(rep["rows"], 4)
        self.assertEqual(rep["provenance"], "research")

    def test_validate_requires_auth(self):
        r = self.c.post("/api/research/validate", files={"file": ("fx.csv", io.BytesIO(csv(GOOD)), "text/csv")})
        self.assertEqual(r.status_code, 401)
