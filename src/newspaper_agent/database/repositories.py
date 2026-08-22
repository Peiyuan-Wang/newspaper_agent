from __future__ import annotations

import sqlite3


class SQLiteMemoryRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save(self, key: str, value: str) -> None:
        self.connection.execute(
            "INSERT INTO memories(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.connection.commit()

    def get(self, key: str) -> str | None:
        row = self.connection.execute("SELECT value FROM memories WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None
