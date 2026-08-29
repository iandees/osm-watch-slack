from __future__ import annotations

import datetime
import json
import logging

from slack_bolt.async_app import AsyncApp

from . import dsl
from .config import Config
from .dsl import ParseError, split_command, to_dsl
from .store import CapExceededError, WatchStore

logger = logging.getLogger(__name__)


def _format_duration(delta: datetime.timedelta) -> str:
    total_seconds = int(delta.total_seconds())
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60
    if days >= 7 and days % 7 == 0:
        weeks = days // 7
        return f"{weeks} week{'s' if weeks != 1 else ''}"
    if days > 0:
        return f"{days} day{'s' if days != 1 else ''}"
    if hours > 0:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    return f"{minutes} minute{'s' if minutes != 1 else ''}"


HELP_TEXT = """\
*/osmwatch* - Watch OpenStreetMap changes

*Type:* `node`, `way`, `relation`, or `nwr` (any type)
*Filters:* `[tag]`, `[tag=value]`, `(new|changed|deleted)`, `(bbox:s,w,n,e)`
*User filters:* `(user:name)`, `(uid:12345)`, `(user_age:<30d)`

*Examples:*
- `relation(12345)[name]` — name changes on a specific relation
- `node[amenity=hospital](new)(bbox:40.7,-74.0,40.8,-73.9)` — new hospitals in a bbox
- `way[highway](deleted)` — highway deletions
- `nwr[building](user_age:<30d)` — building edits by new mappers
- `nwr(user:SomeMapper)` — all edits by a specific user

*Optional:* `expires:<duration>` (e.g. `expires:3d`, `expires:2w`). Default 1 week, max 6mo.

*Subcommands:*
- `/osmwatch list` — active watches in this channel
- `/osmwatch cancel <id>` — cancel a watch you created
- `/osmwatch stats` — watch statistics
- `/osmwatch help` — this help
"""


def create_app(config: Config, store: WatchStore) -> AsyncApp:
    """Create and configure the Slack Bolt async app."""
    app = AsyncApp(token=config.slack_bot_token)

    @app.command("/osmwatch")
    async def handle_osmwatch(ack, body, respond):
        await ack()

        text = (body.get("text") or "").strip()
        user_id = body["user_id"]
        channel_id = body["channel_id"]

        # Help
        if not text or text == "help":
            await respond(text=HELP_TEXT, response_type="ephemeral")
            return

        # List
        if text == "list":
            watches = await store.list_active(channel_id=channel_id)
            if not watches:
                await respond(
                    text="No active watches in this channel.",
                    response_type="ephemeral",
                )
                return
            lines = []
            for w in watches:
                lines.append(
                    f"*#{w.id}* `{w.filter_text}` by <@{w.user_id}> - expires {w.expires_at}"
                )
            await respond(text="\n".join(lines), response_type="ephemeral")
            return

        # Stats
        if text == "stats":
            channel_watches = await store.get_stats(channel_id=channel_id)
            user_stats = await store.get_user_stats()

            if not channel_watches and not user_stats:
                await respond(text="No active watches.", response_type="ephemeral")
                return

            lines: list[str] = []
            if channel_watches:
                lines.append("*Channel watches (by notifications sent):*")
                for i, w in enumerate(channel_watches, 1):
                    lines.append(
                        f"#{i} `{w.filter_text}` by <@{w.user_id}>"
                        f" — {w.notification_count} notification"
                        f"{'s' if w.notification_count != 1 else ''}"
                    )
            else:
                lines.append("No active watches in this channel.")

            if user_stats:
                lines.append("")
                lines.append("*Top users (all channels):*")
                for uid, watch_count, total in user_stats:
                    lines.append(
                        f"<@{uid}> — {watch_count} watch"
                        f"{'es' if watch_count != 1 else ''},"
                        f" {total} total notification"
                        f"{'s' if total != 1 else ''}"
                    )

            await respond(text="\n".join(lines), response_type="ephemeral")
            return

        # Cancel
        if text.startswith("cancel "):
            id_str = text[len("cancel "):].strip()
            try:
                watch_id = int(id_str)
            except ValueError:
                await respond(
                    text=f"Invalid watch ID: `{id_str}`",
                    response_type="ephemeral",
                )
                return
            cancelled = await store.cancel(watch_id, user_id)
            if cancelled:
                await respond(
                    text=f"Watch #{watch_id} cancelled.",
                    response_type="ephemeral",
                )
            else:
                await respond(
                    text=f"Watch not found or not yours: #{watch_id}",
                    response_type="ephemeral",
                )
            return

        # Create watch from DSL
        try:
            filter_text, expires_delta = split_command(text)
        except ParseError as e:
            await respond(text=f"Parse error: {e}", response_type="ephemeral")
            return

        try:
            watch_filter = dsl.parse(filter_text)
        except ParseError as e:
            await respond(text=f"Parse error: {e}", response_type="ephemeral")
            return

        expires_at = (
            datetime.datetime.now(datetime.UTC) + expires_delta
        ).isoformat()
        filter_json = json.dumps(watch_filter.to_dict())
        dsl_text = to_dsl(watch_filter)

        try:
            watch = await store.create(
                user_id=user_id,
                channel_id=channel_id,
                filter_text=dsl_text,
                filter_json=filter_json,
                expires_at=expires_at,
            )
        except CapExceededError as e:
            await respond(text=str(e), response_type="ephemeral")
            return

        # Join the channel so we can post notifications later.
        try:
            await app.client.conversations_join(channel=channel_id)
        except Exception:
            pass

        await respond(
            text=(
                f"Watch #{watch.id} created: `{dsl_text}`"
                f" — expires in {_format_duration(expires_delta)}"
            ),
            response_type="in_channel",
        )

    @app.action("extend_watch")
    async def handle_extend_watch(ack, body, respond):
        await ack()

        watch_id = body["actions"][0]["value"]
        extended = await store.extend(int(watch_id), 7 * 86400)
        if extended:
            await respond(
                text=f"Watch #{watch_id} extended by 1 week.",
                replace_original=True,
            )
        else:
            await respond(
                text=f"Watch #{watch_id} not found or already expired.",
                replace_original=True,
            )

    return app
