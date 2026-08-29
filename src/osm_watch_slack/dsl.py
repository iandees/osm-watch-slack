from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta


class ParseError(ValueError):
    pass


@dataclass(frozen=True)
class TagFilter:
    key: str
    value: str | None = None

    def __str__(self) -> str:
        if self.value is not None:
            return f"[{self.key}={self.value}]"
        return f"[{self.key}]"


@dataclass(frozen=True)
class WatchFilter:
    element_type: str
    element_id: int | None = None
    tags: tuple[TagFilter, ...] = ()
    state: str | None = None
    bbox: tuple[float, float, float, float] | None = None
    osm_user: str | None = None
    osm_uid: int | None = None
    user_age_max: timedelta | None = None

    def to_dict(self) -> dict:
        d: dict = {"element_type": self.element_type}
        if self.element_id is not None:
            d["element_id"] = self.element_id
        if self.tags:
            d["tags"] = [{"key": t.key, "value": t.value} for t in self.tags]
        if self.state is not None:
            d["state"] = self.state
        if self.bbox is not None:
            d["bbox"] = list(self.bbox)
        if self.osm_user is not None:
            d["osm_user"] = self.osm_user
        if self.osm_uid is not None:
            d["osm_uid"] = self.osm_uid
        if self.user_age_max is not None:
            d["user_age_max_seconds"] = int(self.user_age_max.total_seconds())
        return d

    @classmethod
    def from_dict(cls, d: dict) -> WatchFilter:
        tags = tuple(TagFilter(t["key"], t.get("value")) for t in d.get("tags", []))
        bbox = tuple(d["bbox"]) if d.get("bbox") else None
        user_age_max = None
        if d.get("user_age_max_seconds") is not None:
            user_age_max = timedelta(seconds=d["user_age_max_seconds"])
        return cls(
            element_type=d["element_type"],
            element_id=d.get("element_id"),
            tags=tags,
            state=d.get("state"),
            bbox=bbox,
            osm_user=d.get("osm_user"),
            osm_uid=d.get("osm_uid"),
            user_age_max=user_age_max,
        )


VALID_TYPES = {"node", "way", "relation", "nwr"}
VALID_STATES = {"new", "changed", "deleted"}

DURATION_UNITS = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
_USER_AGE_RE = re.compile(r"<(\d+)([dwm])")
MAX_EXPIRES = timedelta(days=180)
DEFAULT_EXPIRES = timedelta(weeks=1)


