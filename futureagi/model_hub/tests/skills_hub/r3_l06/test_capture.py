import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.utils import timezone

from model_hub.models.skills_hub_v12 import HubSkillContextCapture
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v12.capture import (
    accept_trace_case,
    preview_trace_capture,
    purge_expired_captures,
)
from model_hub.services.skills_hub.v12.trace_source import read_trace_source
from tracer.models.project import Project
from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService

pytestmark = pytest.mark.django_db


@pytest.fixture
def trace(principal, monkeypatch):
    project = Project.no_workspace_objects.create(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        name="Synthetic source",
        model_type="GenerativeLLM",
        trace_type="observe",
    )
    now = timezone.now()
    row = {
        "project_id": str(project.pk),
        "trace_id": "synthetic-trace",
        "id": "synthetic-span",
        "start_time": now,
        "observation_type": "generation",
        "service_name": "synthetic-app",
        "_version": "1",
        "is_deleted": 0,
        "input": json.dumps(
            {
                "messages": [
                    {"role": "system", "content": "Private system instructions"},
                    {
                        "role": "user",
                        "content": "Check a synthetic refund for account 123.",
                    },
                    {
                        "role": "assistant",
                        "content": "Failed answer must not become expected truth",
                        "reasoning": "never copy",
                    },
                    {
                        "role": "tool",
                        "content": [
                            {"type": "text", "text": "Refund operation completed."},
                            {"type": "thinking", "text": "Private reasoning"},
                        ],
                    },
                ]
            }
        ),
        "output": "Wrong answer",
        "span_attributes": {},
        "metadata_json": "{}",
    }
    rows, calls = [row], []

    def execute(self, sql, params, **kwargs):
        calls.append((sql, params))
        assert "FINAL" in sql
        assert params["project_id"] == str(project.pk)
        return SimpleNamespace(data=list(rows), complete=True)

    # Mock only the ClickHouse transport. Authorization/extraction/transactions stay real.
    monkeypatch.setattr(V2AnalyticsQueryService, "execute_ch_query", execute)
    ref = {
        "project_id": project.pk,
        "trace_id": row["trace_id"],
        "span_id": row["id"],
        "start_time": now,
    }
    return ref, row, rows, calls, project


def case_input():
    return {
        "id": str(uuid4()),
        "title": "Reviewed synthetic refund",
        "source_group_id": "cannot-forge-lineage",
        "prompt": "Check a synthetic refund for [redacted].",
        "fixtures": [],
        "expected_trigger": None,
        "checks": [
            {
                "id": str(uuid4()),
                "kind": "contains",
                "critical": True,
                "config": {"text": "review", "case_sensitive": False},
            }
        ],
        "reviewed": True,
    }


def preview(principal, context, ref):
    source = read_trace_source(principal=principal, trace_ref=ref)
    return preview_trace_capture(
        principal=principal,
        context_id=context.pk,
        trace_ref=ref,
        selected_segment_ids=[source["segments"][0]["id"]],
    )


def test_capture_redacts_before_sharing_and_replays_without_duplicate(
    principal, context, context_version, trace
):
    ref, row, _, _, _ = trace
    version = context_version(context)
    row["span_attributes"] = {
        "fi.context.id": str(context.pk),
        "fi.context.version_id": str(version.pk),
        "fi.context.digest": version.digest,
    }
    result = preview(principal, context, ref)
    assert result["recorded_context"]["version_id"] == str(version.pk)
    assert all(
        "Private" not in item["text"] and "Failed answer" not in item["text"]
        for item in result["segments"]
    )
    case = case_input()
    request = {
        "source_fingerprint": result["source_fingerprint"],
        "suite_revision": 0,
        "case": case,
        "acknowledge_workspace_sharing": True,
    }
    key = uuid4()
    saved = accept_trace_case(
        principal=principal,
        context_id=context.pk,
        capture_id=result["capture_id"],
        request=request,
        idempotency_key=key,
    )
    assert saved["revision"] == 1
    assert saved["cases"][0]["prompt"] == case["prompt"]
    assert saved["cases"][0]["source_group_id"].startswith("trace:sha256:")
    capture = HubSkillContextCapture.objects.get(pk=result["capture_id"])
    assert capture.selected_text is None
    assert "123" not in json.dumps(capture.accepted_receipt)
    HubSkillContextCapture.objects.filter(pk=capture.pk).update(
        expires_at=timezone.now() - timedelta(days=1)
    )
    assert (
        accept_trace_case(
            principal=principal,
            context_id=context.pk,
            capture_id=result["capture_id"],
            request=request,
            idempotency_key=key,
        )["revision"]
        == 1
    )
    assert len(saved["cases"]) == 1


