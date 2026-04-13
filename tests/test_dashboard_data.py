import unittest
import tempfile
from pathlib import Path

from dashboard_data import load_dashboard_data, rows_for_items


class DashboardDataTests(unittest.TestCase):
    def test_rows_include_provenance_and_duplicate_context(self):
        [row] = rows_for_items([
            {
                "business_name": "Route",
                "location": "Philadelphia, PA",
                "asking_price": 100000,
                "cash_flow_annual": 50000,
                "source_url": "https://example.com/route",
                "_duplicate_count": 2,
                "_duplicate_sources": ["BizBuySell", "BusinessBroker"],
            }
        ])

        self.assertEqual(row["confidence_level"], "medium")
        self.assertIn("cash_flow: scraped", row["provenance_summary"])
        self.assertEqual(row["duplicate_count"], 2)
        self.assertIn("BizBuySell", row["duplicate_sources"])

    def test_source_label_falls_back_to_source_url_domain(self):
        [row] = rows_for_items([
            {
                "business_name": "Pizza Shop",
                "location": "Philadelphia, PA",
                "source_url": "https://www.bizbuysell.com/Business-Opportunity/example/123/",
            }
        ])

        self.assertEqual(row["source"], "BizBuySell")

    def test_load_dashboard_data_reads_csv_uploads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "listings.csv"
            path.write_text("business_name,location\nRoute,Philadelphia PA\n")

            rows = load_dashboard_data(path)

        self.assertEqual(rows[0]["business_name"], "Route")


if __name__ == "__main__":
    unittest.main()
