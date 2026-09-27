import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from tracer.models.trace_scan import TraceScanConfig
from tracer.queries.trace_scanner import get_scan_config
from tracer.serializers.trace_investigation import (
    FindingAttributionRoleSerializer,
    PublishInvestigationRequestSerializer,
)
from tracer.services.trace_investigation import canonical_wire_result_digest


def test_legacy_scanner_rejects_omega_project_config():
    assert TraceScanConfig._meta.get_field("scan_version").get_default() == "omega-v1"
    config = SimpleNamespace(enabled=True, scan_version="omega-v1")
    with patch(
        "tracer.queries.trace_scanner.TraceScanConfig.objects.get_or_create",
        return_value=(config, False),
    ):
        assert get_scan_config("project-id") is None


def test_attribution_explanation_is_optional_and_only_for_supported_roles():
    old = FindingAttributionRoleSerializer(
        data={"status": "supported", "span_id": "span-1", "evidence_ids": ["ev-1"]}
    )
    assert old.is_valid(), old.errors
    assert "explanation" not in old.validated_data

    explained = FindingAttributionRoleSerializer(
        data={
            "status": "supported",
            "span_id": "span-1",
            "evidence_ids": ["ev-1"],
            "explanation": "This call returned the wrong amount.",
        }
    )
    assert explained.is_valid(), explained.errors
    assert explained.validated_data["explanation"] == "This call returned the wrong amount."

    unsupported = FindingAttributionRoleSerializer(
        data={"status": "unknown", "span_id": None, "evidence_ids": [], "explanation": "Guess"}
    )
    assert not unsupported.is_valid()


def test_queued_legacy_task_skips_omega_project_before_embedding():
    from tracer.tasks.trace_scanner import scan_traces_task

    with (
        patch("tracer.tasks.trace_scanner.get_scan_config", return_value=None),
        patch("tracer.tasks.trace_scanner.scan_and_write") as scan,
        patch("tracer.tasks.trace_scanner.embed_trace_inputs_task") as embed,
    ):
        scan_traces_task._original_func(["t"], "p")
    scan.assert_not_called()
    embed.apply_async.assert_not_called()


def test_pg_ingested_root_enters_omega_ledger_with_stable_identity():
    from tracer.utils.trace_ingestion import _record_inline_omega_roots

    project_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    project = SimpleNamespace(
        id=project_id, organization_id=organization_id, workspace_id=None
    )
    root = SimpleNamespace(
        id=uuid.uuid4(),
        trace_id=uuid.uuid4(),
        project_id=project_id,
        parent_span_id=None,
        end_time=datetime(2026, 9, 17, tzinfo=UTC),
    )
    with (
        patch("tracer.utils.trace_ingestion.Project") as projects,
        patch("tracer.utils.trace_ingestion.record_trace_notifications") as record,
    ):
        projects.no_workspace_objects.filter.return_value = [project]
        _record_inline_omega_roots([root], str(organization_id))
        _record_inline_omega_roots([root], str(organization_id))
    first = record.call_args_list[0].kwargs["deliveries"][0]
    second = record.call_args_list[1].kwargs["deliveries"][0]
    assert first == second
    assert first["value"]["traces"][0]["trace_id"] == root.trace_id


def test_omega_replaces_legacy_sweep():
    from tfc.temporal.schedules import tracer as schedules

    assert "sweep-scannable-traces" not in {
        item.schedule_id for item in schedules.TRACER_SCHEDULES
    }
    assert not any(
        "omega" in item.schedule_id or "error-feed-v2" in item.schedule_id
        for item in schedules.TRACER_SCHEDULES
    )


def test_canonical_wire_result_digest_interoperability_vector():
    wire_result = {
        "read_cutoff": "2026-09-12T10:20:30.123456Z",
        "gateway_accounting": [
            {"cost": None, "model_used": "openai/test", "raw": {"charged": False}},
            {"cost": 0, "model_used": "openai/test", "raw": {"units": 1}},
            {"cost": 1, "model_used": "openai/test", "raw": None},
        ],
        "nested": {"small": 0.001, "large": 1000000000000000},
        "result_digest": "ignored",
    }

    assert canonical_wire_result_digest(wire_result) == (
        "sha256:c0c2d86a98393d4a34d1ec1f7aabff4b5c134d06c7fb568185f607bbb91e09c8"
    )


def test_publish_serializer_hashes_raw_wire_before_datetime_normalization():
    serializer = PublishInvestigationRequestSerializer(
        data={
            "idempotency_key": "attempt:result",
            "lease_token": "token",
            "result": {
                "contract_version": "omega-investigation/v1",
                "organization_id": "11111111-1111-1111-1111-111111111111",
                "workspace_id": None,
                "project_id": "22222222-2222-2222-2222-222222222222",
                "job_id": "33333333-3333-3333-3333-333333333333",
                "generation": 1,
                "attempt_id": "44444444-4444-4444-4444-444444444444",
                "trace_id": "55555555-5555-5555-5555-555555555555",
                "engine_version": "omega-v1",
                "read_cutoff": "2026-09-12T10:20:30.123456Z",
                "memory_snapshot_id": "snapshot-1",
                "memory_digest": f"sha256:{'a' * 64}",
                "evidence_digest": f"sha256:{'b' * 64}",
                "execution_status": "completed",
                "outcome": "success",
                "findings": [],
                "requirement_checks": [],
                "evidence_receipts": [],
                "verification_receipts": [],
                "coverage": {
                    "scope": "available trace at cutoff",
                    "observed_span_count": 1,
                    "read_complete": True,
                    "future_arrivals_known": False,
                },
                "usage": {
                    "model_calls": 1,
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cost_usd": None,
                    "cost_status": "unpriced",
                },
                "gateway_accounting": [{"model_used": "openai/test", "cost": None}],
                "result_digest": f"sha256:{'c' * 64}",
            },
        }
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["wire_result_digest"] == (
        canonical_wire_result_digest(serializer.initial_data["result"])
    )
