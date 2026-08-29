from __future__ import annotations

import httpx
import pytest

from osm_watch_slack.changeset_comments import (
    ChangesetComment,
    ChangesetCommentConsumer,
    ChangesetCommentFilter,
    comment_matches,
)
from osm_watch_slack.notifier import CommentMatch, format_comment_notification

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_comment(
    *,
    changeset_id: int = 100,
    comment_id: int = 0,
    user: str = "commenter1",
    text: str = "This looks wrong",
    date: str = "2025-07-01T12:00:00Z",
    changeset_user: str = "mapper1",
    changeset_bbox: tuple[float, float, float, float] | None = (40.0, -74.0, 41.0, -73.0),
) -> ChangesetComment:
    return ChangesetComment(
        changeset_id=changeset_id,
        comment_id=comment_id,
        user=user,
        text=text,
        date=date,
        changeset_user=changeset_user,
        changeset_bbox=changeset_bbox,
    )


# ---------------------------------------------------------------------------
# comment_matches tests
# ---------------------------------------------------------------------------

class TestCommentMatchesOsmUser:
    def test_matches_changeset_owner(self) -> None:
        f = ChangesetCommentFilter(osm_user="mapper1")
        comment = _make_comment(changeset_user="mapper1")
        assert comment_matches(f, comment) is True

    def test_no_match_wrong_user(self) -> None:
        f = ChangesetCommentFilter(osm_user="mapper1")
        comment = _make_comment(changeset_user="mapper2")
        assert comment_matches(f, comment) is False


class TestCommentMatchesCommentText:
    def test_substring_match(self) -> None:
        f = ChangesetCommentFilter(comment_text="looks wrong")
        comment = _make_comment(text="This looks wrong to me")
        assert comment_matches(f, comment) is True

    def test_case_insensitive(self) -> None:
        f = ChangesetCommentFilter(comment_text="LOOKS WRONG")
        comment = _make_comment(text="This looks wrong to me")
        assert comment_matches(f, comment) is True

    def test_no_match_missing_text(self) -> None:
        f = ChangesetCommentFilter(comment_text="vandalism")
        comment = _make_comment(text="Nice edit!")
        assert comment_matches(f, comment) is False


class TestCommentMatchesBbox:
    def test_overlapping_bbox(self) -> None:
        # watch bbox overlaps changeset bbox
        f = ChangesetCommentFilter(bbox=(40.5, -73.5, 41.5, -72.5))
        comment = _make_comment(changeset_bbox=(40.0, -74.0, 41.0, -73.0))
        assert comment_matches(f, comment) is True

    def test_non_overlapping_bbox(self) -> None:
        # watch bbox entirely south of changeset bbox
        f = ChangesetCommentFilter(bbox=(30.0, -74.0, 35.0, -73.0))
        comment = _make_comment(changeset_bbox=(40.0, -74.0, 41.0, -73.0))
        assert comment_matches(f, comment) is False

    def test_bbox_no_changeset_bbox_matches(self) -> None:
        # changeset_bbox is None: bbox filter cannot reject, so it passes
        f = ChangesetCommentFilter(bbox=(40.0, -74.0, 41.0, -73.0))
        comment = _make_comment(changeset_bbox=None)
        assert comment_matches(f, comment) is True

    def test_contained_bbox(self) -> None:
        # watch bbox entirely inside changeset bbox
        f = ChangesetCommentFilter(bbox=(40.2, -73.8, 40.8, -73.2))
        comment = _make_comment(changeset_bbox=(40.0, -74.0, 41.0, -73.0))
        assert comment_matches(f, comment) is True


class TestCommentMatchesNoFilters:
    def test_empty_filter_matches_everything(self) -> None:
        f = ChangesetCommentFilter()
        comment = _make_comment()
        assert comment_matches(f, comment) is True


class TestCommentMatchesCombined:
    def test_user_and_text_both_must_match(self) -> None:
        f = ChangesetCommentFilter(osm_user="mapper1", comment_text="wrong")
        comment = _make_comment(changeset_user="mapper1", text="This is wrong")
        assert comment_matches(f, comment) is True

    def test_user_matches_text_does_not(self) -> None:
        f = ChangesetCommentFilter(osm_user="mapper1", comment_text="vandalism")
        comment = _make_comment(changeset_user="mapper1", text="Nice edit!")
        assert comment_matches(f, comment) is False


# ---------------------------------------------------------------------------
# ChangesetCommentConsumer tests
# ---------------------------------------------------------------------------

class TestConsumerPollFirstCall:
    @pytest.mark.anyio
    async def test_first_poll_returns_empty_and_sets_last_check(self) -> None:
        transport = httpx.MockTransport(lambda req: httpx.Response(200, json={}))
        async with httpx.AsyncClient(transport=transport) as client:
            consumer = ChangesetCommentConsumer(client)
            assert consumer.last_check is None

            result = await consumer.poll()
            assert result == []
            assert consumer.last_check is not None
            assert consumer.last_processed_at is not None


