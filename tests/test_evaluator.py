from __future__ import annotations

from osm_watch_slack.dsl import ChangesetTagFilter, TagFilter, WatchFilter, parse
from osm_watch_slack.evaluator import DiffElement, _changeset_tag_matches, matches


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


class TestNwrMatching:
    def test_nwr_matches_node(self, sample_node_create):
        w = parse("nwr[amenity=hospital]")
        assert matches(w, sample_node_create)

    def test_nwr_matches_way(self, sample_way_delete):
        w = parse("nwr[highway=residential]")
        assert matches(w, sample_way_delete)

    def test_nwr_matches_relation(self, sample_relation):
        w = parse("nwr[name]")
        assert matches(w, sample_relation)


class TestUserMatching:
    def test_user_matches(self, sample_node_create):
        w = parse("node[amenity=hospital](user:mapper1)")
        assert matches(w, sample_node_create)

    def test_user_does_not_match(self, sample_node_create):
        w = parse("node[amenity=hospital](user:other_mapper)")
        assert not matches(w, sample_node_create)


class TestUidMatching:
    def test_uid_matches(self, sample_node_create):
        w = parse("node[amenity=hospital](uid:1001)")
        assert matches(w, sample_node_create)

    def test_uid_does_not_match(self, sample_node_create):
        w = parse("node[amenity=hospital](uid:9999)")
        assert not matches(w, sample_node_create)

    def test_uid_none_does_not_match(self):
        elem = DiffElement(
            action="create", element_type="node", element_id=1,
            changeset_id=1, user="u",
            old_tags={}, new_tags={"name": "Foo"},
            uid=None,
        )
        w = parse("node[name](uid:123)")
        assert not matches(w, elem)


class TestUserAgeMatching:
    def test_young_user_matches(self):
        from datetime import datetime, timedelta, timezone
        created = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        elem = DiffElement(
            action="create", element_type="node", element_id=1,
            changeset_id=1, user="newbie",
            old_tags={}, new_tags={"building": "yes"},
            user_created_at=created,
        )
        w = parse("nwr[building](user_age:<30d)")
        assert matches(w, elem)

    def test_old_user_does_not_match(self):
        from datetime import datetime, timedelta, timezone
        created = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        elem = DiffElement(
            action="create", element_type="node", element_id=1,
            changeset_id=1, user="veteran",
            old_tags={}, new_tags={"building": "yes"},
            user_created_at=created,
        )
        w = parse("nwr[building](user_age:<30d)")
        assert not matches(w, elem)

    def test_no_created_at_does_not_match(self):
        elem = DiffElement(
            action="create", element_type="node", element_id=1,
            changeset_id=1, user="unknown",
            old_tags={}, new_tags={"building": "yes"},
            user_created_at=None,
        )
        w = parse("nwr[building](user_age:<30d)")
        assert not matches(w, elem)


class TestCombinedFilters:
    def test_all_match(self, sample_node_create):
        w = parse("node[amenity=hospital][name](new)(bbox:40.7,-74.0,40.8,-73.9)")
        assert matches(w, sample_node_create)

    def test_one_tag_fails(self, sample_node_create):
        w = parse("node[amenity=hospital][highway](new)")
        assert not matches(w, sample_node_create)


class TestSubstringTagFilter:
    def test_substring_matches(self):
        elem = DiffElement(
            action="create", element_type="node", element_id=1,
            changeset_id=1, user="u",
            old_tags={}, new_tags={"name": "City Hospital North"},
        )
        w = parse("node[name~hospital]")
        assert matches(w, elem)

    def test_substring_case_insensitive(self):
        elem = DiffElement(
            action="create", element_type="node", element_id=1,
            changeset_id=1, user="u",
            old_tags={}, new_tags={"name": "CITY HOSPITAL"},
        )
        w = parse("node[name~hospital]")
        assert matches(w, elem)

    def test_substring_no_match(self):
        elem = DiffElement(
            action="create", element_type="node", element_id=1,
            changeset_id=1, user="u",
            old_tags={}, new_tags={"name": "City Clinic"},
        )
        w = parse("node[name~hospital]")
        assert not matches(w, elem)


class TestChangesetTagFilter:
    def test_exact_match(self):
        ct = ChangesetTagFilter("created_by", "JOSM")
        assert _changeset_tag_matches(ct, {"created_by": "JOSM", "comment": "test"})

    def test_exact_no_match(self):
        ct = ChangesetTagFilter("created_by", "JOSM")
        assert not _changeset_tag_matches(ct, {"created_by": "iD"})

    def test_substring_match(self):
        ct = ChangesetTagFilter("comment", "import", substring=True)
        assert _changeset_tag_matches(ct, {"comment": "Building import for NYC"})

    def test_substring_case_insensitive(self):
        ct = ChangesetTagFilter("comment", "import", substring=True)
        assert _changeset_tag_matches(ct, {"comment": "IMPORT of buildings"})

    def test_key_exists(self):
        ct = ChangesetTagFilter("source")
        assert _changeset_tag_matches(ct, {"source": "bing", "comment": "test"})

    def test_key_missing(self):
        ct = ChangesetTagFilter("source")
        assert not _changeset_tag_matches(ct, {"comment": "test"})

    def test_empty_tags(self):
        ct = ChangesetTagFilter("created_by", "JOSM")
        assert not _changeset_tag_matches(ct, {})

    def test_integrated_with_matches(self):
        w = WatchFilter(
            element_type="node",
            tags=(TagFilter("amenity", "cafe"),),
            changeset_tags=(ChangesetTagFilter("created_by", "JOSM"),),
        )
        elem = DiffElement(
            action="create", element_type="node", element_id=1,
            changeset_id=1, user="u",
            old_tags={}, new_tags={"amenity": "cafe"},
            changeset_tags={"created_by": "JOSM"},
        )
        assert matches(w, elem)

    def test_integrated_changeset_tag_fails(self):
        w = WatchFilter(
            element_type="node",
            tags=(TagFilter("amenity", "cafe"),),
            changeset_tags=(ChangesetTagFilter("created_by", "JOSM"),),
        )
        elem = DiffElement(
            action="create", element_type="node", element_id=1,
            changeset_id=1, user="u",
            old_tags={}, new_tags={"amenity": "cafe"},
            changeset_tags={"created_by": "iD"},
        )
        assert not matches(w, elem)
