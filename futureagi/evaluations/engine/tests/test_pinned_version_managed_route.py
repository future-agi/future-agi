"""One routing rule for a pinned model with no stored key.

Kept apart from the core engine tests because it imports the managed-route
test fixtures (and their LLM and task modules). A guard: it passes before
and after the change.
"""

import pytest

from evaluations.engine import instance
from evaluations.engine.tests.test_pinned_version_engine import (
    PINNED_RUBRIC,
    PINNED_SYSTEM,
    build,
    make_template,
    make_version,
)
from model_hub.tests.test_model_route_llm import mr as mr


@pytest.mark.django_db
def test_a_pinned_model_on_a_managed_route_builds_like_an_unpinned_one(
    mr, organization, workspace
):
    """One routing rule: a pinned model with no stored key gets exactly what an
    unpinned evaluator naming that model gets from the same managed route."""
    from model_hub.tests.test_model_route_support import (
        OPENAI_CHAT_MODEL,
        eligible_port,
    )

    template = make_template(organization, workspace)
    make_version(template, 1, PINNED_RUBRIC, PINNED_SYSTEM, model=OPENAI_CHAT_MODEL)
    port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL})
    common = {
        "workspace_id": str(workspace.id),
        "managed_judge_billing": instance.MANAGED_JUDGE_SINGLE_METER,
    }
    with mr.managed_routing_port(port):
        pinned = build(
            template, organization, version_number=1, model="turing_small", **common
        )
        unpinned = build(template, organization, model=OPENAI_CHAT_MODEL, **common)

    for built in (pinned, unpinned):
        assert built["model"] == OPENAI_CHAT_MODEL
        assert built["api_key"] is None
        assert mr.is_managed_route_binding(built["model_route"])
        assert built["model_route"].model_name == OPENAI_CHAT_MODEL
    assert pinned["provider"] == unpinned["provider"]
    assert pinned["model_route"].operation == unpinned["model_route"].operation
    # A binding admits nothing; each judge call is admitted when it is made.
    assert port.authorize_calls == []
