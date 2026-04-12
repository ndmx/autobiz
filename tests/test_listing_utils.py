import unittest
from unittest.mock import patch

from agent import apply_hard_rules, verify_listings
from listing_utils import attach_listing_metadata, financial_confidence, financial_field_provenance


class ListingUtilsTests(unittest.TestCase):
    def test_financial_confidence_high_when_core_financials_exist(self):
        confidence = financial_confidence({
            "asking_price": 150000,
            "cash_flow_annual": 80000,
            "gross_revenue_annual": 300000,
            "seller_motivation": "Owner retiring",
            "source_url": "https://example.com/listing",
        })

        self.assertEqual(confidence["level"], "high")
        self.assertGreaterEqual(confidence["score"], 80)
        self.assertEqual(confidence["provenance"]["cash_flow"], "scraped")

    def test_financial_confidence_very_low_without_hard_financials(self):
        confidence = financial_confidence({
            "business_name": "Nice sounding shop",
            "source_url": "estimated",
            "is_estimated": True,
        })

        self.assertEqual(confidence["level"], "very_low")
        self.assertLess(confidence["score"], 30)
        self.assertEqual(confidence["provenance"]["source"], "unverified")

    def test_financial_field_provenance_marks_estimates(self):
        provenance = financial_field_provenance({
            "asking_price_usd": 125000,
            "extracted_financials": {"cash_flow_annual": 50000},
            "is_estimated": True,
            "source_url": "estimated",
        })

        self.assertEqual(provenance["asking_price"], "estimated")
        self.assertEqual(provenance["cash_flow"], "estimated")

    def test_attach_listing_metadata_adds_confidence_and_proximity(self):
        result = {"business_name": "Service Route", "weighted_score": 70}
        listing = {
            "business_type": "route",
            "asking_price": 120000,
            "cash_flow_annual": 65000,
            "location": "Bensalem, PA",
            "source_url": "https://example.com/route",
        }

        merged = attach_listing_metadata(result, listing)

        self.assertEqual(merged["asking_price"], 120000)
        self.assertEqual(merged["financial_confidence"]["level"], "medium")
        self.assertIsNotNone(merged["distance_to_philly_miles"])

    def test_hard_rules_cap_very_low_financial_confidence(self):
        result = {
            "business_name": "Estimated Opportunity",
            "business_type": "service",
            "weighted_score": 95,
            "tier": "A",
            "is_estimated": True,
            "source_url": "estimated",
            "extracted_financials": {},
            "scores": {
                "dscr_score": {"score": 5},
                "seller_finance_likelihood": {"score": 5},
                "seller_motivation": {"score": 5},
                "owner_independence": {"score": 5},
                "business_age": {"score": 5},
                "operational_simplicity": {"score": 5},
                "red_flags": {"penalty": 0, "flags": []},
            },
        }

        [capped] = apply_hard_rules([result])

        self.assertLessEqual(capped["weighted_score"], 49)
        self.assertEqual(capped["tier"], "C")
        self.assertEqual(capped["financial_confidence"]["level"], "very_low")

    def test_hard_rules_cap_missing_cash_flow_even_with_other_financials(self):
        result = {
            "business_name": "Revenue Only Listing",
            "business_type": "service",
            "asking_price_usd": 150000,
            "weighted_score": 92,
            "tier": "A",
            "source_url": "https://example.com/revenue-only",
            "seller_motivation": "Owner retiring",
            "extracted_financials": {"gross_revenue_annual": 400000},
            "scores": {
                "dscr_score": {"score": 5},
                "seller_finance_likelihood": {"score": 5},
                "seller_motivation": {"score": 5},
                "owner_independence": {"score": 5},
                "business_age": {"score": 5},
                "operational_simplicity": {"score": 5},
                "red_flags": {"penalty": 0, "flags": []},
            },
        }

        [capped] = apply_hard_rules([result])

        self.assertEqual(capped["weighted_score"], 69)
        self.assertEqual(capped["tier"], "B")

    def test_margin_penalty_cannot_raise_listing_above_cash_flow_cap(self):
        result = {
            "business_name": "Suspicious Margin Listing",
            "business_type": "cleaning service",
            "asking_price_usd": 150000,
            "weighted_score": 95,
            "tier": "A",
            "source_url": "https://example.com/high-margin",
            "seller_motivation": "Owner retiring",
            "extracted_financials": {
                "gross_revenue_annual": 400000,
                "profit_margin_pct": 50,
            },
            "scores": {
                "dscr_score": {"score": 5},
                "seller_finance_likelihood": {"score": 5},
                "seller_motivation": {"score": 5},
                "owner_independence": {"score": 5},
                "business_age": {"score": 5},
                "operational_simplicity": {"score": 5},
                "red_flags": {"penalty": 0, "flags": []},
            },
        }

        [capped] = apply_hard_rules([result])

        self.assertEqual(capped["weighted_score"], 69)
        self.assertEqual(capped["tier"], "B")
        self.assertEqual(capped["scores"]["red_flags"]["penalty"], 2)
        self.assertIn("missing verified cash flow (score capped at 69)", capped["_rule_adjustments"])
        self.assertTrue(
            any(adjustment.startswith("margin 50% exceeds 2x norm") for adjustment in capped["_rule_adjustments"])
        )

    def test_hard_rules_are_idempotent_when_reapplied_after_verification(self):
        result = {
            "business_name": "Repeated Margin Listing",
            "business_type": "cleaning service",
            "asking_price_usd": 150000,
            "weighted_score": 95,
            "tier": "A",
            "source_url": "https://example.com/repeated-margin",
            "seller_motivation": "Owner retiring",
            "extracted_financials": {
                "gross_revenue_annual": 400000,
                "profit_margin_pct": 50,
            },
            "scores": {
                "dscr_score": {"score": 5},
                "seller_finance_likelihood": {"score": 5},
                "seller_motivation": {"score": 5},
                "owner_independence": {"score": 5},
                "business_age": {"score": 5},
                "operational_simplicity": {"score": 5},
                "red_flags": {"penalty": 0, "flags": []},
            },
        }

        [first_pass] = apply_hard_rules([result])
        [second_pass] = apply_hard_rules([first_pass])

        self.assertEqual(second_pass["weighted_score"], 69)
        self.assertEqual(second_pass["scores"]["red_flags"]["penalty"], 2)
        self.assertEqual(
            len(second_pass["_rule_adjustments"]),
            len(set(second_pass["_rule_adjustments"])),
        )

    def test_unverified_listing_is_recapped_in_same_run(self):
        result = {
            "business_name": "Questionable URL Listing",
            "business_type": "service",
            "asking_price_usd": 150000,
            "weighted_score": 90,
            "tier": "A",
            "source_url": "https://example.com/questionable",
            "seller_motivation": "Owner retiring",
            "extracted_financials": {
                "cash_flow_annual": 85000,
                "gross_revenue_annual": 300000,
            },
            "scores": {
                "dscr_score": {"score": 5},
                "seller_finance_likelihood": {"score": 5},
                "seller_motivation": {"score": 5},
                "owner_independence": {"score": 5},
                "business_age": {"score": 5},
                "operational_simplicity": {"score": 5},
                "red_flags": {"penalty": 0, "flags": []},
            },
        }

        with patch("research.grok_call", return_value="UNVERIFIED - could not confirm"):
            [verified] = verify_listings(None, [result], top_n=1)
        [recapped] = apply_hard_rules([verified])

        self.assertTrue(recapped["is_estimated"])
        self.assertEqual(recapped["_verified"], "UNVERIFIED")
        self.assertEqual(recapped["weighted_score"], 69)
        self.assertEqual(recapped["tier"], "B")
        self.assertEqual(recapped["financial_confidence"]["level"], "low")


if __name__ == "__main__":
    unittest.main()
