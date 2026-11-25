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
@pytest.mark.parametrize("check_run", [True, False])
def test_a_check_run_counts_what_its_evals_bill_and_localizes_nothing(
    runner,
    module,
    run_eval,
    check_run,
    run_test,
    call_execution,
    transcript_data,
    eval_template,
):
    source = "fix_check_run" if check_run else "simulate"
    config = _make_eval({"conversation": "call.transcript"}, run_test, eval_template)
    config.error_localizer = True
    with (
        patch(f"{module}.eval_source", return_value=source),
        patch(f"{module}.accrue_check_run_eval_cost") as accrue,
        patch(run_eval, return_value=_SUCCESS_STUB) as ran,
    ):
        runner(config, call_execution, transcript_data)
        on_billed = ran.call_args.kwargs["on_billed"]
        if on_billed is not None:
            on_billed(250)

    assert ran.call_args.kwargs["error_localizer"] is (not check_run)
    if check_run:
        accrue.assert_called_once_with(call_execution.test_execution_id, 250)
    else:
        assert on_billed is None
        accrue.assert_not_called()
