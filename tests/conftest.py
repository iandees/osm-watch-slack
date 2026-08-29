from __future__ import annotations

import pytest

from osm_watch_slack.evaluator import DiffElement


@pytest.fixture
def sample_node_create():
    return DiffElement(
        action="create",
        element_type="node",
        element_id=100,
        changeset_id=1000,
        user="mapper1",
        old_tags={},
        new_tags={"amenity": "hospital", "name": "City Hospital"},
        lat=40.75,
        lon=-73.95,
        uid=1001,
    )


@pytest.fixture
def sample_node_modify():
    return DiffElement(
        action="modify",
        element_type="node",
        element_id=200,
        changeset_id=2000,
        user="mapper2",
        old_tags={"name": "Old Name", "amenity": "cafe"},
        new_tags={"name": "New Name", "amenity": "cafe"},
        lat=51.5,
        lon=-0.1,
        uid=1002,
    )


@pytest.fixture
def sample_way_delete():
    return DiffElement(
        action="delete",
        element_type="way",
        element_id=300,
        changeset_id=3000,
        user="mapper3",
        old_tags={"highway": "residential", "name": "Main St"},
        new_tags={},
        lat=34.05,
        lon=-118.25,
        uid=1003,
    )


@pytest.fixture
def sample_relation():
    return DiffElement(
        action="modify",
        element_type="relation",
        element_id=12345,
        changeset_id=4000,
        user="mapper4",
        old_tags={"name": "Old City", "type": "boundary"},
        new_tags={"name": "New City", "type": "boundary"},
        lat=40.0,
        lon=-74.0,
        uid=1004,
    )
