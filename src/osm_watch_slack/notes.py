from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass

import httpx

from .dsl import WatchFilter

logger = logging.getLogger(__name__)


@dataclass
class Note:
    id: int
    lat: float
    lon: float
    user: str | None  # anonymous notes have no user
    created_at: str
    text: str  # the opening comment text
    url: str  # https://www.openstreetmap.org/note/{id}


class NoteConsumer:
    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self.http_client = http_client
        self.last_note_id: int = 0  # track highest seen note ID
        self.last_processed_at: str | None = None

    async def fetch_notes(
        self, bbox: tuple[float, float, float, float] | None = None
    ) -> list[Note]:
        """Fetch recent open notes, optionally filtered by bbox."""
        params: dict[str, str] = {
            "sort": "created_at",
            "closed": "0",
            "limit": "100",
        }
        if bbox:
            s, w, n, e = bbox
            params["bbox"] = f"{w},{s},{e},{n}"  # API uses west,south,east,north
        resp = await self.http_client.get(
            "https://api.openstreetmap.org/api/0.6/notes.json",
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        notes: list[Note] = []
        for feature in data.get("features", []):
            props = feature["properties"]
            comments = props.get("comments", [])
            first_comment = comments[0] if comments else {}
            geom = feature["geometry"]["coordinates"]  # [lon, lat]
            notes.append(
                Note(
                    id=props["id"],
                    lat=geom[1],
                    lon=geom[0],
                    user=first_comment.get("user"),
                    created_at=props.get("date_created", ""),
                    text=first_comment.get("text", ""),
                    url=f"https://www.openstreetmap.org/note/{props['id']}",
                )
            )
        return notes

    async def poll(
        self,
        watches: list[tuple[int, str, str, WatchFilter]],
    ) -> list[tuple[Note, int, str, str]]:
        """Check for new notes matching any note watches.

        Args:
            watches: list of (watch_id, channel_id, filter_text, WatchFilter) tuples.

        Returns:
            list of (note, watch_id, channel_id, filter_text) tuples for notifications.
        """
        # Collect all unique bboxes from note watches
        bboxes: set[tuple[float, float, float, float] | None] = set()
        for _wid, _cid, _ft, wf in watches:
            bboxes.add(wf.bbox)

        all_notes: dict[int, Note] = {}
        for bbox in bboxes:
            notes = await self.fetch_notes(bbox)
            for note in notes:
                if note.id > self.last_note_id:
                    all_notes[note.id] = note

        if not all_notes:
            return []

        # Update last_note_id
        max_id = max(all_notes.keys())
        if max_id > self.last_note_id:
            self.last_note_id = max_id

        self.last_processed_at = datetime.datetime.now(datetime.UTC).isoformat()

        # Match notes against watches
        matches: list[tuple[Note, int, str, str]] = []
        for note in all_notes.values():
            for wid, cid, ft, wf in watches:
                if _note_matches(wf, note):
                    matches.append((note, wid, cid, ft))

        return matches


def _note_matches(watch: WatchFilter, note: Note) -> bool:
    """Check if a note matches a note watch filter."""
    if watch.bbox is not None:
        s, w, n, e = watch.bbox
        if not (s <= note.lat <= n and w <= note.lon <= e):
            return False
    if watch.osm_user is not None:
        if note.user is None or note.user != watch.osm_user:
            return False
    return True
