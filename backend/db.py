import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class Database:
    def __init__(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    telegram_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT NOT NULL,
                    nickname TEXT,
                    score REAL,
                    avatar_file_id TEXT,
                    is_bot INTEGER NOT NULL DEFAULT 0,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_users_score ON users(score DESC)"
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(users)").fetchall()
            }
            if "is_bot" not in columns:
                connection.execute(
                    "ALTER TABLE users ADD COLUMN is_bot INTEGER NOT NULL DEFAULT 0"
                )

    def upsert_user(
        self,
        telegram_id: int,
        username: str | None,
        first_name: str,
        avatar_file_id: str | None = None,
        is_bot: bool = False,
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO users (telegram_id, username, first_name, avatar_file_id, is_bot)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name,
                    avatar_file_id = COALESCE(excluded.avatar_file_id, users.avatar_file_id),
                    is_bot = excluded.is_bot,
                    is_active = 1,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (telegram_id, username, first_name, avatar_file_id, int(is_bot)),
            )

    def update_avatar(self, telegram_id: int, avatar_file_id: str) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                UPDATE users
                SET avatar_file_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE telegram_id = ?
                """,
                (avatar_file_id, telegram_id),
            )

    def rate_user(self, telegram_id: int, score: float, nickname: str | None) -> bool:
        with self.connection() as connection:
            result = connection.execute(
                """
                UPDATE users
                SET score = ?, nickname = ?, updated_at = CURRENT_TIMESTAMP
                WHERE telegram_id = ? AND is_active = 1
                """,
                (score, nickname or None, telegram_id),
            )
            return result.rowcount == 1

    def find_user_by_username(self, username: str) -> sqlite3.Row | None:
        with self.connection() as connection:
            return connection.execute(
                """
                SELECT * FROM users
                WHERE lower(username) = lower(?) AND is_active = 1
                """,
                (username.lstrip("@"),),
            ).fetchone()

    def get_user(self, telegram_id: int) -> sqlite3.Row | None:
        with self.connection() as connection:
            return connection.execute(
                "SELECT * FROM users WHERE telegram_id = ? AND is_active = 1",
                (telegram_id,),
            ).fetchone()

    def list_users(self) -> list[sqlite3.Row]:
        with self.connection() as connection:
            return connection.execute(
                """
                SELECT * FROM users
                WHERE is_active = 1
                ORDER BY score IS NULL ASC, score DESC, first_name COLLATE NOCASE ASC
                """
            ).fetchall()

    def list_users_with_file_id(self) -> list[sqlite3.Row]:
        with self.connection() as connection:
            return connection.execute(
                """
                SELECT * FROM users
                WHERE is_active = 1 AND avatar_file_id IS NOT NULL
                """
            ).fetchall()

    def mark_inactive(self, telegram_id: int) -> None:
        with self.connection() as connection:
            connection.execute(
                "UPDATE users SET is_active = 0, updated_at = CURRENT_TIMESTAMP WHERE telegram_id = ?",
                (telegram_id,),
            )

    @staticmethod
    def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)
