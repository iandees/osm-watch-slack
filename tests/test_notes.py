from __future__ import annotations

import httpx

from osm_watch_slack.dsl import WatchFilter
from osm_watch_slack.notes import Note, NoteConsumer, _note_matches


def _make_note(
    *,
    note_id: int = 1000,
    lat: float = 40.75,
    lon: float = -73.95,
    user: str | None = "mapper1",
    text: str = "This building is missing",
) -> Note:
    return Note(
        id=note_id,
        lat=lat,
        lon=lon,
        user=user,
        created_at="2025-06-10T12:00:00Z",
        text=text,
        url=f"https://www.openstreetmap.org/note/{note_id}",
    )


class TestNoteMatches:
    def test_bbox_inside(self) -> None:
        watch = WatchFilter(
            element_type="note",
            bbox=(40.0, -74.0, 41.0, -73.0),
        )
        note = _make_note(lat=40.5, lon=-73.5)
        assert _note_matches(watch, note) is True

    def test_bbox_outside(self) -> None:
        watch = WatchFilter(
            element_type="note",
            bbox=(40.0, -74.0, 41.0, -73.0),
        )
        note = _make_note(lat=42.0, lon=-73.5)
        assert _note_matches(watch, note) is False

    def test_bbox_on_boundary(self) -> None:
        watch = WatchFilter(
            element_type="note",
            bbox=(40.0, -74.0, 41.0, -73.0),
        )
        note = _make_note(lat=40.0, lon=-74.0)
        assert _note_matches(watch, note) is True

    def test_user_matches(self) -> None:
        watch = WatchFilter(
            element_type="note",
            osm_user="mapper1",
        )
        note = _make_note(user="mapper1")
        assert _note_matches(watch, note) is True

    def test_user_does_not_match(self) -> None:
        watch = WatchFilter(
            element_type="note",
            osm_user="mapper1",
        )
        note = _make_note(user="mapper2")
        assert _note_matches(watch, note) is False

    def test_user_filter_rejects_anonymous(self) -> None:
        watch = WatchFilter(
            element_type="note",
            osm_user="mapper1",
        )
        note = _make_note(user=None)
        assert _note_matches(watch, note) is False

    def test_bbox_and_user_both_match(self) -> None:
        watch = WatchFilter(
            element_type="note",
            bbox=(40.0, -74.0, 41.0, -73.0),
            osm_user="mapper1",
        )
        note = _make_note(lat=40.5, lon=-73.5, user="mapper1")
        assert _note_matches(watch, note) is True

    def test_bbox_matches_but_user_does_not(self) -> None:
        watch = WatchFilter(
            element_type="note",
            bbox=(40.0, -74.0, 41.0, -73.0),
            osm_user="mapper1",
        )
        note = _make_note(lat=40.5, lon=-73.5, user="mapper2")
        assert _note_matches(watch, note) is False

    def test_user_matches_but_bbox_does_not(self) -> None:
        watch = WatchFilter(
            element_type="note",
            bbox=(40.0, -74.0, 41.0, -73.0),
            osm_user="mapper1",
        )
        note = _make_note(lat=42.0, lon=-73.5, user="mapper1")
        assert _note_matches(watch, note) is False

    def test_no_filters_matches_everything(self) -> None:
        # A watch with only element_type set (no bbox, no user) matches any note
        watch = WatchFilter(element_type="note")
        note = _make_note()
        assert _note_matches(watch, note) is True


SAMPLE_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [-73.95, 40.75],
            },
            "properties": {
                "id": 5001,
                "date_created": "2025-06-10T12:00:00Z",
                "comments": [
                    {
                        "user": "mapper1",
                        "text": "Missing building here",
                    }
                ],
            },
        },
        {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [-73.90, 40.80],
            },
            "properties": {
                "id": 5002,
                "date_created": "2025-06-10T13:00:00Z",
                "comments": [
                    {
                        "text": "Anonymous note text",
                    }
                ],
            },
        },
    ],
}


class TestFetchNotes:
    async def test_fetch_notes_parses_geojson(self) -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=SAMPLE_GEOJSON)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            consumer = NoteConsumer(client)
            notes = await consumer.fetch_notes()

        assert len(notes) == 2

        assert notes[0].id == 5001
        assert notes[0].lat == 40.75
        assert notes[0].lon == -73.95
        assert notes[0].user == "mapper1"
        assert notes[0].text == "Missing building here"
        assert notes[0].url == "https://www.openstreetmap.org/note/5001"

        assert notes[1].id == 5002
        assert notes[1].user is None  # anonymous
        assert notes[1].text == "Anonymous note text"

    async def test_fetch_notes_with_bbox(self) -> None:
        captured_params: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured_params.update(dict(request.url.params))
            return httpx.Response(200, json={"type": "FeatureCollection", "features": []})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            consumer = NoteConsumer(client)
            await consumer.fetch_notes(bbox=(40.7, -74.0, 40.8, -73.9))

        # API bbox format is west,south,east,north
        assert captured_params["bbox"] == "-74.0,40.7,-73.9,40.8"


class TestPoll:
    async def test_poll_returns_only_new_notes(self) -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=SAMPLE_GEOJSON)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            consumer = NoteConsumer(client)
            # Set last_note_id so only note 5002 is "new"
            consumer.last_note_id = 5001

            watches = [
                (1, "C123", "note(bbox:40.0,-74.5,41.0,-73.5)",
                 WatchFilter(element_type="note", bbox=(40.0, -74.5, 41.0, -73.5))),
            ]
            matches = await consumer.poll(watches)

        assert len(matches) == 1
        note, wid, cid, ft = matches[0]
        assert note.id == 5002
        assert wid == 1
        assert cid == "C123"

    async def test_poll_updates_last_note_id(self) -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=SAMPLE_GEOJSON)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            consumer = NoteConsumer(client)
            assert consumer.last_note_id == 0

            watches = [
                (1, "C123", "note(bbox:40.0,-74.5,41.0,-73.5)",
                 WatchFilter(element_type="note", bbox=(40.0, -74.5, 41.0, -73.5))),
            ]
            await consumer.poll(watches)

        assert consumer.last_note_id == 5002

    async def test_poll_no_new_notes(self) -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=SAMPLE_GEOJSON)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            consumer = NoteConsumer(client)
            # All notes are already seen
            consumer.last_note_id = 9999

            watches = [
                (1, "C123", "note(bbox:40.0,-74.5,41.0,-73.5)",
                 WatchFilter(element_type="note", bbox=(40.0, -74.5, 41.0, -73.5))),
            ]
            matches = await consumer.poll(watches)

        assert len(matches) == 0

    async def test_poll_filters_by_user(self) -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=SAMPLE_GEOJSON)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            consumer = NoteConsumer(client)

            watches = [
                (1, "C123", "note(user:mapper1)",
                 WatchFilter(element_type="note", osm_user="mapper1")),
            ]
            matches = await consumer.poll(watches)

        # Only note 5001 has user "mapper1"
        assert len(matches) == 1
        assert matches[0][0].id == 5001
