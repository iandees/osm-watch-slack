from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

from .evaluator import DiffElement

logger = logging.getLogger(__name__)


def _extract_tags(element: ET.Element) -> dict[str, str]:
    """Extract all <tag k="..." v="..."/> children into a dict."""
    return {
        tag.attrib["k"]: tag.attrib["v"]
        for tag in element.findall("tag")
        if "k" in tag.attrib and "v" in tag.attrib
    }


def _extract_coords(element: ET.Element) -> tuple[float | None, float | None]:
    """Extract lat/lon from an element.

    For nodes, reads lat/lon attributes directly.
    For ways/relations, checks for <bounds> or <center> child elements.
    """
    if "lat" in element.attrib and "lon" in element.attrib:
        return float(element.attrib["lat"]), float(element.attrib["lon"])

    # Check for <center> element (ways/relations)
    center = element.find("center")
    if center is not None and "lat" in center.attrib and "lon" in center.attrib:
        return float(center.attrib["lat"]), float(center.attrib["lon"])

    # Check for <bounds> element and compute center
    bounds = element.find("bounds")
    if bounds is not None:
        attrs = bounds.attrib
        if all(k in attrs for k in ("minlat", "minlon", "maxlat", "maxlon")):
            lat = (float(attrs["minlat"]) + float(attrs["maxlat"])) / 2
            lon = (float(attrs["minlon"]) + float(attrs["maxlon"])) / 2
            return lat, lon

    return None, None


def _parse_osm_element(
    element: ET.Element,
) -> tuple[str, int, int, str, dict[str, str], float | None, float | None, int | None]:
    """Parse common attributes from a node/way/relation element."""
    element_type = element.tag
    element_id = int(element.attrib["id"])
    changeset_id = int(element.attrib.get("changeset", "0"))
    user = element.attrib.get("user", "")
    uid_str = element.attrib.get("uid")
    uid = int(uid_str) if uid_str is not None else None
    tags = _extract_tags(element)
    lat, lon = _extract_coords(element)
    return element_type, element_id, changeset_id, user, tags, lat, lon, uid


def _find_osm_element(parent: ET.Element) -> ET.Element | None:
    """Find the first node/way/relation child element."""
    for tag_name in ("node", "way", "relation"):
        elem = parent.find(tag_name)
        if elem is not None:
            return elem
    return None


def parse_augmented_diff(xml_bytes: bytes) -> list[DiffElement]:
    """Parse Overpass augmented diff XML into DiffElement instances."""
    root = ET.fromstring(xml_bytes)
    elements: list[DiffElement] = []

    for action in root.findall("action"):
        action_type = action.attrib.get("type", "")

        if action_type == "create":
            osm_elem = _find_osm_element(action)
            if osm_elem is None:
                continue
            etype, eid, cid, user, tags, lat, lon, uid = _parse_osm_element(osm_elem)
            elements.append(DiffElement(
                action="create",
                element_type=etype,
                element_id=eid,
                changeset_id=cid,
                user=user,
                old_tags={},
                new_tags=tags,
                lat=lat,
                lon=lon,
                uid=uid,
            ))

        elif action_type == "modify":
            old_wrapper = action.find("old")
            new_wrapper = action.find("new")
            if old_wrapper is None or new_wrapper is None:
                continue
            old_elem = _find_osm_element(old_wrapper)
            new_elem = _find_osm_element(new_wrapper)
            if old_elem is None or new_elem is None:
                continue

            _, _, _, _, old_tags, _, _, _ = _parse_osm_element(old_elem)
            etype, eid, cid, user, new_tags, lat, lon, uid = _parse_osm_element(new_elem)
            elements.append(DiffElement(
                action="modify",
                element_type=etype,
                element_id=eid,
                changeset_id=cid,
                user=user,
                old_tags=old_tags,
                new_tags=new_tags,
                lat=lat,
                lon=lon,
                uid=uid,
            ))

        elif action_type == "delete":
            old_wrapper = action.find("old")
            if old_wrapper is None:
                continue
            old_elem = _find_osm_element(old_wrapper)
            if old_elem is None:
                continue

            etype, eid, cid, user, old_tags, lat, lon, uid = _parse_osm_element(old_elem)
            elements.append(DiffElement(
                action="delete",
                element_type=etype,
                element_id=eid,
                changeset_id=cid,
                user=user,
                old_tags=old_tags,
                new_tags={},
                lat=lat,
                lon=lon,
                uid=uid,
            ))

    return elements


