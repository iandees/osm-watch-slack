from __future__ import annotations

import time
from dataclasses import dataclass

from .evaluator import DiffElement


@dataclass
class ChangesetMatch:
    changeset_id: int
    user: str
    comment: str | None
    elements: list[DiffElement]
    watch_id: int
    channel_id: str
    filter_text: str
    element_type: str
    element_id: int | None


def format_notification(match: ChangesetMatch) -> dict:
    """Return a Slack Block Kit message payload for a single changeset match."""
    action_counts: dict[str, int] = {}
    for el in match.elements:
        action_counts[el.action] = action_counts.get(el.action, 0) + 1
    summary_parts = [f"{count} {action}" for action, count in sorted(action_counts.items())]
    summary = ", ".join(summary_parts) if summary_parts else "changes"

    osmcha_url = f"https://osmcha.org/changesets/{match.changeset_id}"
    osm_url = f"https://www.openstreetmap.org/changeset/{match.changeset_id}"

    # Main line: User *name* made N action in changeset link (osmcha)
    text = (
        f"User *{match.user}* made {summary} in changeset "
        f"<{osm_url}|{match.changeset_id}> (<{osmcha_url}|osmcha>)"
    )
    if match.element_id is not None:
        element_url = (
            f"https://www.openstreetmap.org/{match.element_type}/{match.element_id}"
        )
        text += f" | <{element_url}|{match.element_type}/{match.element_id}>"

    if match.comment is not None:
        comment_text = match.comment
        if len(comment_text) > 200:
            comment_text = comment_text[:200] + "..."
        text += f"\n_{comment_text}_"

    blocks: list[dict] = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": text},
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"Watch: `{match.filter_text}`"},
            ],
        },
    ]

    return {"blocks": blocks, "unfurl_links": False, "unfurl_media": False}


@dataclass
class NoteMatch:
    note_id: int
    user: str | None
    text: str
    url: str
    lat: float
    lon: float
    watch_id: int
    channel_id: str
    filter_text: str


def format_note_notification(match: NoteMatch) -> dict:
    """Slack Block Kit message for a note match."""
    user_text = f"*{match.user}*" if match.user else "Anonymous"
    text = f"{user_text} created <{match.url}|note #{match.note_id}>"

    # Truncate note text
    note_text = match.text
    if len(note_text) > 200:
        note_text = note_text[:200] + "..."
    if note_text:
        text += f"\n_{note_text}_"

    blocks: list[dict] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"Watch: `{match.filter_text}`"},
            ],
        },
    ]
    return {"blocks": blocks, "unfurl_links": False, "unfurl_media": False}


def format_digest(matches: list[ChangesetMatch], filter_text: str) -> dict:
    """Return a Slack Block Kit digest message for multiple changeset matches."""
    count = len(matches)
    blocks: list[dict] = []

    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"{count} changesets matched your watch `{filter_text}`",
        },
    })

    # Links to first 20 changesets
    changeset_links = []
    for m in matches[:20]:
        url = f"https://osmcha.org/changesets/{m.changeset_id}"
        changeset_links.append(f"<{url}|#{m.changeset_id}>")
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": ", ".join(changeset_links),
        },
    })

    return {"blocks": blocks}


class RateLimiter:
    def __init__(self, max_per_minute: int = 5) -> None:
        self.max_per_minute = max_per_minute
        self._timestamps: dict[int, list[float]] = {}

    def _prune(self, watch_id: int) -> None:
        """Remove timestamps older than 60 seconds."""
        cutoff = time.monotonic() - 60.0
        if watch_id in self._timestamps:
            self._timestamps[watch_id] = [
                t for t in self._timestamps[watch_id] if t > cutoff
            ]

    def should_digest(self, watch_id: int) -> bool:
        """Return True if the watch has exceeded its per-minute quota."""
        self._prune(watch_id)
        timestamps = self._timestamps.get(watch_id, [])
        return len(timestamps) >= self.max_per_minute

    def record(self, watch_id: int) -> None:
        """Record a sent message timestamp."""
        if watch_id not in self._timestamps:
            self._timestamps[watch_id] = []
        self._timestamps[watch_id].append(time.monotonic())


@dataclass
class CommentMatch:
    changeset_id: int
    comment_user: str
    comment_text: str
    changeset_user: str
    watch_id: int
    channel_id: str
    filter_text: str


def format_comment_notification(match: CommentMatch) -> dict:
    """Return a Slack Block Kit message payload for a changeset comment match."""
    cs_url = f"https://www.openstreetmap.org/changeset/{match.changeset_id}"
    osmcha_url = f"https://osmcha.org/changesets/{match.changeset_id}"

    text = (
        f"*{match.comment_user}* commented on "
        f"<{cs_url}|changeset {match.changeset_id}> "
        f"(<{osmcha_url}|osmcha>) by {match.changeset_user}"
    )

    comment_text = match.comment_text
    if len(comment_text) > 200:
        comment_text = comment_text[:200] + "..."
    if comment_text:
        text += f"\n_{comment_text}_"

    blocks: list[dict] = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": text},
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"Watch: `{match.filter_text}`"},
            ],
        },
    ]

    return {"blocks": blocks, "unfurl_links": False, "unfurl_media": False}


def format_expiry_reminder(watch_id: int, filter_text: str, expires_at: str) -> dict:
    """Return a Slack Block Kit message for an expiring watch."""
    blocks: list[dict] = []

    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"Your watch `{filter_text}` expires {expires_at}",
        },
    })

    blocks.append({
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": "Extend 1 week",
                },
                "action_id": "extend_watch",
                "value": str(watch_id),
            }
        ],
    })

    return {"blocks": blocks}