class TestConsumerPollWithResults:
    @pytest.mark.anyio
    async def test_returns_new_comments(self) -> None:
        # Set up a consumer that already has a last_check in the past
        call_count = 0

        def mock_handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            url = str(request.url)

            if "changesets.json" in url and "time=" in url:
                # Return one changeset
                return httpx.Response(200, json={
                    "changesets": [
                        {
                            "id": 42,
                            "user": "mapper1",
                            "min_lat": 40.0,
                            "min_lon": -74.0,
                            "max_lat": 41.0,
                            "max_lon": -73.0,
                        }
                    ]
                })
            elif "changeset/42.json" in url:
                # Return changeset with discussion
                return httpx.Response(200, json={
                    "changeset": {
                        "id": 42,
                        "user": "mapper1",
                        "discussion": [
                            {
                                "user": "oldcommenter",
                                "text": "Old comment",
                                "date": "2020-01-01T00:00:00Z",
                            },
                            {
                                "user": "reviewer1",
                                "text": "This looks like vandalism",
                                "date": "2099-01-01T12:00:00Z",
                            },
                        ],
                    }
                })
            return httpx.Response(404)

        transport = httpx.MockTransport(mock_handler)
        async with httpx.AsyncClient(transport=transport) as client:
            consumer = ChangesetCommentConsumer(client)
            # Set last_check to something in the past
            consumer.last_check = "2025-01-01T00:00:00+00:00"
            consumer.last_processed_at = consumer.last_check

            result = await consumer.poll()

            assert len(result) == 1
            comment = result[0]
            assert comment.changeset_id == 42
            assert comment.user == "reviewer1"
            assert comment.text == "This looks like vandalism"
            assert comment.changeset_user == "mapper1"
            assert comment.changeset_bbox == (40.0, -74.0, 41.0, -73.0)
            assert comment.comment_id == 1

    @pytest.mark.anyio
    async def test_skips_old_comments(self) -> None:
        """Comments with dates before last_check are not returned."""

        def mock_handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "changesets.json" in url:
                return httpx.Response(200, json={
                    "changesets": [{"id": 99, "user": "mapper2"}]
                })
            elif "changeset/99.json" in url:
                return httpx.Response(200, json={
                    "changeset": {
                        "id": 99,
                        "user": "mapper2",
                        "discussion": [
                            {
                                "user": "olduser",
                                "text": "Old comment",
                                "date": "2020-01-01T00:00:00Z",
                            },
                        ],
                    }
                })
            return httpx.Response(404)

        transport = httpx.MockTransport(mock_handler)
        async with httpx.AsyncClient(transport=transport) as client:
            consumer = ChangesetCommentConsumer(client)
            consumer.last_check = "2025-01-01T00:00:00+00:00"

            result = await consumer.poll()
            assert result == []

    @pytest.mark.anyio
    async def test_deduplicates_across_polls(self) -> None:
        """Same comment is not returned twice across consecutive polls."""

        def mock_handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "changesets.json" in url:
                return httpx.Response(200, json={
                    "changesets": [{"id": 42, "user": "mapper1"}]
                })
            elif "changeset/42.json" in url:
                return httpx.Response(200, json={
                    "changeset": {
                        "id": 42,
                        "user": "mapper1",
                        "discussion": [
                            {
                                "user": "reviewer1",
                                "text": "Comment",
                                "date": "2099-01-01T12:00:00Z",
                            },
                        ],
                    }
                })
            return httpx.Response(404)

        transport = httpx.MockTransport(mock_handler)
        async with httpx.AsyncClient(transport=transport) as client:
            consumer = ChangesetCommentConsumer(client)
            consumer.last_check = "2025-01-01T00:00:00+00:00"

            first = await consumer.poll()
            assert len(first) == 1

            # Reset last_check to allow time-based filtering to pass
            consumer.last_check = "2025-01-01T00:00:00+00:00"
            second = await consumer.poll()
            # The comment was already seen, so it should be skipped
            assert len(second) == 0

    @pytest.mark.anyio
    async def test_handles_http_error_gracefully(self) -> None:
        """HTTP error on changeset listing returns empty list."""

        def mock_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        transport = httpx.MockTransport(mock_handler)
        async with httpx.AsyncClient(transport=transport) as client:
            consumer = ChangesetCommentConsumer(client)
            consumer.last_check = "2025-01-01T00:00:00+00:00"

            result = await consumer.poll()
            assert result == []

    @pytest.mark.anyio
    async def test_handles_discussion_fetch_error(self) -> None:
        """Error fetching a single changeset's discussion skips it."""

        def mock_handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "changesets.json" in url:
                return httpx.Response(200, json={
                    "changesets": [{"id": 42, "user": "mapper1"}]
                })
            elif "changeset/42.json" in url:
                return httpx.Response(500)
            return httpx.Response(404)

        transport = httpx.MockTransport(mock_handler)
        async with httpx.AsyncClient(transport=transport) as client:
            consumer = ChangesetCommentConsumer(client)
            consumer.last_check = "2025-01-01T00:00:00+00:00"

            result = await consumer.poll()
            assert result == []

    @pytest.mark.anyio
    async def test_changeset_without_bbox(self) -> None:
        """Changeset without bbox fields results in None bbox."""

        def mock_handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "changesets.json" in url:
                return httpx.Response(200, json={
                    "changesets": [{"id": 50, "user": "mapper3"}]
                })
            elif "changeset/50.json" in url:
                return httpx.Response(200, json={
                    "changeset": {
                        "id": 50,
                        "user": "mapper3",
                        "discussion": [
                            {
                                "user": "reviewer2",
                                "text": "Question",
                                "date": "2099-01-01T12:00:00Z",
                            },
                        ],
                    }
                })
            return httpx.Response(404)

        transport = httpx.MockTransport(mock_handler)
        async with httpx.AsyncClient(transport=transport) as client:
            consumer = ChangesetCommentConsumer(client)
            consumer.last_check = "2025-01-01T00:00:00+00:00"

            result = await consumer.poll()
            assert len(result) == 1
            assert result[0].changeset_bbox is None


