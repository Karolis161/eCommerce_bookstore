import json
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app import BookstoreServer, initialize_database


class BookstoreApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = f"{self.temp_dir.name}/test.db"
        initialize_database(database_path)
        self.server = BookstoreServer(
            ("127.0.0.1", 0), database_path, admin_token="test-admin-token"
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp_dir.cleanup()

    def request(self, method, path, payload=None, headers=None):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            self.base_url + path,
            data=body,
            method=method,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            response = urlopen(request)
        except HTTPError as error:
            response = error
        return response.status, json.loads(response.read())

    def test_health_reports_admin_permission(self):
        status, health = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertFalse(health["permissions"]["admin"])

        status, health = self.request(
            "GET", "/health", headers={"Authorization": "Bearer wrong-token"}
        )
        self.assertEqual(status, 200)
        self.assertFalse(health["permissions"]["admin"])

        status, health = self.request(
            "GET",
            "/health",
            headers={"Authorization": "Bearer test-admin-token"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(health["permissions"]["admin"])

    def test_catalog_and_checkout_flow(self):
        status, catalog = self.request("GET", "/books")
        self.assertEqual(status, 200)
        self.assertEqual(len(catalog["books"]), 4)

        status, cart = self.request("POST", "/carts", {})
        self.assertEqual(status, 201)
        cart_id = cart["id"]
        status, cart = self.request(
            "POST", f"/carts/{cart_id}/items", {"book_id": 1, "quantity": 2}
        )
        self.assertEqual(status, 200)
        self.assertEqual(cart["total"], "25.98")

        status, order = self.request("POST", f"/carts/{cart_id}/checkout", {})
        self.assertEqual(status, 201)
        self.assertEqual(order["total"], "25.98")
        self.assertEqual(order["items"][0]["quantity"], 2)
        self.assertEqual(self.request("GET", "/books/1")[1]["stock"], 6)

    def test_checkout_rejects_empty_cart(self):
        _, cart = self.request("POST", "/carts", {})
        status, error = self.request("POST", f"/carts/{cart['id']}/checkout", {})
        self.assertEqual(status, 400)
        self.assertIn("empty cart", error["error"])

    def test_invalid_quantity_is_rejected(self):
        _, cart = self.request("POST", "/carts", {})
        status, error = self.request(
            "POST", f"/carts/{cart['id']}/items", {"book_id": 1, "quantity": 0}
        )
        self.assertEqual(status, 400)
        self.assertIn("positive integer", error["error"])


if __name__ == "__main__":
    unittest.main()
