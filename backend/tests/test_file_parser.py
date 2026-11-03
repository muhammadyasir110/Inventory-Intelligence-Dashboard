import _testenv  # noqa: F401  (isolated test database; must be first)
import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils.file_parser import validate_and_parse_inventory


class TestFileParser(unittest.TestCase):

    def test_valid_csv_parsing(self):
        csv_content = b"""SKU,Product Name,Category,Quantity On Hand,Reorder Point,Safety Stock,Unit Cost,Unit Price,Lead Time Days,Daily Avg Demand
SKU-001,Widget Alpha,Hardware,50,20,10,12.50,19.99,7,2.5
SKU-002,Widget Beta,Hardware,10,15,5,8.00,14.00,5,1.8
"""
        df, report = validate_and_parse_inventory("test.csv", csv_content)
        self.assertTrue(report["is_valid"])
        self.assertEqual(len(df), 2)
        self.assertEqual(report["total_rows"], 2)
        self.assertEqual(len(report["errors"]), 0)

    def test_duplicate_sku_detection(self):
        csv_content = b"""SKU,Product Name,Quantity On Hand,Unit Cost
SKU-DUP,Item 1,20,10.0
SKU-DUP,Item 2,30,12.0
"""
        df, report = validate_and_parse_inventory("dup.csv", csv_content)
        # Should flag warning or error for duplicate SKUs
        self.assertTrue(any("duplicate" in w.lower() for w in report["warnings"] + report["errors"]))

    def test_negative_values_detection(self):
        csv_content = b"""SKU,Product Name,Quantity On Hand,Unit Cost
SKU-NEG,Item Negative,-15,10.0
"""
        df, report = validate_and_parse_inventory("neg.csv", csv_content)
        self.assertTrue(any("negative" in w.lower() for w in report["warnings"] + report["errors"]))

    def test_column_alias_normalization(self):
        csv_content = b"""itemcode,itemname,stock,costprice
ITEM-X,Gadget X,100,25.0
"""
        df, report = validate_and_parse_inventory("aliases.csv", csv_content)
        self.assertIn("sku", df.columns)
        self.assertIn("product_name", df.columns)
        self.assertIn("quantity_on_hand", df.columns)
        self.assertIn("unit_cost", df.columns)

    def test_tsv_and_txt_parsing(self):
        tsv_content = b"SKU\tProduct Name\tQuantity On Hand\tUnit Cost\nSKU-TSV1\tTest Item\t45\t15.5\n"
        df, report = validate_and_parse_inventory("inventory.tsv", tsv_content)
        self.assertTrue(report["is_valid"])
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["sku"], "SKU-TSV1")

    def test_pdf_parsing(self):
        import io
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas

        buffer = io.BytesIO()
        p = canvas.Canvas(buffer, pagesize=letter)
        p.drawString(100, 750, "SKU    Product Name    Quantity On Hand    Unit Cost")
        p.drawString(100, 730, "SKU-PDF1    Wireless Adapter    150    25.50")
        p.save()
        pdf_bytes = buffer.getvalue()

        df, report = validate_and_parse_inventory("invoice.pdf", pdf_bytes)
        self.assertTrue(report["is_valid"])
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["sku"], "SKU-PDF1")


if __name__ == "__main__":
    unittest.main()



