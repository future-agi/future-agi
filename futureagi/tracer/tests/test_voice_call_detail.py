"""Endpoint wiring test for GET /tracer/trace/voice_call_detail/.

Validates the eval_scores restructuring (grouped ``eval_task -> eval ->
{aggregate, spans}`` — the same format as the trace-detail endpoint):

* a top-level trace-scoped ``eval_scores`` object,
* every ``observation_span`` carries its own ``eval_scores`` (root conversation
  span -> ``scope="trace"``, children -> ``scope="span"``),
* the legacy flat ``eval_outputs`` dict is gone.

Eval *aggregation* (avg / pass-fail / choice counts) is covered by the helper
unit tests in ``test_trace_detail_eval_scores.py``. The test ClickHouse has no
``tracer_eval_logger`` table, so the eval query falls back to empty here — this
test locks the response SHAPE end-to-end through the real view.
"""

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from tracer.models.observation_span import ObservationSpan
from tracer.tests._ch_seed import seed_ch_span


def _result(response):
    data = response.json()
    return data.get("result", data)


@pytest.mark.integration
@pytest.mark.api
class TestVoiceCallDetailEvalScores:
    def _seed_call(self, project, trace):
        root = ObservationSpan.objects.create(
            id=f"conv_{uuid.uuid4().hex[:16]}",
            project=project,
            trace=trace,
            name="conversation",
            observation_type="conversation",
            start_time=timezone.now() - timedelta(seconds=10),
            end_time=timezone.now(),
            status="OK",
        )
        seed_ch_span(root)
        child = ObservationSpan.objects.create(
            id=f"child_{uuid.uuid4().hex[:16]}",
            project=project,
            trace=trace,
            parent_span_id=root.id,
            name="child-llm",
            observation_type="llm",
            start_time=timezone.now() - timedelta(seconds=8),
            end_time=timezone.now() - timedelta(seconds=6),
            status="OK",
        )
        seed_ch_span(child)
        return root, child

    def test_voice_call_detail_requires_trace_id(self, auth_client):
        resp = auth_client.get("/tracer/trace/voice_call_detail/")
        assert resp.status_code == 400

    def test_eval_scores_grouped_format(self, auth_client, project, trace):
        self._seed_call(project, trace)
        resp = auth_client.get(
            "/tracer/trace/voice_call_detail/", {"trace_id": str(trace.id)}
        )
        assert resp.status_code == 200
        data = _result(resp)

        # Legacy flat dict replaced by the grouped eval_scores object.
        assert "eval_outputs" not in data
        assert isinstance(data.get("eval_scores"), dict)
        assert data["eval_scores"]["scope"] == "trace"
        assert isinstance(data["eval_scores"]["eval_tasks"], list)

        # Every span carries its own eval_scores; scopes follow root vs child.
        spans = data["observation_span"]
        assert spans, "voice call should expose observation_span entries"
        for span in spans:
            assert "eval_scores" in span
            assert "eval_tasks" in span["eval_scores"]

        root_entry = next(
            s for s in spans if s.get("observation_type") == "conversation"
        )
        assert root_entry["eval_scores"]["scope"] == "trace"

        child_entries = [
            s for s in spans if s.get("observation_type") != "conversation"
        ]
        assert child_entries, "expected at least one child span"
        for child in child_entries:
            assert child["eval_scores"]["scope"] == "span"
