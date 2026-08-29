from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChangesetCommentFilter:
    osm_user: str | None = None  # comments on changesets BY this user
    comment_text: str | None = None  # comments containing this text
    bbox: tuple[float, float, float, float] | None = None  # changeset in bbox


@dataclass
class ChangesetComment:
    changeset_id: int
    comment_id: int  # index in discussion
    user: str  # who wrote the comment
    text: str
    date: str  # ISO 8601
    changeset_user: str  # who created the changeset
    changeset_bbox: tuple[float, float, float, float] | None


class ChangesetCommentConsumer:
    """Polls the OSM API for new changeset discussion comments."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self.http_client = http_client
        self.last_check: str | None = None  # ISO timestamp
        self.last_processed_at: str | None = None
        self._seen_comments: set[tuple[int, int]] = set()  # (changeset_id, comment_idx)

    async def poll(self) -> list[ChangesetComment]:
        """Fetch new changeset comments since last check."""
        now = datetime.datetime.now(datetime.UTC)

        if self.last_check is None:
            # First run: set last_check to now, don't fetch anything
            self.last_check = now.isoformat()
            self.last_processed_at = self.last_check
            return []

        # Get recently modified changesets.
        # The time param finds changesets closed/modified after this time.
        params: dict[str, str] = {
            "time": self.last_check,
            "order": "newest",
            "limit": "50",
        }
        try:
            resp = await self.http_client.get(
                "https://api.openstreetmap.org/api/0.6/changesets.json",
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
        except Exception:
            logger.warning("Failed to fetch recent changesets", exc_info=True)
            return []

        data = resp.json()
        changesets = data.get("changesets", [])

        new_comments: list[ChangesetComment] = []
        for cs in changesets:
            cs_id = cs["id"]
            cs_user = cs.get("user", "")

            # Parse bbox if available
            cs_bbox: tuple[float, float, float, float] | None = None
            if all(k in cs for k in ("min_lat", "min_lon", "max_lat", "max_lon")):
                cs_bbox = (
                    float(cs["min_lat"]),
                    float(cs["min_lon"]),
                    float(cs["max_lat"]),
                    float(cs["max_lon"]),
                )

            # Fetch discussion for this changeset
            try:
                disc_resp = await self.http_client.get(
                    f"https://api.openstreetmap.org/api/0.6/changeset/{cs_id}.json",
                    params={"include_discussion": "true"},
                    timeout=30,
                )
                disc_resp.raise_for_status()
            except Exception:
                logger.warning(
                    "Failed to fetch discussion for changeset %d",
                    cs_id,
                    exc_info=True,
                )
                continue

            disc_data = disc_resp.json()
            discussion = disc_data.get("changeset", {}).get("discussion", [])

            for idx, comment in enumerate(discussion):
                key = (cs_id, idx)
                if key in self._seen_comments:
                    continue
                self._seen_comments.add(key)

                # Only include comments newer than last_check
                comment_date = comment.get("date", "")
                if comment_date <= self.last_check:
                    continue

                new_comments.append(
                    ChangesetComment(
                        changeset_id=cs_id,
                        comment_id=idx,
                        user=comment.get("user", "anonymous"),
                        text=comment.get("text", ""),
                        date=comment_date,
                        changeset_user=cs_user,
                        changeset_bbox=cs_bbox,
                    )
                )

        self.last_check = now.isoformat()
        self.last_processed_at = self.last_check

        # Prune old seen comments (keep last 10000)
        if len(self._seen_comments) > 10000:
            self._seen_comments = set(
                sorted(self._seen_comments, key=lambda x: x[1])[-5000:]
            )

        return new_comments


def comment_matches(
    f: ChangesetCommentFilter, comment: ChangesetComment
) -> bool:
    """Check if a changeset comment matches a filter."""
    if f.osm_user is not None:
        if comment.changeset_user != f.osm_user:
            return False
    if f.comment_text is not None:
        if f.comment_text.lower() not in comment.text.lower():
            return False
    if f.bbox is not None and comment.changeset_bbox is not None:
        s, w, n, e = f.bbox
        cs, cw, cn, ce = comment.changeset_bbox
        # Check if changeset bbox intersects watch bbox
        if cn < s or cs > n or ce < w or cw > e:
            return False
    return True
