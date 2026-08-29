from __future__ import annotations

import asyncio
import json
import logging
import signal
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import httpx
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

from . import USER_AGENT
from .app import create_app
from .config import Config
from .consumer import DiffConsumer
from .dsl import WatchFilter
from .evaluator import DiffElement, matches
from .notifier import (
    ChangesetMatch,
    RateLimiter,
    format_digest,
    format_expiry_reminder,
    format_notification,
)
from .store import WatchStore

log = logging.getLogger("osm_watch_slack")


async def _fetch_user_created_at(
    http_client: httpx.AsyncClient,
    uid: int,
    cache: dict[int, str | None],
) -> str | None:
    """Fetch account creation date for an OSM user, using a persistent cache."""
    if uid in cache:
        return cache[uid]
    try:
        resp = await http_client.get(
            f"https://api.openstreetmap.org/api/0.6/user/{uid}.json",
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        created = data.get("user", {}).get("account_created")
        cache[uid] = created
    except Exception:
        log.warning("Failed to fetch user %d info", uid, exc_info=True)
        cache[uid] = None
    return cache[uid]


async def _fetch_changeset_comment(
    http_client: httpx.AsyncClient,
    changeset_id: int,
    cache: dict[int, str | None],
) -> str | None:
    """Fetch the comment tag from an OSM changeset, using a per-batch cache."""
    if changeset_id in cache:
        return cache[changeset_id]
    try:
        resp = await http_client.get(
            f"https://api.openstreetmap.org/api/0.6/changeset/{changeset_id}",
            timeout=30,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        cs = root.find("changeset")
        comment = None
        if cs is not None:
            for tag in cs.findall("tag"):
                if tag.attrib.get("k") == "comment":
                    comment = tag.attrib.get("v")
                    break
        cache[changeset_id] = comment
    except Exception:
        log.warning("Failed to fetch changeset %d comment", changeset_id, exc_info=True)
        cache[changeset_id] = None
    return cache[changeset_id]


async def main() -> None:
    config = Config.from_env()

    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Ensure data directory exists.
    Path(config.database_path).parent.mkdir(parents=True, exist_ok=True)

    store = WatchStore(config.database_path, config.user_watch_cap, config.channel_watch_cap)
    await store.initialize()

    http_client = httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, follow_redirects=True)

    consumer = DiffConsumer(
        config.state_path,
        http_client,
        config.overpass_base_url,
        config.replication_state_url,
    )

    app = create_app(config, store)
    socket_handler = AsyncSocketModeHandler(app, config.slack_app_token)
    rate_limiter = RateLimiter(config.digest_threshold)
    # Persistent cache: uid -> account_created ISO string. Account creation
    # dates never change, so this cache is valid for the process lifetime.
    user_created_cache: dict[int, str | None] = {}

    async def on_diff_batch(elements: list[DiffElement]) -> None:
        watches = await store.get_all_active()
        if not watches:
            return

        # Check if any watch uses user_age filter — only then do we need lookups.
        parsed_watches: list[tuple[int, str, str, WatchFilter]] = []
        needs_user_age = False
        for watch in watches:
            try:
                wf = WatchFilter.from_dict(json.loads(watch.filter_json))
            except Exception:
                log.warning("Skipping watch %d with invalid filter_json", watch.id)
                continue
            parsed_watches.append((watch.id, watch.channel_id, watch.filter_text, wf))
            if wf.user_age_max is not None:
                needs_user_age = True

        # Populate user_created_at on elements if any watch needs it.
        if needs_user_age:
            uids = {e.uid for e in elements if e.uid is not None}
            for uid in uids:
                created = await _fetch_user_created_at(
                    http_client, uid, user_created_cache
                )
                if created:
                    for e in elements:
                        if e.uid == uid:
                            e.user_created_at = created

        # Build (watch_id, changeset_id) -> list[DiffElement] grouping.
        grouped: dict[tuple[int, int], list[DiffElement]] = defaultdict(list)
        watch_meta: dict[int, tuple[str, str, WatchFilter]] = {}

        for watch_id, channel_id, filter_text, watch_filter in parsed_watches:
            watch_meta[watch_id] = (channel_id, filter_text, watch_filter)
            for element in elements:
                if matches(watch_filter, element):
                    grouped[(watch_id, element.changeset_id)].append(element)

        if not grouped:
            return

        # Cache changeset comments across this batch.
        comment_cache: dict[int, str | None] = {}

        # Group all matches by watch_id for rate-limiting decisions.
        per_watch: dict[int, list[ChangesetMatch]] = defaultdict(list)
        for (watch_id, changeset_id), elems in grouped.items():
            channel_id, filter_text, watch_filter = watch_meta[watch_id]
            comment = await _fetch_changeset_comment(http_client, changeset_id, comment_cache)
            user = elems[0].user if elems else ""
            cm = ChangesetMatch(
                changeset_id=changeset_id,
                user=user,
                comment=comment,
                elements=elems,
                watch_id=watch_id,
                channel_id=channel_id,
                filter_text=filter_text,
                element_type=watch_filter.element_type,
                element_id=watch_filter.element_id,
            )
            per_watch[watch_id].append(cm)

        # Send notifications per watch.
        for watch_id, changeset_matches in per_watch.items():
            channel_id = changeset_matches[0].channel_id
            filter_text = changeset_matches[0].filter_text

            sent_count = 0
            if rate_limiter.should_digest(watch_id):
                payload = format_digest(changeset_matches, filter_text)
                try:
                    await app.client.chat_postMessage(
                        channel=channel_id,
                        blocks=payload["blocks"],
                        text=f"{len(changeset_matches)} changesets matched watch",
                    )
                    sent_count = len(changeset_matches)
                except Exception:
                    log.warning("Failed to send digest for watch %d", watch_id, exc_info=True)
            else:
                for cm in changeset_matches:
                    payload = format_notification(cm)
                    try:
                        await app.client.chat_postMessage(
                            channel=cm.channel_id,
                            blocks=payload["blocks"],
                            text=f"OSM change in changeset {cm.changeset_id}",
                            unfurl_links=payload.get("unfurl_links", True),
                            unfurl_media=payload.get("unfurl_media", True),
                        )
                        rate_limiter.record(watch_id)
                        sent_count += 1
                    except Exception:
                        log.warning(
                            "Failed to send notification for watch %d changeset %d",
                            watch_id,
                            cm.changeset_id,
                            exc_info=True,
                        )
            if sent_count:
                await store.increment_notification_count(watch_id, sent_count)

    async def expiry_loop() -> None:
        while True:
            await asyncio.sleep(3600)
            try:
                expired_count = await store.expire_old()
                if expired_count:
                    log.info("Expired %d watches", expired_count)

                expiring = await store.expiring_soon(within_hours=24)
                for watch in expiring:
                    payload = format_expiry_reminder(watch.id, watch.filter_text, watch.expires_at)
                    try:
                        await app.client.chat_postMessage(
                            channel=watch.channel_id,
                            blocks=payload["blocks"],
                            text="Watch expiring soon",
                        )
                        await store.mark_reminder_sent(watch.id)
                    except Exception:
                        log.warning(
                            "Failed to send expiry reminder for watch %d",
                            watch.id,
                            exc_info=True,
                        )
            except Exception:
                log.warning("Error in expiry loop", exc_info=True)

    # Graceful shutdown on SIGTERM / SIGINT.
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def _request_shutdown() -> None:
        log.info("Shutdown signal received")
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _request_shutdown)

    async def _shutdown_watcher() -> None:
        await shutdown_event.wait()
        raise SystemExit(0)

    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(socket_handler.start_async(), name="slack-socket")
            tg.create_task(consumer.run(on_diff_batch), name="diff-consumer")
            tg.create_task(expiry_loop(), name="expiry-loop")
            tg.create_task(_shutdown_watcher(), name="shutdown-watcher")
    except* SystemExit:
        log.info("Shutting down gracefully")
    finally:
        await store.close()
        await http_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
