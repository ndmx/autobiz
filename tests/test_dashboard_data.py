import unittest

from dashboard_data import rows_for_items


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


if __name__ == "__main__":
    unittest.main()
