from __future__ import annotations

import datetime
from dataclasses import dataclass

import aiosqlite


class CapExceededError(Exception):
    pass


@dataclass
class WatchRow:
    id: int
    user_id: str
    channel_id: str
    filter_text: str
    filter_json: str
    created_at: str
    expires_at: str
    reminder_sent_at: str | None
    active: bool
    notification_count: int = 0


_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS watches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    filter_text TEXT NOT NULL,
    filter_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    reminder_sent_at TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    notification_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_watches_active ON watches (active) WHERE active = 1;
CREATE INDEX IF NOT EXISTS idx_watches_channel ON watches (channel_id, active);
CREATE INDEX IF NOT EXISTS idx_watches_user ON watches (user_id, active);
CREATE INDEX IF NOT EXISTS idx_watches_expires ON watches (expires_at) WHERE active = 1;
"""


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def _row_to_watch(row: aiosqlite.Row) -> WatchRow:
    return WatchRow(
        id=row[0],
        user_id=row[1],
        channel_id=row[2],
        filter_text=row[3],
        filter_json=row[4],
        created_at=row[5],
        expires_at=row[6],
        reminder_sent_at=row[7],
        active=bool(row[8]),
        notification_count=row[9],
    )


class WatchStore:
    def __init__(self, db_path: str, user_cap: int = 20, channel_cap: int = 50) -> None:
        self._db_path = db_path
        self._user_cap = user_cap
        self._channel_cap = channel_cap
        self._db: aiosqlite.Connection | None = None

    @property
    def _conn(self) -> aiosqlite.Connection:
        assert self._db is not None, "call initialize() first"
        return self._db

    async def initialize(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.executescript(_SCHEMA_SQL)
        # Insert schema version if table is empty.
        cursor = await self._conn.execute("SELECT COUNT(*) FROM schema_version")
        (count,) = await cursor.fetchone()  # type: ignore[misc]
        if count == 0:
            await self._conn.execute("INSERT INTO schema_version (version) VALUES (?)", (2,))
            await self._conn.commit()
            return

        # Migrations
        cursor = await self._conn.execute("SELECT version FROM schema_version")
        (version,) = await cursor.fetchone()  # type: ignore[misc]
        if version == 1:
            await self._conn.execute(
                "ALTER TABLE watches ADD COLUMN notification_count INTEGER NOT NULL DEFAULT 0"
            )
            await self._conn.execute(
                "UPDATE schema_version SET version = 2"
            )
        await self._conn.commit()

    async def create(
        self,
        user_id: str,
        channel_id: str,
        filter_text: str,
        filter_json: str,
        expires_at: str,
    ) -> WatchRow:
        # Enforce per-user cap.
        cursor = await self._conn.execute(
            "SELECT COUNT(*) FROM watches WHERE user_id = ? AND active = 1",
            (user_id,),
        )
        (user_count,) = await cursor.fetchone()  # type: ignore[misc]
        if user_count >= self._user_cap:
            raise CapExceededError(
                f"User {user_id} has reached the per-user watch cap of {self._user_cap}"
            )

        # Enforce per-channel cap.
        cursor = await self._conn.execute(
            "SELECT COUNT(*) FROM watches WHERE channel_id = ? AND active = 1",
            (channel_id,),
        )
        (channel_count,) = await cursor.fetchone()  # type: ignore[misc]
        if channel_count >= self._channel_cap:
            raise CapExceededError(
                f"Channel {channel_id} has reached the per-channel watch cap "
                f"of {self._channel_cap}"
            )

        now = _now_iso()
        cursor = await self._conn.execute(
            """INSERT INTO watches (user_id, channel_id, filter_text, filter_json,
                                    created_at, expires_at, active)
               VALUES (?, ?, ?, ?, ?, ?, 1)""",
            (user_id, channel_id, filter_text, filter_json, now, expires_at),
        )
        await self._conn.commit()
        watch_id = cursor.lastrowid

        cursor = await self._conn.execute("SELECT * FROM watches WHERE id = ?", (watch_id,))
        row = await cursor.fetchone()
        return _row_to_watch(row)  # type: ignore[arg-type]

    async def list_active(
        self,
        *,
        channel_id: str | None = None,
        user_id: str | None = None,
    ) -> list[WatchRow]:
        clauses = ["active = 1"]
        params: list[str] = []
        if channel_id is not None:
            clauses.append("channel_id = ?")
            params.append(channel_id)
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        where = " AND ".join(clauses)
        cursor = await self._conn.execute(
            f"SELECT * FROM watches WHERE {where}", params
        )
        rows = await cursor.fetchall()
        return [_row_to_watch(r) for r in rows]

    async def cancel(self, watch_id: int, user_id: str) -> bool:
        cursor = await self._conn.execute(
            "UPDATE watches SET active = 0 WHERE id = ? AND user_id = ? AND active = 1",
            (watch_id, user_id),
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def get_all_active(self) -> list[WatchRow]:
        cursor = await self._conn.execute("SELECT * FROM watches WHERE active = 1")
        rows = await cursor.fetchall()
        return [_row_to_watch(r) for r in rows]

    async def expiring_soon(self, within_hours: int = 24) -> list[WatchRow]:
        now = _now_iso()
        deadline = (
            datetime.datetime.now(datetime.UTC)
            + datetime.timedelta(hours=within_hours)
        ).isoformat()
        cursor = await self._conn.execute(
            """SELECT * FROM watches
               WHERE active = 1
                 AND expires_at <= ?
                 AND expires_at > ?
                 AND reminder_sent_at IS NULL""",
            (deadline, now),
        )
        rows = await cursor.fetchall()
        return [_row_to_watch(r) for r in rows]

    async def expire_old(self) -> int:
        now = _now_iso()
        cursor = await self._conn.execute(
            "UPDATE watches SET active = 0 WHERE active = 1 AND expires_at < ?",
            (now,),
        )
        await self._conn.commit()
        return cursor.rowcount

    async def extend(self, watch_id: int, duration_seconds: int) -> bool:
        cursor = await self._conn.execute(
            "SELECT expires_at FROM watches WHERE id = ? AND active = 1",
            (watch_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return False
        old_expires = datetime.datetime.fromisoformat(row[0])
        new_expires = old_expires + datetime.timedelta(seconds=duration_seconds)
        cursor = await self._conn.execute(
            "UPDATE watches SET expires_at = ?, reminder_sent_at = NULL WHERE id = ?",
            (new_expires.isoformat(), watch_id),
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def mark_reminder_sent(self, watch_id: int) -> None:
        now = _now_iso()
        await self._conn.execute(
            "UPDATE watches SET reminder_sent_at = ? WHERE id = ?",
            (now, watch_id),
        )
        await self._conn.commit()

    async def increment_notification_count(self, watch_id: int, count: int = 1) -> None:
        await self._conn.execute(
            "UPDATE watches SET notification_count = notification_count + ? WHERE id = ?",
            (count, watch_id),
        )
        await self._conn.commit()

    async def get_stats(self, channel_id: str | None = None) -> list[WatchRow]:
        if channel_id is not None:
            cursor = await self._conn.execute(
                "SELECT * FROM watches WHERE active = 1 AND channel_id = ? "
                "ORDER BY notification_count DESC",
                (channel_id,),
            )
        else:
            cursor = await self._conn.execute(
                "SELECT * FROM watches WHERE active = 1 "
                "ORDER BY notification_count DESC"
            )
        rows = await cursor.fetchall()
        return [_row_to_watch(r) for r in rows]

    async def get_user_stats(self) -> list[tuple[str, int, int]]:
        cursor = await self._conn.execute(
            "SELECT user_id, COUNT(*) AS active_watch_count, "
            "SUM(notification_count) AS total_notifications "
            "FROM watches WHERE active = 1 "
            "GROUP BY user_id ORDER BY total_notifications DESC"
        )
        rows = await cursor.fetchall()
        return [(row[0], row[1], row[2]) for row in rows]

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None
