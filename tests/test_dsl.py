from __future__ import annotations

from datetime import timedelta

import pytest

from osm_watch_slack.dsl import (
    ParseError,
    TagFilter,
    WatchFilter,
    parse,
    parse_expires,
    split_command,
    to_dsl,
)


class TestParse:
    def test_relation_with_id_and_tag(self):
        f = parse("relation(12345)[name]")
        assert f.element_type == "relation"
        assert f.element_id == 12345
        assert f.tags == (TagFilter("name", None),)
        assert f.state is None
        assert f.bbox is None

    def test_node_full(self):
        f = parse("node[amenity=hospital](new)(bbox:40.7,-74.0,40.8,-73.9)")
        assert f.element_type == "node"
        assert f.element_id is None
        assert f.tags == (TagFilter("amenity", "hospital"),)
        assert f.state == "new"
        assert f.bbox == (40.7, -74.0, 40.8, -73.9)

    def test_way_deleted(self):
        f = parse("way[highway](deleted)")
        assert f.element_type == "way"
        assert f.tags == (TagFilter("highway", None),)
        assert f.state == "deleted"

    def test_multiple_tags(self):
        f = parse("node[amenity=restaurant][cuisine=pizza]")
        assert len(f.tags) == 2
        assert f.tags[0] == TagFilter("amenity", "restaurant")
        assert f.tags[1] == TagFilter("cuisine", "pizza")

    def test_id_only(self):
        f = parse("node(999)")
        assert f.element_id == 999

    def test_bbox_only(self):
        f = parse("node(bbox:1.0,2.0,3.0,4.0)")
        assert f.bbox == (1.0, 2.0, 3.0, 4.0)

    def test_state_before_bbox(self):
        f = parse("node[name](changed)(bbox:1,2,3,4)")
        assert f.state == "changed"
        assert f.bbox == (1.0, 2.0, 3.0, 4.0)

    def test_bbox_before_state(self):
        f = parse("node[name](bbox:1,2,3,4)(changed)")
        assert f.state == "changed"
        assert f.bbox == (1.0, 2.0, 3.0, 4.0)


class TestParseErrors:
    def test_empty(self):
        with pytest.raises(ParseError, match="Empty"):
            parse("")

    def test_invalid_type(self):
        with pytest.raises(ParseError, match="element type"):
            parse("building[name]")

    def test_no_filter(self):
        with pytest.raises(ParseError, match="At least one"):
            parse("node")

    def test_no_filter_with_state_only(self):
        with pytest.raises(ParseError, match="At least one"):
            parse("node(new)")

    def test_bad_bbox_values(self):
        with pytest.raises(ParseError, match="numbers"):
            parse("node(bbox:a,b,c,d)")

    def test_bad_bbox_count(self):
        with pytest.raises(ParseError, match="4 values"):
            parse("node(bbox:1,2,3)")

    def test_duplicate_state(self):
        with pytest.raises(ParseError, match="Duplicate state"):
            parse("node[name](new)(changed)")

    def test_duplicate_bbox(self):
        with pytest.raises(ParseError, match="Duplicate bbox"):
            parse("node[name](bbox:1,2,3,4)(bbox:5,6,7,8)")

    def test_unknown_clause(self):
        with pytest.raises(ParseError, match="Unknown"):
            parse("node[name](foobar)")

    def test_unclosed_tag(self):
        with pytest.raises(ParseError, match="']'"):
            parse("node[name")

    def test_empty_tag_key(self):
        with pytest.raises(ParseError, match="Empty tag key"):
            parse("node[](new)")


class TestRoundTrip:
    def test_simple(self):
        text = "relation(12345)[name]"
        assert to_dsl(parse(text)) == text

    def test_full(self):
        text = "node[amenity=hospital](new)(bbox:40.7,-74.0,40.8,-73.9)"
        assert to_dsl(parse(text)) == text

    def test_way_deleted(self):
        text = "way[highway](deleted)"
        assert to_dsl(parse(text)) == text


class TestSerialization:
    def test_to_dict_from_dict(self):
        f = parse("node[amenity=hospital](new)(bbox:40.7,-74.0,40.8,-73.9)")
        d = f.to_dict()
        f2 = WatchFilter.from_dict(d)
        assert f.element_type == f2.element_type
        assert f.element_id == f2.element_id
        assert f.tags == f2.tags
        assert f.state == f2.state
        assert f.bbox is not None and f2.bbox is not None
        for a, b in zip(f.bbox, f2.bbox):
            assert abs(a - b) < 1e-9


class TestParseExpires:
    def test_minutes(self):
        assert parse_expires("expires:30m") == timedelta(minutes=30)

    def test_hours(self):
        assert parse_expires("expires:12h") == timedelta(hours=12)

    def test_days(self):
        assert parse_expires("expires:3d") == timedelta(days=3)

    def test_weeks(self):
        assert parse_expires("expires:2w") == timedelta(weeks=2)

    def test_max_exceeded(self):
        with pytest.raises(ParseError, match="180 days"):
            parse_expires("expires:200d")

    def test_invalid_format(self):
        with pytest.raises(ParseError, match="Invalid expires"):
            parse_expires("expires:abc")


class TestSplitCommand:
    def test_dsl_only(self):
        dsl, expires = split_command("relation(12345)[name]")
        assert dsl == "relation(12345)[name]"
        assert expires == timedelta(weeks=1)

    def test_with_expires(self):
        dsl, expires = split_command("relation(12345)[name] expires:3d")
        assert dsl == "relation(12345)[name]"
        assert expires == timedelta(days=3)
