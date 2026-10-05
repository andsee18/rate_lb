from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row


class Database:
    def __init__(self, url: str) -> None:
        self.url = url
        self.initialize()

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        with psycopg.connect(self.url, row_factory=dict_row) as connection:
            yield connection

    def initialize(self) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    telegram_id BIGINT PRIMARY KEY,
                    username TEXT,
                    first_name TEXT NOT NULL,
                    nickname TEXT,
                    gender TEXT NOT NULL DEFAULT 'male',
                    score DOUBLE PRECISION,
                    avatar_file_id TEXT,
                    symmetry_score DOUBLE PRECISION,
                    jaw_score DOUBLE PRECISION,
                    skin_score DOUBLE PRECISION,
                    harmony_score DOUBLE PRECISION,
                    eyes_score DOUBLE PRECISION,
                    hair_score DOUBLE PRECISION,
                    pros TEXT,
                    cons TEXT,
                    verdict TEXT,
                    is_bot BOOLEAN NOT NULL DEFAULT FALSE,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_users_score ON users(score DESC)"
            )
            connection.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS gender TEXT NOT NULL DEFAULT 'male'"
            )
            connection.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS pros TEXT"
            )
            connection.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS cons TEXT"
            )
            connection.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS verdict TEXT"
            )
            for column in (
                "symmetry_score", "jaw_score", "skin_score",
                "harmony_score", "eyes_score", "hair_score",
            ):
                connection.execute(
                    f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {column} DOUBLE PRECISION"
                )
            connection.commit()

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
                INSERT INTO users
                    (telegram_id, username, first_name, avatar_file_id, is_bot)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (telegram_id) DO UPDATE SET
                    username = EXCLUDED.username,
                    first_name = EXCLUDED.first_name,
                    avatar_file_id = COALESCE(EXCLUDED.avatar_file_id, users.avatar_file_id),
                    is_bot = EXCLUDED.is_bot,
                    is_active = TRUE,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (telegram_id, username, first_name, avatar_file_id, is_bot),
            )

    def update_avatar(self, telegram_id: int, avatar_file_id: str) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                UPDATE users
                SET avatar_file_id = %s, updated_at = CURRENT_TIMESTAMP
                WHERE telegram_id = %s
                """,
                (avatar_file_id, telegram_id),
            )

    def rate_user(self, telegram_id: int, score: float, nickname: str | None) -> bool:
        with self.connection() as connection:
            result = connection.execute(
                """
                UPDATE users
                SET score = %s, nickname = %s, updated_at = CURRENT_TIMESTAMP
                WHERE telegram_id = %s AND is_active = TRUE
                """,
                (score, nickname or None, telegram_id),
            )
            return result.rowcount == 1

    def unrate_user(self, telegram_id: int) -> bool:
        with self.connection() as connection:
            result = connection.execute(
                """
                UPDATE users
                SET score = NULL, nickname = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE telegram_id = %s AND is_active = TRUE
                """,
                (telegram_id,),
            )
            return result.rowcount == 1

    def set_nickname(self, telegram_id: int, nickname: str) -> bool:
        with self.connection() as connection:
            result = connection.execute(
                """
                UPDATE users
                SET nickname = %s, updated_at = CURRENT_TIMESTAMP
                WHERE telegram_id = %s AND is_active = TRUE
                """,
                (nickname, telegram_id),
            )
            return result.rowcount == 1

    def set_gender(self, telegram_id: int, gender: str) -> bool:
        with self.connection() as connection:
            result = connection.execute(
                """
                UPDATE users
                SET gender = %s, updated_at = CURRENT_TIMESTAMP
                WHERE telegram_id = %s AND is_active = TRUE
                """,
                (gender, telegram_id),
            )
            return result.rowcount == 1

    def set_details(self, telegram_id: int, field: str, value: str | None) -> bool:
        if field not in {"pros", "cons", "verdict"}:
            raise ValueError(f"Unsupported details field: {field}")
        with self.connection() as connection:
            result = connection.execute(
                f"""
                UPDATE users
                SET {field} = %s, updated_at = CURRENT_TIMESTAMP
                WHERE telegram_id = %s AND is_active = TRUE
                """,
                (value, telegram_id),
            )
            return result.rowcount == 1

    def set_parameters(self, telegram_id: int, parameters: dict[str, float]) -> bool:
        columns = (
            "symmetry_score", "jaw_score", "skin_score",
            "harmony_score", "eyes_score", "hair_score",
        )
        values = [parameters[column] for column in columns]
        with self.connection() as connection:
            result = connection.execute(
                """
                UPDATE users
                SET symmetry_score = %s, jaw_score = %s, skin_score = %s,
                    harmony_score = %s, eyes_score = %s, hair_score = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE telegram_id = %s AND is_active = TRUE
                """,
                (*values, telegram_id),
            )
            return result.rowcount == 1

    def find_user_by_username(self, username: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            return connection.execute(
                """
                SELECT * FROM users
                WHERE lower(username) = lower(%s) AND is_active = TRUE
                """,
                (username.lstrip("@"),),
            ).fetchone()

    def get_user(self, telegram_id: int) -> dict[str, Any] | None:
        with self.connection() as connection:
            return connection.execute(
                "SELECT * FROM users WHERE telegram_id = %s AND is_active = TRUE",
                (telegram_id,),
            ).fetchone()

    def list_users(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            return connection.execute(
                """
                SELECT * FROM users
                WHERE is_active = TRUE
                ORDER BY (score IS NULL) ASC, score DESC, lower(first_name) ASC
                """
            ).fetchall()

    def list_users_with_file_id(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            return connection.execute(
                """
                SELECT * FROM users
                WHERE is_active = TRUE AND avatar_file_id IS NOT NULL
                """
            ).fetchall()

    def mark_inactive(self, telegram_id: int) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                UPDATE users
                SET is_active = FALSE, updated_at = CURRENT_TIMESTAMP
                WHERE telegram_id = %s
                """,
                (telegram_id,),
            )

    @staticmethod
    def row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
        return row
