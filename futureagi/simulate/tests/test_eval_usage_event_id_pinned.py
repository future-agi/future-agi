"""A call evaluation's usage event id, for fixed inputs, is this exact id.

Every tree that bills these evaluations computes the same id for the same
call, call workflow run and evaluator, so a retry that lands on a newer
release after a deploy is billed once. The inputs are made-up values.
"""

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.requires_ee

CALL_ID = "11111111-1111-4111-8111-111111111111"
RUN = (
    "call-exec-11111111-1111-4111-8111-111111111111",
    "22222222-2222-4222-8222-222222222222",
)
EVAL_CONFIG_ID = "33333333-3333-4333-8333-333333333333"


def _event_id(evaluator):
    from evaluations.usage_step import step_event_id
    from simulate.temporal.activities import xl

    call = SimpleNamespace(id=CALL_ID)
    return step_event_id(xl._usage_step(call, RUN, evaluator))


def test_an_evaluation_config_event_id_is_pinned():
    evaluator = ("simulate_eval_config", EVAL_CONFIG_ID)

    assert _event_id(evaluator) == "59190707-0769-5183-a833-f3e8ada59f51"


def test_a_tool_call_evaluation_event_id_is_pinned():
    evaluator = ("simulate_tool_evaluation", "0")

    assert _event_id(evaluator) == "cb74a063-1d3c-5709-877f-246a53cfe072"
