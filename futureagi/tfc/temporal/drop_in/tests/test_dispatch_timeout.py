"""start_activity hands a dispatch deadline to the sync bridge, and only then."""

from unittest.mock import patch

import pytest

from tfc.temporal.drop_in.runner import start_activity


@pytest.mark.unit
@pytest.mark.parametrize("deadline", [2.0, None])
def test_start_activity_threads_the_deadline_to_the_bridge(deadline):
    seen = {}

    def bridge(dispatch, **kwargs):
        seen.update(kwargs)
        return "workflow-1"

    with patch(
        "tfc.temporal.common.client._run_async_in_sync_context", side_effect=bridge
    ):
        result = start_activity(
            "probe",
            args=(),
            kwargs={},
            queue="default",
            task_id="probe-1",
            dispatch_timeout_seconds=deadline,
        )

    assert result == "workflow-1"
    if deadline is None:
        assert seen == {}
    else:
        assert seen == {"dispatch_timeout_seconds": 2.0}
