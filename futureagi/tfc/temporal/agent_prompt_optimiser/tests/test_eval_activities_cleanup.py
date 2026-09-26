from unittest.mock import call, patch

import pytest

from tfc.temporal.agent_prompt_optimiser.eval_activities import (
    _cancel_running_scenario_workflows,
)


@pytest.mark.unit
def test_cleanup_cancels_only_remaining_workflows_and_stays_best_effort():
    workflow_ids = ["completed", "not-found", "cancel-error", "still-running"]
    results_collected = [{"workflow_id": "completed"}]

    with patch(
        "tfc.temporal.common.client.cancel_workflow_sync",
        side_effect=[False, RuntimeError("temporal unavailable"), True],
    ) as cancel_workflow:
        _cancel_running_scenario_workflows(workflow_ids, results_collected)

    assert cancel_workflow.call_args_list == [
        call("not-found"),
        call("cancel-error"),
        call("still-running"),
    ]
