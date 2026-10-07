import hmac
import json
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

DEFAULT_BOOKS = [
    ("The Left Hand of Darkness", "Ursula K. Le Guin", 12.99, 8),
    ("The Hobbit", "J.R.R. Tolkien", 10.50, 12),
    ("Kindred", "Octavia E. Butler", 11.25, 6),
    ("A Wizard of Earthsea", "Ursula K. Le Guin", 9.75, 10),
]


def connect(database_path):
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(database_path):
    with connect(database_path) as connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                author TEXT NOT NULL,
                price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
                stock INTEGER NOT NULL CHECK (stock >= 0)
            );
            CREATE TABLE IF NOT EXISTS carts (
                id INTEGER PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'checked_out'))
            );
            CREATE TABLE IF NOT EXISTS cart_items (
                cart_id INTEGER NOT NULL REFERENCES carts(id),
                book_id INTEGER NOT NULL REFERENCES books(id),
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                PRIMARY KEY (cart_id, book_id)
            );
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY,
                cart_id INTEGER NOT NULL UNIQUE REFERENCES carts(id),
                total_cents INTEGER NOT NULL CHECK (total_cents >= 0)
            );
            CREATE TABLE IF NOT EXISTS order_items (
                order_id INTEGER NOT NULL REFERENCES orders(id),
                book_id INTEGER NOT NULL REFERENCES books(id),
                title TEXT NOT NULL,
                author TEXT NOT NULL,
                unit_price_cents INTEGER NOT NULL,
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                PRIMARY KEY (order_id, book_id)
            );
            """)
        count = connection.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        if count == 0:
            connection.executemany(
                "INSERT INTO books (title, author, price_cents, stock) VALUES (?, ?, ?, ?)",
                [
                    (title, author, round(price * 100), stock)
                    for title, author, price, stock in DEFAULT_BOOKS
                ],
            )


def book_json(row):
    return {
        "id": row["id"],
        "title": row["title"],
        "author": row["author"],
        "price": f"{row['price_cents'] / 100:.2f}",
        "stock": row["stock"],
    }


class BookstoreServer(ThreadingHTTPServer):
    def __init__(self, address, database_path, admin_token=None):
        self.database_path = database_path
        self.admin_token = (
            os.environ.get("BOOKSTORE_ADMIN_TOKEN", "")
            if admin_token is None
            else admin_token
        )
        super().__init__(address, BookstoreHandler)


class BookstoreHandler(BaseHTTPRequestHandler):
    def send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 1_000_000:
                raise ValueError
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError
            return payload
        except (ValueError, json.JSONDecodeError):
            raise ValueError("Request body must be a JSON object") from None

    def has_admin_permission(self):
        configured_token = self.server.admin_token
        if not configured_token:
            return False
        scheme, separator, token = self.headers.get("Authorization", "").partition(" ")
        return (
            separator == " "
            and scheme.lower() == "bearer"
            and hmac.compare_digest(token, configured_token)
        )

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        parts = path.split("/")
        with connect(self.server.database_path) as connection:
            if path == "/" or path == "/health":
                self.send_json(
                    200,
                    {
                        "status": "ok",
                        "service": "bookstore-api",
                        "permissions": {"admin": self.has_admin_permission()},
                    },
                )
            elif path == "/books":
                rows = connection.execute("SELECT * FROM books ORDER BY id").fetchall()
                self.send_json(200, {"books": [book_json(row) for row in rows]})
            elif len(parts) == 3 and parts[1] == "books" and parts[2].isdigit():
                row = connection.execute(
                    "SELECT * FROM books WHERE id = ?", (int(parts[2]),)
                ).fetchone()
                if row is None:
                    self.send_json(404, {"error": "Book not found"})
                else:
                    self.send_json(200, book_json(row))
            elif len(parts) == 3 and parts[1] == "carts" and parts[2].isdigit():
                self.send_cart(connection, int(parts[2]))
            elif len(parts) == 3 and parts[1] == "orders" and parts[2].isdigit():
                self.send_order(connection, int(parts[2]))
            else:
                self.send_json(404, {"error": "Route not found"})

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/")
        parts = path.split("/")
        try:
            payload = self.read_json()
            with connect(self.server.database_path) as connection:
                if path == "/carts":
                    cursor = connection.execute("INSERT INTO carts DEFAULT VALUES")
                    self.send_cart(connection, cursor.lastrowid, status=201)
                elif (
                    len(parts) == 4
                    and parts[1] == "carts"
                    and parts[2].isdigit()
                    and parts[3] == "items"
                ):
                    self.add_cart_item(connection, int(parts[2]), payload)
                elif (
                    len(parts) == 4
                    and parts[1] == "carts"
                    and parts[2].isdigit()
                    and parts[3] == "checkout"
                ):
                    self.checkout(connection, int(parts[2]))
                else:
                    self.send_json(404, {"error": "Route not found"})
        except ValueError as error:
            self.send_json(400, {"error": str(error)})

    def add_cart_item(self, connection, cart_id, payload):
        book_id = payload.get("book_id")
        quantity = payload.get("quantity", 1)
        if type(book_id) is not int or type(quantity) is not int or quantity < 1:
            raise ValueError("book_id and positive integer quantity are required")

        cart = connection.execute(
            "SELECT status FROM carts WHERE id = ?", (cart_id,)
        ).fetchone()
        if cart is None:
            self.send_json(404, {"error": "Cart not found"})
            return
        if cart["status"] != "active":
            self.send_json(409, {"error": "Cart has already been checked out"})
            return

        book = connection.execute(
            "SELECT * FROM books WHERE id = ?", (book_id,)
        ).fetchone()
        if book is None:
            self.send_json(404, {"error": "Book not found"})
            return
        existing = connection.execute(
            "SELECT quantity FROM cart_items WHERE cart_id = ? AND book_id = ?",
            (cart_id, book_id),
        ).fetchone()
        new_quantity = quantity + (existing["quantity"] if existing else 0)
        if new_quantity > book["stock"]:
            self.send_json(409, {"error": "Requested quantity exceeds available stock"})
            return
        connection.execute(
            """INSERT INTO cart_items (cart_id, book_id, quantity) VALUES (?, ?, ?)
               ON CONFLICT(cart_id, book_id) DO UPDATE SET quantity = excluded.quantity""",
            (cart_id, book_id, new_quantity),
        )
        self.send_cart(connection, cart_id)

    def checkout(self, connection, cart_id):
        connection.execute("BEGIN IMMEDIATE")
        cart = connection.execute(
            "SELECT status FROM carts WHERE id = ?", (cart_id,)
        ).fetchone()
        if cart is None:
            connection.rollback()
            self.send_json(404, {"error": "Cart not found"})
            return
        if cart["status"] != "active":
            connection.rollback()
            self.send_json(409, {"error": "Cart has already been checked out"})
            return

        items = connection.execute(
            """SELECT books.*, cart_items.quantity
               FROM cart_items JOIN books ON books.id = cart_items.book_id
               WHERE cart_items.cart_id = ?""",
            (cart_id,),
        ).fetchall()
        if not items:
            connection.rollback()
            self.send_json(400, {"error": "Cannot check out an empty cart"})
            return
        for item in items:
            if item["quantity"] > item["stock"]:
                connection.rollback()
                self.send_json(
                    409, {"error": f"Insufficient stock for {item['title']}"}
                )
                return

        total_cents = sum(item["price_cents"] * item["quantity"] for item in items)
        cursor = connection.execute(
            "INSERT INTO orders (cart_id, total_cents) VALUES (?, ?)",
            (cart_id, total_cents),
        )
        order_id = cursor.lastrowid
        for item in items:
            connection.execute(
                """INSERT INTO order_items
                   (order_id, book_id, title, author, unit_price_cents, quantity)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    order_id,
                    item["id"],
                    item["title"],
                    item["author"],
                    item["price_cents"],
                    item["quantity"],
                ),
            )
            connection.execute(
                "UPDATE books SET stock = stock - ? WHERE id = ?",
                (item["quantity"], item["id"]),
            )
        connection.execute(
            "UPDATE carts SET status = 'checked_out' WHERE id = ?", (cart_id,)
        )
        connection.commit()
        with connect(self.server.database_path) as read_connection:
            self.send_order(read_connection, order_id, status=201)

    def send_cart(self, connection, cart_id, status=200):
        cart = connection.execute(
            "SELECT * FROM carts WHERE id = ?", (cart_id,)
        ).fetchone()
        if cart is None:
            self.send_json(404, {"error": "Cart not found"})
            return
        rows = connection.execute(
            """SELECT books.*, cart_items.quantity
               FROM cart_items JOIN books ON books.id = cart_items.book_id
               WHERE cart_items.cart_id = ? ORDER BY books.id""",
            (cart_id,),
        ).fetchall()
        items = [
            {
                "book": book_json(row),
                "quantity": row["quantity"],
                "line_total": f"{row['price_cents'] * row['quantity'] / 100:.2f}",
            }
            for row in rows
        ]
        total_cents = sum(row["price_cents"] * row["quantity"] for row in rows)
        self.send_json(
            status,
            {
                "id": cart["id"],
                "status": cart["status"],
                "items": items,
                "total": f"{total_cents / 100:.2f}",
            },
        )

    def send_order(self, connection, order_id, status=200):
        order = connection.execute(
            "SELECT * FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
        if order is None:
            self.send_json(404, {"error": "Order not found"})
            return
        rows = connection.execute(
            "SELECT * FROM order_items WHERE order_id = ? ORDER BY book_id",
            (order_id,),
        ).fetchall()
        self.send_json(
            status,
            {
                "id": order["id"],
                "cart_id": order["cart_id"],
                "items": [
                    {
                        "book_id": row["book_id"],
                        "title": row["title"],
                        "author": row["author"],
                        "unit_price": f"{row['unit_price_cents'] / 100:.2f}",
                        "quantity": row["quantity"],
                    }
                    for row in rows
                ],
                "total": f"{order['total_cents'] / 100:.2f}",
            },
        )

    def log_message(self, format_string, *args):
        print(f"{self.address_string()} - {format_string % args}")


def main():
    database_path = os.environ.get("BOOKSTORE_DB", "bookstore.db")
    host = os.environ.get("BOOKSTORE_HOST", "127.0.0.1")
    port = int(os.environ.get("BOOKSTORE_PORT", "8765"))
    initialize_database(database_path)
    server = BookstoreServer((host, port), database_path)
    print(f"Bookstore API listening at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping bookstore API")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
