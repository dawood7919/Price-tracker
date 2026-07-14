import sqlite3
from contextlib import contextmanager
from datetime import date

from config import DATABASE_PATH
from database.models import SCHEMA_SQL


@contextmanager
def get_connection():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(SCHEMA_SQL)


def upsert_user(user_id: int, username: str | None) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO users (user_id, username) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET username = excluded.username, last_active = CURRENT_TIMESTAMP
            """,
            (user_id, username),
        )


def start_download(user_id: int, url: str, domain: str, title: str, resolution: str) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO download_history (user_id, url, domain, title, resolution, status)
            VALUES (?, ?, ?, ?, ?, 'in_progress')
            """,
            (user_id, url, domain, title, resolution),
        )
        return cursor.lastrowid


def finish_download(history_id: int, status: str, file_size_bytes: int | None) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE download_history
            SET status = ?, file_size_bytes = ?, completed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, file_size_bytes, history_id),
        )

    today = date.today().isoformat()
    is_failure = 0 if status == "completed" else 1
    size = file_size_bytes or 0
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO statistics (date, total_downloads, failed_downloads, total_bytes)
            VALUES (?, 1, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                total_downloads = total_downloads + 1,
                failed_downloads = failed_downloads + excluded.failed_downloads,
                total_bytes = total_bytes + excluded.total_bytes
            """,
            (today, is_failure, size),
        )


def get_user_history(user_id: int, limit: int = 10) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM download_history WHERE user_id = ? ORDER BY requested_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()


def get_setting(key: str, default: str | None = None) -> str | None:
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
