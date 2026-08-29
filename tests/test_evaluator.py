from __future__ import annotations

from osm_watch_slack.dsl import parse
from osm_watch_slack.evaluator import DiffElement, matches


class TestTypeMatching:
    def test_matching_type(self, sample_node_create):
        w = parse("node[amenity=hospital]")
        assert matches(w, sample_node_create)

    def test_mismatching_type(self, sample_node_create):
        w = parse("way[amenity=hospital]")
        assert not matches(w, sample_node_create)


class TestIdMatching:
    def test_matching_id(self, sample_relation):
        w = parse("relation(12345)[name]")
        assert matches(w, sample_relation)

    def test_mismatching_id(self, sample_relation):
        w = parse("relation(99999)[name]")
        assert not matches(w, sample_relation)


class TestStateMatching:
    def test_new_matches_create(self, sample_node_create):
        w = parse("node[amenity=hospital](new)")
        assert matches(w, sample_node_create)

    def test_new_does_not_match_modify(self, sample_node_modify):
        w = parse("node[name](new)")
        assert not matches(w, sample_node_modify)

    def test_changed_matches_modify(self, sample_node_modify):
        w = parse("node[name](changed)")
        assert matches(w, sample_node_modify)

    def test_deleted_matches_delete(self, sample_way_delete):
        w = parse("way[highway](deleted)")
        assert matches(w, sample_way_delete)

    def test_no_state_matches_any(self, sample_node_create):
        w = parse("node[amenity=hospital]")
        assert matches(w, sample_node_create)


class TestTagExistenceFilter:
    def test_tag_added(self):
        elem = DiffElement(
            action="modify", element_type="node", element_id=1,
            changeset_id=1, user="u",
            old_tags={}, new_tags={"name": "Foo"},
        )
        w = parse("node(1)[name]")
        assert matches(w, elem)

    def test_tag_removed(self):
        elem = DiffElement(
            action="modify", element_type="node", element_id=1,
            changeset_id=1, user="u",
            old_tags={"name": "Foo"}, new_tags={},
        )
        w = parse("node(1)[name]")
        assert matches(w, elem)

    def test_tag_value_changed(self, sample_node_modify):
        w = parse("node(200)[name]")
        assert matches(w, sample_node_modify)

    def test_tag_unchanged(self, sample_node_modify):
        # amenity=cafe in both old and new — no change
        w = parse("node(200)[amenity]")
        assert not matches(w, sample_node_modify)

    def test_tag_absent(self, sample_node_modify):
        w = parse("node(200)[highway]")
        assert not matches(w, sample_node_modify)


class TestTagValueFilter:
    def test_new_state_has_value(self, sample_node_create):
        w = parse("node[amenity=hospital]")
        assert matches(w, sample_node_create)

    def test_old_state_had_value(self):
        elem = DiffElement(
            action="modify", element_type="node", element_id=1,
            changeset_id=1, user="u",
            old_tags={"amenity": "hospital"}, new_tags={"amenity": "clinic"},
        )
        w = parse("node(1)[amenity=hospital]")
        assert matches(w, elem)

    def test_neither_has_value(self, sample_node_modify):
        w = parse("node(200)[amenity=hospital]")
        assert not matches(w, sample_node_modify)


class TestBboxMatching:
    def test_inside_bbox(self, sample_node_create):
        w = parse("node[amenity=hospital](bbox:40.7,-74.0,40.8,-73.9)")
        assert matches(w, sample_node_create)

    def test_outside_bbox(self, sample_node_create):
        w = parse("node[amenity=hospital](bbox:51.0,-1.0,52.0,0.0)")
        assert not matches(w, sample_node_create)

    def test_no_coordinates(self):
        elem = DiffElement(
            action="modify", element_type="way", element_id=1,
            changeset_id=1, user="u",
            old_tags={"highway": "residential"},
            new_tags={"highway": "primary"},
        )
        w = parse("way[highway](bbox:1,2,3,4)")
        assert not matches(w, elem)

    def test_on_edge(self):
        elem = DiffElement(
            action="create", element_type="node", element_id=1,
            changeset_id=1, user="u",
            old_tags={}, new_tags={"amenity": "cafe"},
            lat=40.7, lon=-74.0,
        )
        w = parse("node[amenity=cafe](bbox:40.7,-74.0,40.8,-73.9)")
        assert matches(w, elem)


class TestCombinedFilters:
    def test_all_match(self, sample_node_create):
        w = parse("node[amenity=hospital][name](new)(bbox:40.7,-74.0,40.8,-73.9)")
        assert matches(w, sample_node_create)

    def test_one_tag_fails(self, sample_node_create):
        w = parse("node[amenity=hospital][highway](new)")
        assert not matches(w, sample_node_create)
