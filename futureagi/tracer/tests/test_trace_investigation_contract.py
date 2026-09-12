from types import SimpleNamespace
from unittest.mock import patch

from tracer.models.trace_scan import TraceScanEngine
from tracer.queries.trace_scanner import get_scan_config
from tracer.serializers.trace_investigation import PublishInvestigationRequestSerializer
from tracer.services.trace_investigation import canonical_wire_result_digest


def test_legacy_scanner_rejects_omega_project_config():
    config = SimpleNamespace(enabled=True, engine=TraceScanEngine.OMEGA)
    with patch(
        "tracer.queries.trace_scanner.TraceScanConfig.objects.get_or_create",
        return_value=(config, False),
    ):
        assert get_scan_config("project-id") is None


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
