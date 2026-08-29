from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .dsl import WatchFilter


@dataclass
class DiffElement:
    action: str  # "create", "modify", "delete"
    element_type: str  # "node", "way", "relation"
    element_id: int
    changeset_id: int
    user: str
    old_tags: dict[str, str] = field(default_factory=dict)
    new_tags: dict[str, str] = field(default_factory=dict)
    lat: float | None = None
    lon: float | None = None
    uid: int | None = None
    user_created_at: str | None = None


STATE_MAP = {"new": "create", "changed": "modify", "deleted": "delete"}


def matches(watch: WatchFilter, element: DiffElement) -> bool:
    # nwr matches any element type; otherwise require exact match
    if watch.element_type != "nwr" and element.element_type != watch.element_type:
        return False

    if watch.element_id is not None and element.element_id != watch.element_id:
        return False

    if watch.state is not None:
        expected_action = STATE_MAP.get(watch.state)
        if element.action != expected_action:
            return False

    for tag in watch.tags:
        if not _tag_matches(tag.key, tag.value, element):
            return False

    if watch.bbox is not None:
        if not _bbox_matches(watch.bbox, element):
            return False

    if watch.osm_user is not None:
        if element.user != watch.osm_user:
            return False

    if watch.osm_uid is not None:
        if element.uid != watch.osm_uid:
            return False

    if watch.user_age_max is not None:
        if not _user_age_matches(watch.user_age_max, element):
            return False

    return True


def _user_age_matches(max_age: timedelta, element: DiffElement) -> bool:
    if element.user_created_at is None:
        return False
    created = datetime.fromisoformat(element.user_created_at.replace("Z", "+00:00"))
    age = datetime.now(timezone.utc) - created
    return age < max_age


def _tag_matches(key: str, value: str | None, element: DiffElement) -> bool:
    old_val = element.old_tags.get(key)
    new_val = element.new_tags.get(key)

    if value is None:
        # [key] — tag must have been added, removed, or changed
        has_old = key in element.old_tags
        has_new = key in element.new_tags
        if not has_old and not has_new:
            return False
        if has_old and has_new and old_val == new_val:
            return False
        return True
    else:
        # [key=value] — new state has it OR old state had it (element stops matching)
        return new_val == value or old_val == value


def _bbox_matches(
    bbox: tuple[float, float, float, float], element: DiffElement
) -> bool:
    if element.lat is None or element.lon is None:
        return False
    south, west, north, east = bbox
    return south <= element.lat <= north and west <= element.lon <= east