class DiffConsumer:
    """Fetches and processes OSM minutely augmented diffs."""

    def __init__(
        self,
        state_path: str,
        http_client: httpx.AsyncClient,
        base_url: str = "https://overpass-api.de",
        replication_url: str = "https://planet.openstreetmap.org/replication/minute/state.txt",
    ) -> None:
        self.state_path = Path(state_path)
        self.http_client = http_client
        self.base_url = base_url.rstrip("/")
        self.replication_url = replication_url

    async def get_sequence(self) -> int:
        """Read sequence number from state file, or fetch current from replication URL."""
        if self.state_path.exists():
            text = self.state_path.read_text().strip()
            return int(text)

        resp = await self.http_client.get(self.replication_url)
        resp.raise_for_status()
        for line in resp.text.splitlines():
            line = line.strip()
            if line.startswith("sequenceNumber="):
                return int(line.split("=", 1)[1])
        raise ValueError("sequenceNumber not found in replication state")

    async def fetch_diff(self, sequence: int) -> list[DiffElement]:
        """Fetch augmented diff for a sequence number, with retry logic."""
        url = f"{self.base_url}/api/augmented_diff?id={sequence}"
        max_retries = 3
        backoff = 30

        for attempt in range(max_retries):
            try:
                resp = await self.http_client.get(url, timeout=120)

                if resp.status_code == 404:
                    raise _NotYetAvailable(sequence)

                if resp.status_code == 429 or resp.status_code >= 500:
                    wait = min(backoff * (2 ** attempt), 300)
                    logger.warning(
                        "Server returned %d for sequence %d, retrying in %ds",
                        resp.status_code, sequence, wait,
                    )
                    await asyncio.sleep(wait)
                    continue

                resp.raise_for_status()
                return parse_augmented_diff(resp.content)

            except httpx.HTTPError as exc:
                if attempt < max_retries - 1:
                    wait = min(backoff * (2 ** attempt), 300)
                    logger.warning(
                        "HTTP error fetching sequence %d: %s, retrying in %ds",
                        sequence, exc, wait,
                    )
                    await asyncio.sleep(wait)
                else:
                    raise

        return []  # unreachable but satisfies type checker

    async def save_state(self, sequence: int) -> None:
        """Write sequence number to the state file."""
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(str(sequence))

    async def run(self, callback) -> None:
        """Main loop: fetch diffs, process, save state, repeat.

        Args:
            callback: an async callable that receives a list[DiffElement].
        """
        sequence = await self.get_sequence()
        error_backoff = 30

        while True:
            try:
                elements = await self.fetch_diff(sequence)
                error_backoff = 30  # reset on success

                if elements:
                    await callback(elements)

                await self.save_state(sequence)
                sequence += 1
                await asyncio.sleep(60)

            except _NotYetAvailable:
                logger.info("Sequence %d not yet available, waiting 30s", sequence)
                await asyncio.sleep(30)

            except Exception:
                logger.warning(
                    "Error processing sequence %d, retrying in %ds",
                    sequence, error_backoff,
                    exc_info=True,
                )
                await asyncio.sleep(error_backoff)
                error_backoff = min(error_backoff * 2, 300)


class _NotYetAvailable(Exception):
    """Raised when a diff sequence is not yet available (404)."""

    def __init__(self, sequence: int) -> None:
        self.sequence = sequence
        super().__init__(f"Sequence {sequence} not yet available")
