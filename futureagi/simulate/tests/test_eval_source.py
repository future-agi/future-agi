"""Both eval paths log the call's source, which keeps a fix's check runs out of the eval limits."""

from unittest.mock import patch

import pytest

from simulate.tests.test_eval_resolver_behavior import (
    _SUCCESS_STUB,
    _make_eval,
    _run,
    _run_xl,
)
from simulate.tests.test_eval_resolver_behavior import (  # noqa: F401 - fixtures
    agent_definition as agent_definition,
)
from simulate.tests.test_eval_resolver_behavior import agent_version as agent_version
from simulate.tests.test_eval_resolver_behavior import call_execution as call_execution
from simulate.tests.test_eval_resolver_behavior import (
    dataset_for_scenario as dataset_for_scenario,
)
from simulate.tests.test_eval_resolver_behavior import eval_template as eval_template
from simulate.tests.test_eval_resolver_behavior import run_test as run_test
from simulate.tests.test_eval_resolver_behavior import scenario as scenario
from simulate.tests.test_eval_resolver_behavior import (
    simulator_agent as simulator_agent,
)
from simulate.tests.test_eval_resolver_behavior import test_execution as test_execution
from simulate.tests.test_eval_resolver_behavior import (
    transcript_data as transcript_data,
)


@pytest.mark.django_db
@patch("simulate.services.test_executor.close_old_connections", lambda: None)
@patch("simulate.temporal.activities.xl.close_old_connections", lambda: None)
@pytest.mark.parametrize(
    "runner, module, run_eval",
    [
        (
            _run,
            "simulate.services.test_executor",
            "simulate.services.test_executor.run_eval_func",
        ),
        (
            _run_xl,
            "simulate.temporal.activities.xl",
            "model_hub.views.utils.evals.run_eval_func",
        ),
    ],
)
def test_both_eval_paths_log_the_call_s_source(
    runner, module, run_eval, run_test, call_execution, transcript_data, eval_template
):
    with (
        patch(f"{module}.eval_source", return_value="from-the-call"),
        patch(run_eval, return_value=_SUCCESS_STUB) as ran,
    ):
        runner(
            _make_eval({"conversation": "call.transcript"}, run_test, eval_template),
            call_execution,
            transcript_data,
        )

    assert ran.call_args.kwargs["source"] == "from-the-call"
