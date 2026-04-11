import unittest

from reporting import render_html_report


class ReportingTests(unittest.TestCase):
    def test_html_report_contains_dashboard_table(self):
        html = render_html_report([
            {
                "business_name": "Cleaning Route",
                "business_type": "cleaning",
                "location": "Philadelphia, PA",
                "asking_price_usd": 125000,
                "extracted_financials": {"cash_flow_annual": 70000, "gross_revenue_annual": 200000},
                "weighted_score": 72,
                "tier": "B",
                "source_url": "https://example.com/listing",
                "_source": "BizBuySell",
            }
        ], budget=250000, min_budget=75000, title="test run")

        self.assertIn("<table>", html)
        self.assertIn("Cleaning Route", html)
        self.assertIn("Ranked by distance from Philadelphia", html)
        self.assertIn("cash_flow: llm_extracted", html)


if __name__ == "__main__":
    unittest.main()
