import os
import unittest

import app


class AppRunHelperTests(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("AUTOBIZ_NO_BROWSER", None)

    def test_dashboard_url_points_to_dashboard(self):
        self.assertEqual(app.dashboard_url(7860), "http://localhost:7860/dashboard")

    def test_auto_open_browser_enabled_by_default(self):
        os.environ.pop("AUTOBIZ_NO_BROWSER", None)
        self.assertTrue(app.should_auto_open_browser())

    def test_auto_open_browser_can_be_disabled(self):
        for value in ("1", "true", "YES", "on"):
            os.environ["AUTOBIZ_NO_BROWSER"] = value
            self.assertFalse(app.should_auto_open_browser())


if __name__ == "__main__":
    unittest.main()
