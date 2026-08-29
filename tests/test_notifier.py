from __future__ import annotations

from unittest.mock import patch

from osm_watch_slack.evaluator import DiffElement
from osm_watch_slack.notifier import (
    ChangesetMatch,
    NoteMatch,
    RateLimiter,
    format_digest,
    format_expiry_reminder,
    format_note_notification,
    format_notification,
)


def _make_match(
    *,
    changeset_id: int = 100,
    user: str = "testuser",
    comment: str | None = None,
    element_type: str = "node",
    element_id: int | None = None,
    watch_id: int = 1,
    channel_id: str = "C123",
    filter_text: str = "node[amenity=cafe]",
    elements: list[DiffElement] | None = None,
) -> ChangesetMatch:
    if elements is None:
        elements = [
            DiffElement(
                action="create",
                element_type="node",
                element_id=12345,
                changeset_id=changeset_id,
                user=user,
            )
        ]
    return ChangesetMatch(
        changeset_id=changeset_id,
        user=user,
        comment=comment,
        elements=elements,
        watch_id=watch_id,
        channel_id=channel_id,
        filter_text=filter_text,
        element_type=element_type,
        element_id=element_id,
    )


class TestFormatNotification:
    def test_basic_links(self) -> None:
        match = _make_match(changeset_id=42, user="mapper1")
        result = format_notification(match)
        blocks = result["blocks"]
        text = blocks[0]["text"]["text"]

        assert "*mapper1*" in text
        assert "https://osmcha.org/changesets/42" in text
        assert "https://www.openstreetmap.org/changeset/42" in text

    def test_unfurl_disabled(self) -> None:
        result = format_notification(_make_match())
        assert result["unfurl_links"] is False
        assert result["unfurl_media"] is False

    def test_element_link_present_when_element_id_set(self) -> None:
        match = _make_match(changeset_id=42, element_type="way", element_id=999)
        result = format_notification(match)
        text = result["blocks"][0]["text"]["text"]
        assert "https://www.openstreetmap.org/way/999" in text

    def test_element_link_absent_when_element_id_none(self) -> None:
        match = _make_match(changeset_id=42, element_id=None)
        result = format_notification(match)
        text = result["blocks"][0]["text"]["text"]
        assert "/node/" not in text
        assert "/way/" not in text
        assert "/relation/" not in text

    def test_comment_included(self) -> None:
        match = _make_match(comment="Fixed a typo in the park name")
        result = format_notification(match)
        text = result["blocks"][0]["text"]["text"]
        assert "Fixed a typo in the park name" in text

    def test_comment_truncated(self) -> None:
        long_comment = "A" * 300
        match = _make_match(comment=long_comment)
        result = format_notification(match)
        text = result["blocks"][0]["text"]["text"]
        assert "A" * 200 + "..." in text

    def test_context_block_has_filter_text(self) -> None:
        match = _make_match(filter_text="way[highway=residential]")
        result = format_notification(match)
        context = [b for b in result["blocks"] if b["type"] == "context"]
        assert len(context) == 1
        assert "`way[highway=residential]`" in context[0]["elements"][0]["text"]

    def test_compact_two_blocks(self) -> None:
        result = format_notification(_make_match())
        assert len(result["blocks"]) == 2
        assert result["blocks"][0]["type"] == "section"
        assert result["blocks"][1]["type"] == "context"


class TestFormatDigest:
    def test_includes_count_and_links(self) -> None:
        matches = [_make_match(changeset_id=i) for i in range(1, 6)]
        result = format_digest(matches, "node[amenity=cafe]")
        blocks = result["blocks"]

        assert "5 changesets" in blocks[0]["text"]["text"]
        assert "`node[amenity=cafe]`" in blocks[0]["text"]["text"]

        links_text = blocks[1]["text"]["text"]
        for i in range(1, 6):
            assert f"https://osmcha.org/changesets/{i}" in links_text

    def test_limits_to_20_links(self) -> None:
        matches = [_make_match(changeset_id=i) for i in range(1, 30)]
        result = format_digest(matches, "node[amenity]")
        links_text = result["blocks"][1]["text"]["text"]

        assert "https://osmcha.org/changesets/20" in links_text
        assert "https://osmcha.org/changesets/21" not in links_text