class TestParserIntegrityV22(unittest.TestCase):
    """v2.2: the parser must report every value it did not read from the file."""

    def test_research_schema_refused(self):
        csv_content = (b"SKU_ID,Segment,Annual_Demand_Dbar,Demand_StdDev_Sigma,Unit_Cost_c,Ordering_Cost_s,"
                       b"Holding_Cost_h,Shortage_Cost_pi,Lead_Time_L,Unit_Volume_v\n"
                       b"FX-01,A,12000,900,150,500,30,60,7,0.002\n")
        df, report = validate_and_parse_inventory("probe.csv", csv_content)
        self.assertFalse(report["is_valid"])
        self.assertIn("research", report["errors"][0].lower())

    def test_derivations_reported(self):
        csv_content = b"SKU,Product Name,Quantity On Hand,Unit Price\nA1,Item,90,10\nA2,Item2,,20\n"
        df, report = validate_and_parse_inventory("d.csv", csv_content)
        d = report["derivations"]
        for f in ("lead_time_days", "reorder_point", "safety_stock", "max_stock_level", "unit_cost", "quantity_on_hand"):
            self.assertIn(f, d, f)
        self.assertEqual(d["quantity_on_hand"][0]["rows"], 1)        # the one empty cell
        self.assertTrue(any("Derived/defaulted" in w for w in report["warnings"]))

    def test_no_derivations_when_complete(self):
        csv_content = (b"SKU,Product Name,Category,Quantity On Hand,Reorder Point,Safety Stock,Max Stock Level,"
                       b"Unit Cost,Unit Price,Lead Time Days,Daily Avg Demand,Supplier,Warehouse\n"
                       b"A1,Item,C,50,20,10,80,12.5,19.99,7,2.5,S,W\n")
        df, report = validate_and_parse_inventory("full.csv", csv_content)
        self.assertEqual(report["derivations"], {})
        self.assertEqual(df.iloc[0]["unit_cost"], 12.5)

    def test_sales_order_columns_not_mapped_to_inventory(self):
        csv_content = (b"ORDERNUMBER,QUANTITYORDERED,PRICEEACH,MSRP,CUSTOMERNAME,CITY,PRODUCTLINE,PRODUCTCODE\n"
                       b"10107,30,95.7,95,Land of Toys,NYC,Motorcycles,S10_1678\n")
        df, report = validate_and_parse_inventory("sales.csv", csv_content)
        m = report["column_mapping"]
        self.assertNotEqual(m.get("quantity_on_hand"), "QUANTITYORDERED")
        self.assertNotIn(m.get("unit_cost"), ("PRICEEACH", "MSRP"))
        self.assertNotEqual(m.get("supplier"), "CUSTOMERNAME")
        self.assertNotEqual(m.get("warehouse"), "CITY")
        self.assertEqual(m.get("sku"), "PRODUCTCODE")

    def test_negative_values_rejected_not_clipped(self):
        df, report = validate_and_parse_inventory("n.csv", b"SKU,Product Name,Quantity On Hand,Unit Cost\nA,B,-15,10\n")
        self.assertFalse(report["is_valid"])
        self.assertEqual(df.iloc[0]["quantity_on_hand"], -15)


class TestParserIntegrity251(unittest.TestCase):
    """2.5.1: explicit date formats per cell; no invented unit cost or price."""

    def test_mixed_date_formats_and_unreadable_dates_reported(self):
        import warnings
        csv = (b"sku,product_name,quantity_on_hand,unit_cost,daily_avg_demand,expiry_date\n"
               b"A,a,1,2,1,31/12/2026\nB,b,1,2,1,2026-11-30\nC,c,1,2,1,05.01.2027\nD,d,1,2,1,12/31/2026\nE,e,1,2,1,\n")
        with warnings.catch_warnings():
            warnings.simplefilter("error")          # the 2.5.0 parser emitted a pandas UserWarning here
            df, rep = validate_and_parse_inventory("x.csv", csv)
        got = [None if str(d) == "NaT" else d.date().isoformat() for d in df["expiry_date"]]
        self.assertEqual(got, ["2026-12-31", "2026-11-30", "2027-01-05", None, None])
        self.assertEqual([e["row"] for e in rep["row_errors"] if "date" in e["error"]], [5])   # empty cell is fine

    def test_missing_cost_and_price_not_invented(self):
        csv = (b"sku,product_name,quantity_on_hand,unit_cost,unit_price,daily_avg_demand\n"
               b"A,a,5,0,100,1\nB,b,5,40,0,1\n")
        df, rep = validate_and_parse_inventory("x.csv", csv)
        self.assertEqual(df["unit_cost"].tolist(), [0.0, 40.0])     # 2.5.0: 70.0 (0.70 x price)
        self.assertEqual(df["unit_price"].tolist(), [100.0, 0.0])   # 2.5.0: 54.0 (1.35 x cost)
        self.assertTrue(any("no unit cost" in w for w in rep["warnings"]))
