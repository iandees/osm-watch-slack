from __future__ import annotations

from unittest.mock import patch

from osm_watch_slack.evaluator import DiffElement
from osm_watch_slack.notifier import (
    ChangesetMatch,
    RateLimiter,
    format_digest,
    format_expiry_reminder,
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
        match = _make_match(changeset_id=42)
        result = format_notification(match)
        blocks = result["blocks"]

        # Should have author section, links section, and context block
        assert len(blocks) >= 3

        # Check OSMCha URL
        links_text = blocks[1]["text"]["text"]
        assert "https://osmcha.org/changesets/42" in links_text

        # Check osm.org URL
        assert "https://www.openstreetmap.org/changeset/42" in links_text

    def test_element_link_present_when_element_id_set(self) -> None:
        match = _make_match(changeset_id=42, element_type="way", element_id=999)
        result = format_notification(match)
        links_text = result["blocks"][1]["text"]["text"]
        assert "https://www.openstreetmap.org/way/999" in links_text

    def test_element_link_absent_when_element_id_none(self) -> None:
        match = _make_match(changeset_id=42, element_id=None)
        result = format_notification(match)
        links_text = result["blocks"][1]["text"]["text"]
        # Should not contain an element-specific link pattern
        assert "/node/" not in links_text
        assert "/way/" not in links_text
        assert "/relation/" not in links_text

    def test_comment_included(self) -> None:
        match = _make_match(comment="Fixed a typo in the park name")
        result = format_notification(match)
        blocks = result["blocks"]
        comment_texts = [
            b["text"]["text"]
            for b in blocks
            if b["type"] == "section" and "Fixed a typo" in b["text"]["text"]
        ]
        assert len(comment_texts) == 1

    def test_comment_truncated(self) -> None:
        long_comment = "A" * 300
        match = _make_match(comment=long_comment)
        result = format_notification(match)
        blocks = result["blocks"]
        comment_blocks = [
            b for b in blocks
            if b["type"] == "section" and "AAA" in b["text"]["text"]
        ]
        assert len(comment_blocks) == 1
        comment_text = comment_blocks[0]["text"]["text"]
        # Truncated to 200 chars plus "..."
        assert len(comment_text) == 203
        assert comment_text.endswith("...")

    def test_context_block_has_filter_text(self) -> None:
        match = _make_match(filter_text="way[highway=residential]")
        result = format_notification(match)
        context = [b for b in result["blocks"] if b["type"] == "context"]
        assert len(context) == 1
        assert "`way[highway=residential]`" in context[0]["elements"][0]["text"]


class TestFormatDigest:
    def test_includes_count_and_links(self) -> None:
        matches = [_make_match(changeset_id=i) for i in range(1, 6)]
        result = format_digest(matches, "node[amenity=cafe]")
        blocks = result["blocks"]

        # Count section
        assert "5 changesets" in blocks[0]["text"]["text"]
        assert "`node[amenity=cafe]`" in blocks[0]["text"]["text"]

        # Links section has OSMCha links for all 5
        links_text = blocks[1]["text"]["text"]
        for i in range(1, 6):
            assert f"https://osmcha.org/changesets/{i}" in links_text

    def test_limits_to_20_links(self) -> None:
        matches = [_make_match(changeset_id=i) for i in range(1, 30)]
        result = format_digest(matches, "node[amenity]")
        links_text = result["blocks"][1]["text"]["text"]

        # Should include changeset 20 but not 21
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
        # 5 recorded, so next check should say digest
        assert rl.should_digest(1)

    def test_different_watches_independent(self) -> None:
        rl = RateLimiter(max_per_minute=5)
        for _ in range(5):
            rl.record(1)
        # Watch 1 is at limit, watch 2 is not
        assert rl.should_digest(1)
        assert not rl.should_digest(2)

    def test_old_timestamps_pruned(self) -> None:
        rl = RateLimiter(max_per_minute=5)

        # Insert timestamps that appear to be old by patching monotonic
        base_time = 1000.0
        with patch("osm_watch_slack.notifier.time") as mock_time:
            mock_time.monotonic.return_value = base_time
            for _ in range(5):
                rl.record(1)

            # Now jump forward 61 seconds -- old timestamps should be pruned
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

        # Section with expiry info
        assert "`node[amenity=cafe]`" in blocks[0]["text"]["text"]
        assert "2025-06-15T12:00:00" in blocks[0]["text"]["text"]

        # Actions block with button
        actions = [b for b in blocks if b["type"] == "actions"]
        assert len(actions) == 1
        button = actions[0]["elements"][0]
        assert button["type"] == "button"
        assert button["action_id"] == "extend_watch"
        assert button["value"] == "42"
        assert button["text"]["text"] == "Extend 1 week"
