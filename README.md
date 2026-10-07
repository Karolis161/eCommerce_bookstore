# Bookstore API

A small JSON bookstore backend built with Python's standard library and SQLite. It needs Python 3.10 or newer and has no third-party dependencies.

## Run

```bash
python3 app.py
```

The server listens on `http://127.0.0.1:8765` and creates `bookstore.db` in the current directory. Set `BOOKSTORE_PORT` to change the port or `BOOKSTORE_DB` to change the database path.

## API

All request bodies are JSON. Prices are returned as decimal strings.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Check server status |
| `GET` | `/books` | List books and stock |
| `GET` | `/books/{id}` | Get one book |
| `POST` | `/carts` | Create an empty cart |
| `GET` | `/carts/{id}` | View a cart |
| `POST` | `/carts/{id}/items` | Add `{"book_id": 1, "quantity": 2}` |
| `POST` | `/carts/{id}/checkout` | Place an order and decrement stock |
| `GET` | `/orders/{id}` | View a completed order |

Example checkout:

```bash
curl -X POST http://127.0.0.1:8765/carts -H 'Content-Type: application/json' -d '{}'
curl -X POST http://127.0.0.1:8765/carts/1/items -H 'Content-Type: application/json' -d '{"book_id":1,"quantity":2}'
curl -X POST http://127.0.0.1:8765/carts/1/checkout -H 'Content-Type: application/json' -d '{}'
```

## Test

```bash
python3 -m unittest -v
```
