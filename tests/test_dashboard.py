import unittest

from app import app


class DashboardTests(unittest.TestCase):
    def test_dashboard_route_renders_visual_board(self):
        client = app.test_client()
        response = client.get("/dashboard")

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Philadelphia-first acquisition board", body)
        self.assertIn("Ranked by distance from Philadelphia, then score.", body)
        self.assertIn("<table>", body)


if __name__ == "__main__":
    unittest.main()
