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

HELP_TEXT = """\
*/osmwatch* - Watch OpenStreetMap changes

*DSL grammar:*
`<type>[<id>]?[<tag-filter>]*(new|changed|deleted)?(bbox:s,w,n,e)?`

*Examples:*
- `relation(12345)[name]` - watch a specific relation with a name tag
- `node[amenity=hospital](new)(bbox:40.7,-74.0,40.8,-73.9)` - new hospitals in a bbox
- `way[highway](deleted)` - deleted highways

*Optional:* append `expires:<duration>` (e.g. `expires:3d`, `expires:2w`). Default is 1 week.

*Subcommands:*
- `/osmwatch list` - list active watches in this channel
- `/osmwatch cancel <id>` - cancel a watch you created
- `/osmwatch help` - show this help
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

        await respond(
            text=f"Watch #{watch.id} created: `{dsl_text}` — expires {watch.expires_at}",
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