def test_capture_same_span_id_other_project_denied_before_transport(principal, trace):
    ref, _, _, calls, _ = trace
    with pytest.raises(SkillsHubError) as exc:
        read_trace_source(principal=principal, trace_ref={**ref, "project_id": uuid4()})
    assert exc.value.code == "resource_not_found"
    assert not calls


def test_ambiguous_physical_identity_never_chooses_first(principal, trace):
    ref, row, rows, _, _ = trace
    rows.append({**row, "service_name": "another-service"})
    with pytest.raises(SkillsHubError) as exc:
        read_trace_source(principal=principal, trace_ref=ref)
    assert exc.value.code == "capture_unsafe"


def test_source_change_and_suite_cas_preserve_preview(principal, context, trace):
    ref, row, _, _, _ = trace
    result = preview(principal, context, ref)
    request = {
        "source_fingerprint": result["source_fingerprint"],
        "suite_revision": 0,
        "case": case_input(),
        "acknowledge_workspace_sharing": True,
    }
    row["_version"] = "2"
    with pytest.raises(SkillsHubError) as exc:
        accept_trace_case(
            principal=principal,
            context_id=context.pk,
            capture_id=result["capture_id"],
            request=request,
            idempotency_key=uuid4(),
        )
    assert exc.value.code == "capture_unsafe"
    row["_version"] = "1"
    with pytest.raises(SkillsHubError) as exc:
        accept_trace_case(
            principal=principal,
            context_id=context.pk,
            capture_id=result["capture_id"],
            request={**request, "suite_revision": 1},
            idempotency_key=uuid4(),
        )
    assert exc.value.code == "test_suite_conflict"
    assert (
        HubSkillContextCapture.objects.get(pk=result["capture_id"]).selected_text
        is not None
    )


def test_capture_expiry_purge_and_missing_lineage(principal, context, trace):
    ref, _, _, _, _ = trace
    result = preview(principal, context, ref)
    assert result["recorded_context"] is None
    HubSkillContextCapture.objects.filter(pk=result["capture_id"]).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert purge_expired_captures() == 1
    assert (
        HubSkillContextCapture.objects.get(pk=result["capture_id"]).selected_text
        is None
    )
    with pytest.raises(SkillsHubError) as exc:
        accept_trace_case(
            principal=principal,
            context_id=context.pk,
            capture_id=result["capture_id"],
            request={
                "source_fingerprint": result["source_fingerprint"],
                "suite_revision": 0,
                "case": case_input(),
                "acknowledge_workspace_sharing": True,
            },
            idempotency_key=uuid4(),
        )
    assert exc.value.code == "capture_expired"


def test_external_reasoning_is_never_a_segment(principal, trace):
    ref, _, _, _, _ = trace
    result = read_trace_source(principal=principal, trace_ref=ref)
    assert [segment["kind"] for segment in result["segments"]] == [
        "request",
        "tool_result",
    ]
    serialized = json.dumps(result)
    assert "never copy" not in serialized
    assert "Wrong answer" not in serialized
    assert "Private reasoning" not in serialized