def parse(text: str) -> WatchFilter:
    text = text.strip()
    if not text:
        raise ParseError("Empty filter")

    pos = 0

    def peek() -> str | None:
        return text[pos] if pos < len(text) else None

    def advance(n: int = 1) -> str:
        nonlocal pos
        result = text[pos : pos + n]
        pos += n
        return result

    def skip_ws():
        nonlocal pos
        while pos < len(text) and text[pos] == " ":
            pos += 1

    # Parse element type
    element_type = None
    for t in sorted(VALID_TYPES, key=len, reverse=True):
        if text[pos:].startswith(t):
            element_type = t
            advance(len(t))
            break
    if element_type is None:
        raise ParseError(
            f"Expected element type (node/way/relation/nwr), got: {text[pos:pos+20]!r}"
        )

    # Parse optional element ID: (12345)
    # Only treat as ID if the char after '(' is a digit
    element_id = None
    if peek() == "(" and pos + 1 < len(text) and text[pos + 1].isdigit():
        advance()
        id_start = pos
        while peek() and peek().isdigit():
            advance()
        element_id = int(text[id_start:pos])
        if peek() != ")":
            raise ParseError("Expected ')' after element ID")
        advance()

    # Parse tag filters: [key] or [key=value]
    tags: list[TagFilter] = []
    while peek() == "[":
        advance()
        key_start = pos
        while peek() and peek() not in ("=", "]"):
            advance()
        key = text[key_start:pos].strip()
        if not key:
            raise ParseError("Empty tag key in filter")

        value = None
        if peek() == "=":
            advance()
            val_start = pos
            while peek() and peek() != "]":
                advance()
            value = text[val_start:pos].strip()

        if peek() != "]":
            raise ParseError("Expected ']' to close tag filter")
        advance()
        tags.append(TagFilter(key, value))

    # Parse optional parenthesized clauses (order-independent)
    state = None
    bbox = None
    osm_user = None
    osm_uid = None
    user_age_max = None

    while peek() == "(":
        advance()
        inner_start = pos
        depth = 1
        while peek() and depth > 0:
            if peek() == "(":
                depth += 1
            elif peek() == ")":
                depth -= 1
            if depth > 0:
                advance()
        inner = text[inner_start:pos]
        if peek() != ")":
            raise ParseError("Unclosed parenthesis")
        advance()

        if inner in VALID_STATES:
            if state is not None:
                raise ParseError("Duplicate state filter")
            state = inner
        elif inner.startswith("bbox:"):
            if bbox is not None:
                raise ParseError("Duplicate bbox filter")
            bbox_str = inner[5:]
            parts = bbox_str.split(",")
            if len(parts) != 4:
                raise ParseError("bbox requires 4 values: south,west,north,east")
            try:
                bbox = tuple(float(p.strip()) for p in parts)
            except ValueError:
                raise ParseError("bbox values must be numbers")
        elif inner.startswith("user:"):
            if osm_user is not None:
                raise ParseError("Duplicate user filter")
            username = inner[5:]
            if not username:
                raise ParseError("Empty user name in user filter")
            osm_user = username
        elif inner.startswith("uid:"):
            if osm_uid is not None:
                raise ParseError("Duplicate uid filter")
            uid_str = inner[4:]
            try:
                osm_uid = int(uid_str)
            except ValueError:
                raise ParseError(f"Invalid uid value: {uid_str!r}")
        elif inner.startswith("user_age:"):
            if user_age_max is not None:
                raise ParseError("Duplicate user_age filter")
            age_str = inner[9:]
            m = _USER_AGE_RE.fullmatch(age_str)
            if not m:
                raise ParseError(
                    "Invalid user_age format. Use (user_age:<N<unit>) "
                    "where unit is d(ays), w(eeks), or m(onths). Example: (user_age:<30d)"
                )
            amount = int(m.group(1))
            unit = m.group(2)
            if unit == "d":
                user_age_max = timedelta(days=amount)
            elif unit == "w":
                user_age_max = timedelta(weeks=amount)
            elif unit == "m":
                user_age_max = timedelta(days=amount * 30)
        else:
            raise ParseError(f"Unknown parenthesized clause: ({inner})")

    skip_ws()
    if pos < len(text):
        raise ParseError(f"Unexpected trailing text: {text[pos:]!r}")

    # Validate constraint: at least one filter required
    if (
        element_id is None
        and not tags
        and bbox is None
        and osm_user is None
        and osm_uid is None
        and user_age_max is None
    ):
        raise ParseError(
            "At least one of: element ID, tag filter, bbox, user, uid, or user_age is required. "
            "Fully unfiltered watches are not allowed."
        )

    return WatchFilter(
        element_type=element_type,
        element_id=element_id,
        tags=tuple(tags),
        state=state,
        bbox=bbox,
        osm_user=osm_user,
        osm_uid=osm_uid,
        user_age_max=user_age_max,
    )


def _format_user_age(td: timedelta) -> str:
    """Format a timedelta as the most natural user_age DSL string."""
    total_days = td.days
    if total_days > 0 and total_days % 7 == 0 and (total_days // 7) * 7 == total_days:
        weeks = total_days // 7
        if total_days % 30 != 0:
            return f"<{weeks}w"
    if total_days > 0 and total_days % 30 == 0:
        months = total_days // 30
        return f"<{months}m"
    return f"<{total_days}d"


def to_dsl(f: WatchFilter) -> str:
    parts = [f.element_type]
    if f.element_id is not None:
        parts.append(f"({f.element_id})")
    for tag in f.tags:
        parts.append(str(tag))
    if f.state is not None:
        parts.append(f"({f.state})")
    if f.bbox is not None:
        s, w, n, e = f.bbox
        parts.append(f"(bbox:{s},{w},{n},{e})")
    if f.osm_user is not None:
        parts.append(f"(user:{f.osm_user})")
    if f.osm_uid is not None:
        parts.append(f"(uid:{f.osm_uid})")
    if f.user_age_max is not None:
        parts.append(f"(user_age:{_format_user_age(f.user_age_max)})")
    return "".join(parts)


_EXPIRES_RE = re.compile(r"expires:(\d+)([mhdw])")


def parse_expires(text: str) -> timedelta:
    m = _EXPIRES_RE.fullmatch(text.strip())
    if not m:
        raise ParseError(
            "Invalid expires format. Use expires:<number><unit> "
            "where unit is m(inutes), h(ours), d(ays), or w(eeks). Example: expires:3d"
        )
    amount = int(m.group(1))
    unit = DURATION_UNITS[m.group(2)]
    delta = timedelta(**{unit: amount})
    if delta > MAX_EXPIRES:
        raise ParseError(f"Maximum watch duration is 180 days, got {delta.days} days")
    if delta <= timedelta(0):
        raise ParseError("Watch duration must be positive")
    return delta


def split_command(text: str) -> tuple[str, timedelta]:
    """Split a slash command argument into DSL filter text and optional expires duration."""
    parts = text.strip().split()
    expires = DEFAULT_EXPIRES
    filter_parts = []
    for part in parts:
        if part.startswith("expires:"):
            expires = parse_expires(part)
        else:
            filter_parts.append(part)
    return " ".join(filter_parts), expires
