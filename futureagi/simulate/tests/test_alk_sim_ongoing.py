"""Per-call ONGOING status ping (hosted sims show progress, not PENDING -> terminal)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from simulate.serializers.alk_simulate_ingestion import (
    ALKSimulateStatusUpdateSerializer,
)
from simulate.services.alk_simulate_ingestion import mark_alk_sim_call_ongoing


class TestALKSimStatusSerializer:
    def test_accepts_ongoing_only(self):
        assert ALKSimulateStatusUpdateSerializer(data={"status": "ongoing"}).is_valid()
        # Terminal statuses go through the heavyweight result endpoint, not here.
        assert not ALKSimulateStatusUpdateSerializer(
            data={"status": "completed"}
        ).is_valid()
        assert not ALKSimulateStatusUpdateSerializer(
            data={"status": "pending"}
        ).is_valid()


class TestMarkOngoing:
    @patch("simulate.services.alk_simulate_ingestion.CallExecution")
    def test_pending_gated_update_returns_true(self, mock_ce):
        mock_ce.CallStatus.PENDING = "pending"
        mock_ce.CallStatus.ONGOING = "ongoing"
        qs = MagicMock()
        qs.update.return_value = 1
        mock_ce.objects.filter.return_value = qs

        assert mark_alk_sim_call_ongoing(SimpleNamespace(id="cid")) is True
        # PENDING-gated so a late ping cannot clobber a terminal result.
        mock_ce.objects.filter.assert_called_once_with(id="cid", status="pending")
        qs.update.assert_called_once_with(status="ongoing")

    @patch("simulate.services.alk_simulate_ingestion.CallExecution")
    def test_no_pending_row_returns_false(self, mock_ce):
        mock_ce.CallStatus.PENDING = "pending"
        mock_ce.CallStatus.ONGOING = "ongoing"
        qs = MagicMock()
        qs.update.return_value = 0  # already terminal/ongoing -> nothing flipped
        mock_ce.objects.filter.return_value = qs

        assert mark_alk_sim_call_ongoing(SimpleNamespace(id="x")) is False
