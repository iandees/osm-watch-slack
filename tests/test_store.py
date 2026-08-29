from __future__ import annotations

import datetime

import aiosqlite
import pytest

from osm_watch_slack.store import CapExceededError, WatchStore


def _future_iso(hours: int = 48) -> str:
    return (datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=hours)).isoformat()


def _past_iso(hours: int = 1) -> str:
    return (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=hours)).isoformat()


@pytest.fixture
async def store():
    s = WatchStore(":memory:")
    await s.initialize()
    yield s
    await s.close()


async def test_create_and_retrieve(store: WatchStore):
    watch = await store.create(
        user_id="U001",
        channel_id="C001",
        filter_text="amenity=hospital",
        filter_json='{"tag": "amenity=hospital"}',
        expires_at=_future_iso(),
    )
    assert watch.id is not None
    assert watch.user_id == "U001"
    assert watch.channel_id == "C001"
    assert watch.filter_text == "amenity=hospital"
    assert watch.active is True

    active = await store.list_active()
    assert len(active) == 1
    assert active[0].id == watch.id


async def test_per_user_cap(store: WatchStore):
    cap_store = WatchStore(":memory:", user_cap=3)
    await cap_store.initialize()
    try:
        for i in range(3):
            await cap_store.create(
                user_id="U001",
                channel_id=f"C{i:03}",
                filter_text=f"tag={i}",
                filter_json="{}",
                expires_at=_future_iso(),
            )
        with pytest.raises(CapExceededError, match="per-user"):
            await cap_store.create(
                user_id="U001",
                channel_id="C999",
                filter_text="one-too-many",
                filter_json="{}",
                expires_at=_future_iso(),
            )
    finally:
        await cap_store.close()


async def test_per_channel_cap(store: WatchStore):
    cap_store = WatchStore(":memory:", channel_cap=2)
    await cap_store.initialize()
    try:
        for i in range(2):
            await cap_store.create(
                user_id=f"U{i:03}",
                channel_id="C001",
                filter_text=f"tag={i}",
                filter_json="{}",
                expires_at=_future_iso(),
            )
        with pytest.raises(CapExceededError, match="per-channel"):
            await cap_store.create(
                user_id="U999",
                channel_id="C001",
                filter_text="one-too-many",
                filter_json="{}",
                expires_at=_future_iso(),
            )
    finally:
        await cap_store.close()


async def test_cancel_by_owner(store: WatchStore):
    watch = await store.create(
        user_id="U001",
        channel_id="C001",
        filter_text="highway=*",
        filter_json="{}",
        expires_at=_future_iso(),
    )
    result = await store.cancel(watch.id, "U001")
    assert result is True

    active = await store.list_active()
    assert len(active) == 0


async def test_cancel_by_non_owner(store: WatchStore):
    watch = await store.create(
        user_id="U001",
        channel_id="C001",
        filter_text="highway=*",
        filter_json="{}",
        expires_at=_future_iso(),
    )
    result = await store.cancel(watch.id, "U999")
    assert result is False

    active = await store.list_active()
    assert len(active) == 1


async def test_expire_old(store: WatchStore):
    # Create an already-expired watch.
    await store.create(
        user_id="U001",
        channel_id="C001",
        filter_text="old-watch",
        filter_json="{}",
        expires_at=_past_iso(hours=1),
    )
    # Create a non-expired watch.
    await store.create(
        user_id="U001",
        channel_id="C001",
        filter_text="fresh-watch",
        filter_json="{}",
        expires_at=_future_iso(),
    )

    count = await store.expire_old()
    assert count == 1

    active = await store.list_active()
    assert len(active) == 1
    assert active[0].filter_text == "fresh-watch"


async def test_expiring_soon(store: WatchStore):
    # Expiring in 12 hours -- should appear with default within_hours=24.
    soon_watch = await store.create(
        user_id="U001",
        channel_id="C001",
        filter_text="expiring-soon",
        filter_json="{}",
        expires_at=(
            datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=12)
        ).isoformat(),
    )
    # Expiring in 72 hours -- should NOT appear.
    await store.create(
        user_id="U001",
        channel_id="C001",
        filter_text="far-future",
        filter_json="{}",
        expires_at=_future_iso(hours=72),
    )

    result = await store.expiring_soon(within_hours=24)
    assert len(result) == 1
    assert result[0].id == soon_watch.id

    # Mark reminder sent, then confirm it no longer appears.
    await store.mark_reminder_sent(soon_watch.id)
    result = await store.expiring_soon(within_hours=24)
    assert len(result) == 0


async def test_extend(store: WatchStore):
    watch = await store.create(
        user_id="U001",
        channel_id="C001",
        filter_text="extendable",
        filter_json="{}",
        expires_at=_future_iso(hours=24),
    )
    # Mark reminder sent first so we can verify it gets cleared.
    await store.mark_reminder_sent(watch.id)

    original_expires = datetime.datetime.fromisoformat(watch.expires_at)
    result = await store.extend(watch.id, duration_seconds=3600)
    assert result is True

    active = await store.list_active()
    extended = [w for w in active if w.id == watch.id][0]
    new_expires = datetime.datetime.fromisoformat(extended.expires_at)
    assert new_expires > original_expires
    expected = original_expires + datetime.timedelta(seconds=3600)
    assert abs((new_expires - expected).total_seconds()) < 1
    assert extended.reminder_sent_at is None


async def test_list_active_filter_by_channel(store: WatchStore):
    await store.create(
        user_id="U001",
        channel_id="C001",
        filter_text="ch1",
        filter_json="{}",
        expires_at=_future_iso(),
    )
    await store.create(
        user_id="U001",
        channel_id="C002",
        filter_text="ch2",
        filter_json="{}",
        expires_at=_future_iso(),
    )

    result = await store.list_active(channel_id="C001")
    assert len(result) == 1
    assert result[0].filter_text == "ch1"