# ---------------------------------------------------------------------------
# format_comment_notification tests
# ---------------------------------------------------------------------------

class TestFormatCommentNotification:
    def test_basic_structure(self) -> None:
        match = CommentMatch(
            changeset_id=42,
            comment_user="reviewer1",
            comment_text="This looks wrong",
            changeset_user="mapper1",
            watch_id=1,
            channel_id="C123",
            filter_text="changeset_comment(user:mapper1)",
        )
        result = format_comment_notification(match)

        assert "blocks" in result
        assert result["unfurl_links"] is False
        assert result["unfurl_media"] is False

        blocks = result["blocks"]
        assert len(blocks) == 2
        assert blocks[0]["type"] == "section"
        assert blocks[1]["type"] == "context"

    def test_contains_links(self) -> None:
        match = CommentMatch(
            changeset_id=42,
            comment_user="reviewer1",
            comment_text="Check this",
            changeset_user="mapper1",
            watch_id=1,
            channel_id="C123",
            filter_text="changeset_comment(user:mapper1)",
        )
        result = format_comment_notification(match)
        text = result["blocks"][0]["text"]["text"]

        assert "https://www.openstreetmap.org/changeset/42" in text
        assert "https://osmcha.org/changesets/42" in text
        assert "*reviewer1*" in text
        assert "mapper1" in text

    def test_comment_text_included(self) -> None:
        match = CommentMatch(
            changeset_id=42,
            comment_user="reviewer1",
            comment_text="Please fix the tagging",
            changeset_user="mapper1",
            watch_id=1,
            channel_id="C123",
            filter_text="changeset_comment(user:mapper1)",
        )
        result = format_comment_notification(match)
        text = result["blocks"][0]["text"]["text"]
        assert "Please fix the tagging" in text

    def test_long_comment_truncated(self) -> None:
        long_text = "A" * 300
        match = CommentMatch(
            changeset_id=42,
            comment_user="reviewer1",
            comment_text=long_text,
            changeset_user="mapper1",
            watch_id=1,
            channel_id="C123",
            filter_text="changeset_comment(user:mapper1)",
        )
        result = format_comment_notification(match)
        text = result["blocks"][0]["text"]["text"]
        assert "A" * 200 + "..." in text
        assert "A" * 201 not in text

    def test_empty_comment_text(self) -> None:
        match = CommentMatch(
            changeset_id=42,
            comment_user="reviewer1",
            comment_text="",
            changeset_user="mapper1",
            watch_id=1,
            channel_id="C123",
            filter_text="changeset_comment(user:mapper1)",
        )
        result = format_comment_notification(match)
        text = result["blocks"][0]["text"]["text"]
        # Empty comment should not add italic text line
        assert text.endswith("by mapper1")

    def test_context_block_has_filter(self) -> None:
        match = CommentMatch(
            changeset_id=42,
            comment_user="reviewer1",
            comment_text="Check",
            changeset_user="mapper1",
            watch_id=1,
            channel_id="C123",
            filter_text="changeset_comment(user:mapper1)",
        )
        result = format_comment_notification(match)
        context = result["blocks"][1]
        assert context["type"] == "context"
        assert "`changeset_comment(user:mapper1)`" in context["elements"][0]["text"]
