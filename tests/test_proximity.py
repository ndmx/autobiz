import unittest

from proximity import (
    coordinates_for_location,
    distance_to_philly,
    extract_county,
    proximity_bucket,
)


class ProximityTests(unittest.TestCase):
    def test_philadelphia_is_zero_distance_bucket(self):
        self.assertLessEqual(distance_to_philly("Philadelphia, PA"), 5)
        self.assertEqual(proximity_bucket(distance_to_philly("Philadelphia, PA")), "Philadelphia")

    def test_suburb_and_statewide_locations_rank_by_distance(self):
        kop = distance_to_philly("King of Prussia, PA")
        pittsburgh = distance_to_philly("Pittsburgh, PA")
        erie = distance_to_philly("Erie, PA")

        self.assertIsNotNone(kop)
        self.assertIsNotNone(pittsburgh)
        self.assertIsNotNone(erie)
        self.assertLess(kop, pittsburgh)
        self.assertLess(pittsburgh, erie)

    def test_county_and_craigslist_market_fallbacks(self):
        self.assertEqual(extract_county("Delaware County, PA"), "delaware")
        self.assertIsNotNone(coordinates_for_location("Pennsylvania", "craigslist/poconos"))
        self.assertEqual(proximity_bucket(distance_to_philly("Delaware County, PA")), "within 25 mi")

    def test_unknown_specific_city_stays_unknown(self):
        self.assertIsNone(distance_to_philly("Made Up Borough, PA"))
        self.assertEqual(proximity_bucket(None), "unknown distance")


if __name__ == "__main__":
    unittest.main()
