import unittest

from scraper import filter_by_budget, normalize_listings
from source_adapters.craigslist import is_real_business
from source_adapters.grok_pages import build_grok_source_urls


class ScraperNormalizationTests(unittest.TestCase):
    def test_normalize_listings_removes_placeholders_and_dedupes(self):
        listings = [
            {
                "business_name": "Business Name",
                "description": "placeholder",
                "source_url": "string - the full listing URL",
                "asking_price": 100000,
            },
            {
                "business_name": "Established Cleaning Route",
                "business_type": "cleaning",
                "asking_price": 125000,
                "location": "Philadelphia, PA",
                "cash_flow_annual": 70000,
                "gross_revenue_annual": 180000,
                "description": "Owner retiring. Established recurring clients.",
                "source_url": "https://example.com/cleaning",
                "_source": "test",
            },
            {
                "business_name": "Established Cleaning Route",
                "business_type": "cleaning",
                "asking_price": 125000,
                "location": "Philadelphia, PA",
                "description": "Duplicate",
                "source_url": "https://example.com/duplicate",
                "_source": "test",
            },
        ]

        normalized = normalize_listings(listings)

        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0]["business_name"], "Established Cleaning Route")
        self.assertEqual(normalized[0]["financial_confidence"]["level"], "high")
        self.assertEqual(normalized[0]["proximity_rank"], 1)

    def test_filter_by_budget_keeps_unknown_prices_and_filters_known_prices(self):
        listings = [
            {"business_name": "Unknown Ask", "asking_price": None, "location": "Philadelphia, PA"},
            {"business_name": "Too Cheap", "asking_price": 1000, "location": "Philadelphia, PA"},
            {"business_name": "In Range", "asking_price": 100000, "location": "Philadelphia, PA"},
        ]

        filtered = filter_by_budget(listings, 50000, 150000)

        self.assertEqual([item["business_name"] for item in filtered], ["Unknown Ask", "In Range"])

    def test_craigslist_business_filter_requires_business_signals(self):
        self.assertTrue(is_real_business(
            "Established vending route for sale",
            "Profitable route with recurring customers and owner retiring.",
            price=50000,
        ))
        self.assertFalse(is_real_business("Used cargo van", "Clean truck ready to drive.", price=12000))

    def test_grok_source_urls_prioritize_philly_then_statewide(self):
        labels = [label for label, _ in build_grok_source_urls("Pennsylvania", 250000, min_price=75000)]

        self.assertEqual(labels[0], "BizBuySell-Philadelphia")
        self.assertIn("BusinessBroker-PA", labels)
        self.assertIn("BizQuest-PA", labels)
        self.assertIn("DealStream-PA", labels)


if __name__ == "__main__":
    unittest.main()
