from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from osm_watch_slack.consumer import DiffConsumer, parse_augmented_diff

FIXTURES = Path(__file__).parent / "fixtures"


class TestParseAugmentedDiff:
    @pytest.fixture()
    def mixed_elements(self):
        xml_bytes = (FIXTURES / "augmented_diff_mixed.xml").read_bytes()
        return parse_augmented_diff(xml_bytes)

    def test_element_count(self, mixed_elements):
        assert len(mixed_elements) == 3

    def test_create_action(self, mixed_elements):
        elem = mixed_elements[0]
        assert elem.action == "create"
        assert elem.element_type == "node"
        assert elem.element_id == 100
        assert elem.changeset_id == 1000
        assert elem.user == "mapper1"
        assert elem.new_tags["amenity"] == "cafe"
        assert elem.new_tags["name"] == "Corner Coffee"
        assert elem.lat == pytest.approx(40.75)
        assert elem.lon == pytest.approx(-73.95)

    def test_create_has_empty_old_tags(self, mixed_elements):
        elem = mixed_elements[0]
        assert elem.old_tags == {}

    def test_create_uid(self, mixed_elements):
        elem = mixed_elements[0]
        assert elem.uid == 1001

    def test_modify_uid(self, mixed_elements):
        elem = mixed_elements[1]
        assert elem.uid == 1002

    def test_delete_uid(self, mixed_elements):
        elem = mixed_elements[2]
        assert elem.uid == 1003

    def test_modify_action(self, mixed_elements):
        elem = mixed_elements[1]
        assert elem.action == "modify"
        assert elem.element_type == "node"
        assert elem.element_id == 200
        assert elem.changeset_id == 2001
        assert elem.user == "mapper2"

    def test_modify_has_both_tags(self, mixed_elements):
        elem = mixed_elements[1]
        assert elem.old_tags["name"] == "Old Name"
        assert elem.new_tags["name"] == "New Name"
        # Unchanged tag present in both
        assert elem.old_tags["amenity"] == "restaurant"
        assert elem.new_tags["amenity"] == "restaurant"

    def test_delete_action(self, mixed_elements):
        elem = mixed_elements[2]
        assert elem.action == "delete"
        assert elem.element_type == "way"
        assert elem.element_id == 300
        assert elem.changeset_id == 3000
        assert elem.user == "mapper3"
        assert elem.old_tags["highway"] == "residential"
        assert elem.old_tags["name"] == "Elm Street"

    def test_delete_has_empty_new_tags(self, mixed_elements):
        elem = mixed_elements[2]
        assert elem.new_tags == {}

    def test_delete_way_has_bounds_center(self, mixed_elements):
        elem = mixed_elements[2]
        # Center of bounds: (34.0+34.1)/2=34.05, (-118.3+-118.2)/2=-118.25
        assert elem.lat == pytest.approx(34.05)
        assert elem.lon == pytest.approx(-118.25)


class TestDiffConsumerGetSequence:
    async def test_reads_from_state_file(self, tmp_path):
        state_file = tmp_path / "state.txt"
        state_file.write_text("67890")

        consumer = DiffConsumer(
            state_path=str(state_file),
            http_client=AsyncMock(spec=httpx.AsyncClient),
        )
        seq = await consumer.get_sequence()
        assert seq == 67890

    async def test_fetches_from_replication_url(self, tmp_path):
        state_file = tmp_path / "state.txt"
        # state file does not exist

        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.text = (
            "#Thu Jan 01 00:00:00 UTC 2024\n"
            "sequenceNumber=12345\n"
            "timestamp=2024-01-01T00\\:00\\:00Z\n"
        )
        mock_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get.return_value = mock_response

        consumer = DiffConsumer(
            state_path=str(state_file),
            http_client=mock_client,
            replication_url="https://example.com/state.txt",
        )
        seq = await consumer.get_sequence()
        assert seq == 12345
        mock_client.get.assert_called_once_with("https://example.com/state.txt")
