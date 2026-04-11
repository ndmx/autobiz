import unittest

from dedupe import dedupe_listings, is_probable_duplicate, token_similarity


class DedupeTests(unittest.TestCase):
    def test_token_similarity_detects_related_names(self):
        self.assertGreaterEqual(
            token_similarity({"pizza", "delaware"}, {"pizza", "delaware", "county"}),
            0.65,
        )

    def test_probable_duplicate_uses_name_location_and_price_band(self):
        first = {
            "business_name": "Established Pizza Shop Delaware County",
            "location": "Delaware County, PA",
            "asking_price": 225000,
        }
        second = {
            "business_name": "Pizza Shop in Delaware County",
            "location": "Delaware County, PA",
            "asking_price": 229000,
        }

        self.assertTrue(is_probable_duplicate(first, second))

    def test_dedupe_merges_sources_and_keeps_better_financials(self):
        listings = [
            {
                "business_name": "Established Pizza Shop Delaware County",
                "location": "Delaware County, PA",
                "asking_price": 225000,
                "source_url": "https://example.com/a",
                "_source": "BizBuySell",
            },
            {
                "business_name": "Pizza Shop in Delaware County",
                "location": "Delaware County, PA",
                "asking_price": 229000,
                "cash_flow_annual": 120000,
                "gross_revenue_annual": 650000,
                "description": "Longer operating summary with financials.",
                "_source": "BusinessBroker",
            },
        ]

        [merged] = dedupe_listings(listings)

        self.assertEqual(merged["_duplicate_count"], 2)
        self.assertEqual(merged["cash_flow_annual"], 120000)
        self.assertEqual(set(merged["_duplicate_sources"]), {"BizBuySell", "BusinessBroker"})


if __name__ == "__main__":
    unittest.main()
