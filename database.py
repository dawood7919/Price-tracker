import sqlite3
from contextlib import contextmanager

from config import DB_PATH


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                url TEXT NOT NULL,
                product_name TEXT,
                current_price REAL,
                previous_price REAL,
                store_name TEXT,
                last_checked TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            );

            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                price REAL NOT NULL,
                checked_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (product_id) REFERENCES products (id)
            );
            """
        )


def add_user(user_id: int, username: str | None):
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
            (user_id, username),
        )


def add_product(user_id: int, url: str, product_name: str, price: float, store_name: str) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO products (user_id, url, product_name, current_price, previous_price, store_name, last_checked)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (user_id, url, product_name, price, price, store_name),
        )
        product_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO price_history (product_id, price) VALUES (?, ?)",
            (product_id, price),
        )
        return product_id


def get_products_by_user(user_id: int) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM products WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()


def get_product(product_id: int, user_id: int | None = None) -> sqlite3.Row | None:
    query = "SELECT * FROM products WHERE id = ?"
    params: list = [product_id]
    if user_id is not None:
        query += " AND user_id = ?"
        params.append(user_id)
    with get_connection() as conn:
        return conn.execute(query, params).fetchone()


def remove_product(product_id: int, user_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM products WHERE id = ? AND user_id = ?",
            (product_id, user_id),
        )
        return cursor.rowcount > 0


def get_all_products() -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM products").fetchall()


def update_product_price(product_id: int, new_price: float) -> float | None:
    """Updates the stored price and logs history. Returns the price that was current before this update."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT current_price FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        old_price = row["current_price"] if row else None

        conn.execute(
            """
            UPDATE products
            SET previous_price = current_price, current_price = ?, last_checked = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (new_price, product_id),
        )
        conn.execute(
            "INSERT INTO price_history (product_id, price) VALUES (?, ?)",
            (product_id, new_price),
        )
        return old_price
