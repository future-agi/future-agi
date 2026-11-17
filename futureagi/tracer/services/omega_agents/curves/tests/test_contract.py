"""The curves answer matches its declared response contract
(tracer/serializers/omega_agent_curves.py), for a full window and for an empty one."""

from datetime import UTC, datetime, timedelta

import pytest

from tracer.serializers.omega_agent_curves import OmegaAgentCurvesResultSerializer
from tracer.services.omega_agents.curves.aggregate import AgentRun, curves

pytestmark = pytest.mark.unit

START = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


def _run(index, version, **fields):
    values = {
        "trace_id": f"trace-{index}",
        "task_id": f"task-{index}",
        "version": version,
        "started_at": START + timedelta(hours=index % 30),
        "latency_ms": 100 + index,
        "approval_wait_ms": index % 3,
        "status": ("OK", "ERROR", "UNSET")[index % 3],
        "llm_spans": index % 2,
        "unpriced_llm_spans": 1 if index % 7 == 0 else 0,
        "cost_micros": 10 * index,
    }
    values.update(fields)
    return AgentRun(**values)


def _answer(runs):
    return {
        "agentId": "workforce_agent_1",
        "projectId": "6f0c9c6e-6f4c-4c65-9d3e-0c8b7c0b1a11",
        "window": {
            "startDate": "2026-10-20",
            "endDate": "2026-10-21",
            "timezone": "UTC",
        },
        **curves(runs),
    }


@pytest.mark.parametrize(
    "runs",
    [
        [],
        [_run(i, 1) for i in range(12)]
        + [_run(100 + i, 2) for i in range(11)]
        + [_run(200, None)],
        [_run(i, None) for i in range(3)],
    ],
    ids=["empty", "two-versions", "unversioned-only"],
)
def test_the_answer_matches_its_contract(runs):
    serializer = OmegaAgentCurvesResultSerializer(data=_answer(runs))

    assert serializer.is_valid(), serializer.errors
