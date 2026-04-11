import unittest

from source_adapters import bizbuysell, bizquest, businessbroker, dealstream
from source_adapters.grok_pages import parser_for_label


class SourceParserTests(unittest.TestCase):
    def test_bizbuysell_parser_extracts_listing_card(self):
        html = """
        <article class="listing-card">
          <a href="/Business-Opportunity/cleaning-route/123"><h3>Established Cleaning Route</h3></a>
          <span class="location">Philadelphia, PA</span>
          <span class="price">Asking Price $125,000</span>
          <p>Cash Flow $72,000 Gross Revenue $210,000 owner retiring</p>
        </article>
        """

        [listing] = bizbuysell.parse_listings(html, "https://www.bizbuysell.com/test", label="BizBuySell-Test")

        self.assertEqual(listing["business_name"], "Established Cleaning Route")
        self.assertEqual(listing["asking_price"], 125000)
        self.assertEqual(listing["cash_flow_annual"], 72000)
        self.assertEqual(listing["gross_revenue_annual"], 210000)
        self.assertEqual(listing["seller_motivation"], "owner retiring")

    def test_businessbroker_parser_extracts_listing_card(self):
        html = """
        <div class="business-listing">
          <h2>Pizza Shop in Delaware County</h2>
          <div class="listing-location">Delaware County, PA</div>
          <div class="financials">Price: $229,000 Cash Flow: $120,000 Revenue: $650,000</div>
          <a href="/business-for-sale/pizza-shop.aspx">details</a>
        </div>
        """

        [listing] = businessbroker.parse_listings(html, "https://www.businessbroker.net/list")

        self.assertEqual(listing["location"], "Delaware County, PA")
        self.assertTrue(listing["source_url"].endswith("/business-for-sale/pizza-shop.aspx"))

    def test_bizquest_parser_extracts_listing_card(self):
        html = """
        <li class="search-result">
          <a href="/business-for-sale/mobile-vending-pa/BW123/">Mobile Vending Route</a>
          <span class="city-state">Bucks County, PA</span>
          <span class="asking-price">$95,000</span>
          <p>Cash Flow: $48,000 Revenue: $140,000</p>
        </li>
        """

        [listing] = bizquest.parse_listings(html, "https://www.bizquest.com/businesses-for-sale-in-pennsylvania-pa/")

        self.assertEqual(listing["business_name"], "Mobile Vending Route")
        self.assertEqual(listing["asking_price"], 95000)

    def test_dealstream_parser_extracts_listing_card(self):
        html = """
        <article class="deal-card">
          <h3>Specialty Service Business</h3>
          <div class="location">Lancaster, PA</div>
          <div class="deal-price">Asking $185,000</div>
          <div class="teaser">SDE $90,000 Sales $320,000</div>
          <a href="/business-for-sale/specialty-service">view</a>
        </article>
        """

        [listing] = dealstream.parse_listings(html, "https://dealstream.com/pennsylvania-businesses-for-sale/5")

        self.assertEqual(listing["cash_flow_annual"], 90000)
        self.assertEqual(listing["gross_revenue_annual"], 320000)

    def test_parser_router_matches_marketplace_labels(self):
        self.assertIs(parser_for_label("BizBuySell-PA"), bizbuysell.parse_listings)
        self.assertIs(parser_for_label("BusinessBroker-PA"), businessbroker.parse_listings)
        self.assertIs(parser_for_label("BizQuest-PA"), bizquest.parse_listings)
        self.assertIs(parser_for_label("DealStream-PA"), dealstream.parse_listings)


if __name__ == "__main__":
    unittest.main()