class TestRateLimiter:
    def test_under_limit_not_digest(self) -> None:
        rl = RateLimiter(max_per_minute=5)
        for _ in range(5):
            assert not rl.should_digest(1)
            rl.record(1)

    def test_over_limit_triggers_digest(self) -> None:
        rl = RateLimiter(max_per_minute=5)
        for _ in range(5):
            rl.record(1)
        assert rl.should_digest(1)

    def test_different_watches_independent(self) -> None:
        rl = RateLimiter(max_per_minute=5)
        for _ in range(5):
            rl.record(1)
        assert rl.should_digest(1)
        assert not rl.should_digest(2)

    def test_old_timestamps_pruned(self) -> None:
        rl = RateLimiter(max_per_minute=5)

        base_time = 1000.0
        with patch("osm_watch_slack.notifier.time") as mock_time:
            mock_time.monotonic.return_value = base_time
            for _ in range(5):
                rl.record(1)

            mock_time.monotonic.return_value = base_time + 61.0
            assert not rl.should_digest(1)


class TestFormatExpiryReminder:
    def test_includes_button(self) -> None:
        result = format_expiry_reminder(
            watch_id=42,
            filter_text="node[amenity=cafe]",
            expires_at="2025-06-15T12:00:00",
        )
        blocks = result["blocks"]

        assert "`node[amenity=cafe]`" in blocks[0]["text"]["text"]
        assert "2025-06-15T12:00:00" in blocks[0]["text"]["text"]

        actions = [b for b in blocks if b["type"] == "actions"]
        assert len(actions) == 1
        button = actions[0]["elements"][0]
        assert button["type"] == "button"
        assert button["action_id"] == "extend_watch"
        assert button["value"] == "42"
        assert button["text"]["text"] == "Extend 1 week"


class TestFormatNoteNotification:
    def _make_note_match(self, **kwargs) -> NoteMatch:
        defaults = dict(
            note_id=5001,
            user="mapper1",
            text="Building is missing",
            url="https://www.openstreetmap.org/note/5001",
            lat=40.75,
            lon=-73.95,
            watch_id=1,
            channel_id="C123",
            filter_text="note(bbox:40.7,-74.0,40.8,-73.9)",
        )
        defaults.update(kwargs)
        return NoteMatch(**defaults)

    def test_basic_note_link(self) -> None:
        result = format_note_notification(self._make_note_match())
        blocks = result["blocks"]
        text = blocks[0]["text"]["text"]
        assert "https://www.openstreetmap.org/note/5001" in text
        assert "note #5001" in text
        assert "*mapper1*" in text

    def test_anonymous_user(self) -> None:
        result = format_note_notification(self._make_note_match(user=None))
        text = result["blocks"][0]["text"]["text"]
        assert "Anonymous" in text

    def test_note_text_included(self) -> None:
        result = format_note_notification(
            self._make_note_match(text="Fix this road")
        )
        text = result["blocks"][0]["text"]["text"]
        assert "Fix this road" in text

    def test_note_text_truncated(self) -> None:
        long_text = "A" * 300
        result = format_note_notification(self._make_note_match(text=long_text))
        text = result["blocks"][0]["text"]["text"]
        assert "A" * 200 + "..." in text

    def test_empty_note_text(self) -> None:
        result = format_note_notification(self._make_note_match(text=""))
        text = result["blocks"][0]["text"]["text"]
        # No italicized text line when empty
        assert "\n_" not in text

    def test_unfurl_disabled(self) -> None:
        result = format_note_notification(self._make_note_match())
        assert result["unfurl_links"] is False
        assert result["unfurl_media"] is False

    def test_context_block_has_filter_text(self) -> None:
        result = format_note_notification(self._make_note_match())
        context = [b for b in result["blocks"] if b["type"] == "context"]
        assert len(context) == 1
        assert "`note(bbox:40.7,-74.0,40.8,-73.9)`" in context[0]["elements"][0]["text"]

    def test_two_blocks(self) -> None:
        result = format_note_notification(self._make_note_match())
        assert len(result["blocks"]) == 2
        assert result["blocks"][0]["type"] == "section"
        assert result["blocks"][1]["type"] == "context"