async def test_list_active_filter_by_user(store: WatchStore):
    await store.create(
        user_id="U001",
        channel_id="C001",
        filter_text="user1",
        filter_json="{}",
        expires_at=_future_iso(),
    )
    await store.create(
        user_id="U002",
        channel_id="C001",
        filter_text="user2",
        filter_json="{}",
        expires_at=_future_iso(),
    )

    result = await store.list_active(user_id="U002")
    assert len(result) == 1
    assert result[0].filter_text == "user2"


async def test_increment_notification_count(store: WatchStore):
    watch = await store.create(
        user_id="U001",
        channel_id="C001",
        filter_text="highway=*",
        filter_json="{}",
        expires_at=_future_iso(),
    )
    assert watch.notification_count == 0

    await store.increment_notification_count(watch.id, 1)
    await store.increment_notification_count(watch.id, 5)

    watches = await store.list_active()
    assert len(watches) == 1
    assert watches[0].notification_count == 6


async def test_get_stats(store: WatchStore):
    w1 = await store.create(
        user_id="U001",
        channel_id="C001",
        filter_text="low",
        filter_json="{}",
        expires_at=_future_iso(),
    )
    w2 = await store.create(
        user_id="U002",
        channel_id="C001",
        filter_text="high",
        filter_json="{}",
        expires_at=_future_iso(),
    )
    w3 = await store.create(
        user_id="U001",
        channel_id="C002",
        filter_text="other-channel",
        filter_json="{}",
        expires_at=_future_iso(),
    )

    await store.increment_notification_count(w1.id, 5)
    await store.increment_notification_count(w2.id, 20)
    await store.increment_notification_count(w3.id, 10)

    # All active watches, ordered by notification_count desc
    stats = await store.get_stats()
    assert len(stats) == 3
    assert stats[0].id == w2.id
    assert stats[0].notification_count == 20
    assert stats[1].id == w3.id
    assert stats[1].notification_count == 10
    assert stats[2].id == w1.id
    assert stats[2].notification_count == 5

    # Filtered by channel
    ch1_stats = await store.get_stats(channel_id="C001")
    assert len(ch1_stats) == 2
    assert ch1_stats[0].id == w2.id
    assert ch1_stats[1].id == w1.id


async def test_get_user_stats(store: WatchStore):
    w1 = await store.create(
        user_id="U001",
        channel_id="C001",
        filter_text="watch1",
        filter_json="{}",
        expires_at=_future_iso(),
    )
    w2 = await store.create(
        user_id="U001",
        channel_id="C002",
        filter_text="watch2",
        filter_json="{}",
        expires_at=_future_iso(),
    )
    w3 = await store.create(
        user_id="U002",
        channel_id="C001",
        filter_text="watch3",
        filter_json="{}",
        expires_at=_future_iso(),
    )

    await store.increment_notification_count(w1.id, 10)
    await store.increment_notification_count(w2.id, 20)
    await store.increment_notification_count(w3.id, 5)

    user_stats = await store.get_user_stats()
    assert len(user_stats) == 2
    # U001 has 30 total notifications across 2 watches
    assert user_stats[0] == ("U001", 2, 30)
    # U002 has 5 total notifications across 1 watch
    assert user_stats[1] == ("U002", 1, 5)


async def test_migration_v1_to_v2():
    """Create a v1 schema manually, then verify WatchStore.initialize() migrates it."""
    db_path = ":memory:"
    # Build a v1 database by hand (no notification_count column).
    db = await aiosqlite.connect(db_path)
    await db.executescript(
        """\
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version (version) VALUES (1);
        CREATE TABLE watches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            filter_text TEXT NOT NULL,
            filter_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            reminder_sent_at TEXT,
            active INTEGER NOT NULL DEFAULT 1
        );
        INSERT INTO watches (user_id, channel_id, filter_text, filter_json,
                             created_at, expires_at, active)
        VALUES ('U001', 'C001', 'old-watch', '{}',
                '2025-01-01T00:00:00', '2099-01-01T00:00:00', 1);
        """
    )
    await db.commit()

    # We need to use a file-backed DB because :memory: with a new connection
    # won't share data. Use the shared cache URI instead.
    await db.close()

    # Use a temp file approach for the migration test.
    import os
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        tmp_path = f.name

    try:
        # Re-create v1 database on disk.
        db = await aiosqlite.connect(tmp_path)
        await db.executescript(
            """\
            CREATE TABLE schema_version (version INTEGER NOT NULL);
            INSERT INTO schema_version (version) VALUES (1);
            CREATE TABLE watches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                filter_text TEXT NOT NULL,
                filter_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                reminder_sent_at TEXT,
                active INTEGER NOT NULL DEFAULT 1
            );
            INSERT INTO watches (user_id, channel_id, filter_text, filter_json,
                                 created_at, expires_at, active)
            VALUES ('U001', 'C001', 'old-watch', '{}',
                    '2025-01-01T00:00:00', '2099-01-01T00:00:00', 1);
            """
        )
        await db.commit()
        await db.close()

        # Now open via WatchStore and initialize -- should migrate.
        store = WatchStore(tmp_path)
        await store.initialize()

        # Verify schema_version is now 2.
        cursor = await store._conn.execute("SELECT version FROM schema_version")
        (version,) = await cursor.fetchone()
        assert version == 2

        # Verify the existing watch has notification_count defaulting to 0.
        watches = await store.get_all_active()
        assert len(watches) == 1
        assert watches[0].notification_count == 0
        assert watches[0].filter_text == "old-watch"

        await store.close()
    finally:
        os.unlink(tmp_path)
