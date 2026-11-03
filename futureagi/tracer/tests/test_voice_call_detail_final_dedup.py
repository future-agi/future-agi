"""voice_call_detail must collapse ReplacingMergeTree version rows.

Re-ingesting a span (poll/webhook upsert) writes a second CH row with the
same sort key and a higher ``_version``; until background merges run, both
rows coexist. The endpoint reads with ``FINAL`` so the newest version wins
and tombstones (``is_deleted=1``) drop out. These tests seed duplicate
versions directly and pin that behavior end-to-end.
"""

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status

from model_hub.models.ai_model import AIModel
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.tests._ch_seed import seed_ch_span, seed_ch_trace

VOICE_CALL_DETAIL_URL = "/tracer/trace/voice_call_detail/"


@pytest.fixture
def observe_project(db, organization, workspace):
    return Project.objects.create(
        name=f"Voice Detail Dedup {uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


@pytest.fixture
def observe_trace(db, observe_project):
    trace = Trace.objects.create(
        project=observe_project,
        name="Voice dedup trace",
        input={"prompt": "hi"},
        output={"response": "hello"},
    )
    seed_ch_trace(trace)
    return trace


def _make_root_span(project, trace, raw_log: dict) -> ObservationSpan:
    return ObservationSpan.objects.create(
        id=f"voice_root_{uuid.uuid4().hex[:16]}",
        project=project,
        trace=trace,
        name="Voice root conversation",
        observation_type="conversation",
        provider="vapi",
        start_time=timezone.now() - timedelta(seconds=30),
        end_time=timezone.now(),
        latency_ms=1000,
        status="OK",
        span_attributes={"raw_log": raw_log},
    )


def _get_detail(auth_client, trace):
    return auth_client.get(VOICE_CALL_DETAIL_URL, {"trace_id": str(trace.id)})


@pytest.mark.django_db
class TestVoiceCallDetailFinalDedup:
    def test_root_span_latest_version_wins(
        self, auth_client, observe_project, observe_trace
    ):
        """The production TH-7458 shape: a stale 'in-progress' stub is
        superseded by a re-ingested 'ended' row with recordings — the detail
        endpoint must serve the newer version."""
        span = _make_root_span(
            observe_project,
            observe_trace,
            {"id": "call-123", "status": "in-progress"},
        )
        seed_ch_span(span)

        span.span_attributes = {
            "raw_log": {
                "id": "call-123",
                "status": "ended",
                "recordingUrl": "https://recordings.example/call-123.wav",
            }
        }
        span.save(update_fields=["span_attributes"])
        seed_ch_span(span)  # second CH row, same sort key, higher _version

        resp = _get_detail(auth_client, observe_trace)
        assert resp.status_code == status.HTTP_200_OK, resp.data
        result = resp.data["result"]
        assert result["status"] == "completed"
        assert result["recording_url"] == "https://recordings.example/call-123.wav"

    def test_idempotent_reingest_serves_one_call(
        self, auth_client, observe_project, observe_trace
    ):
        """Re-fetching the same call twice (the remediation script re-runs are
        idempotent) writes two identical CH rows — FINAL must still resolve to a
        single, correct call rather than erroring or duplicating."""
        span = _make_root_span(
            observe_project,
            observe_trace,
            {"id": "call-456", "status": "ended"},
        )
        seed_ch_span(span)
        seed_ch_span(span)  # identical re-ingest, same sort key + status

        resp = _get_detail(auth_client, observe_trace)
        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert resp.data["result"]["status"] == "completed"
        assert resp.data["result"]["call_id"] == "call-456"

    def test_child_span_versions_collapse_to_one_entry(
        self, auth_client, observe_project, observe_trace
    ):
        """A re-ingested child span must appear once, with the newest fields."""
        root = _make_root_span(
            observe_project,
            observe_trace,
            {"id": "call-789", "status": "ended"},
        )
        seed_ch_span(root)

        child = ObservationSpan.objects.create(
            id=f"voice_child_{uuid.uuid4().hex[:16]}",
            project=observe_project,
            trace=observe_trace,
            parent_span_id=root.id,
            name="llm turn v1",
            observation_type="generation",
            start_time=timezone.now() - timedelta(seconds=20),
            end_time=timezone.now() - timedelta(seconds=19),
            latency_ms=500,
            status="OK",
        )
        seed_ch_span(child)

        child.name = "llm turn v2"
        child.save(update_fields=["name"])
        seed_ch_span(child)

        resp = _get_detail(auth_client, observe_trace)
        assert resp.status_code == status.HTTP_200_OK, resp.data
        spans = resp.data["result"]["observation_span"]
        child_entries = [s for s in spans if s["id"] == child.id]
        assert len(child_entries) == 1
        assert child_entries[0]["name"] == "llm turn v2"

    def test_tombstoned_root_span_is_not_resurrected(
        self, auth_client, observe_project, observe_trace
    ):
        """Dropping the explicit ``is_deleted = 0`` predicate must not leak
        deleted spans: the two-arg RMT engine removes tombstoned rows under
        FINAL, so a soft-deleted call returns 404 — the stale live version
        must not be resurrected."""
        span = _make_root_span(
            observe_project,
            observe_trace,
            {"id": "call-del", "status": "ended"},
        )
        seed_ch_span(span)

        span.deleted = True
        seed_ch_span(span)  # tombstone: is_deleted=1, higher _version

        resp = _get_detail(auth_client, observe_trace)
        assert resp.status_code == status.HTTP_404_NOT_FOUND
