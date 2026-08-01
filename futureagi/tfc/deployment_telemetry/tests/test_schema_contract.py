"""Contract tests binding the sender and receiver wire formats together.

These run in the main repo, where both the sender schema
(``tfc.deployment_telemetry.schema``) and the receiver's vendored copy
(``ee.usage.deployment_telemetry_schema``) are importable. They fail loudly
if the two diverge, if a sender-built payload stops validating on the
receiver, or if the HMAC signing computed by the two sides disagrees.

CI REQUIREMENT: these must run in a gate that has both the main repo and
``ee/`` checked out.  If ``ee`` is absent the import below errors (not
skips), which is intentional — a silently-skipped contract test is no
contract test.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from ee.usage import deployment_telemetry_schema as receiver_schema
from tfc.deployment_telemetry import schema as sender_schema


def _public_constants(module) -> dict:
    """Every UPPER_CASE module-level constant (the wire contract surface)."""
    return {
        name: getattr(module, name)
        for name in dir(module)
        if name.isupper() and not name.startswith("_")
    }


def test_schema_constants_match_across_repos():
    sender_constants = _public_constants(sender_schema)

    assert set(sender_constants) == set(_public_constants(receiver_schema))
    for name, value in sender_constants.items():
        assert value == getattr(receiver_schema, name), name


def test_derive_domain_matches():
    for email in ("a@b.com", "User@Example.COM", "x@sub.domain.io"):
        assert sender_schema.derive_domain(email) == receiver_schema.derive_domain(
            email
        )


def test_sender_heartbeat_payload_validates_on_receiver():
    from ee.usage.serializers.deployment_telemetry import (
        DeploymentHeartbeatSerializer,
    )

    from tfc.deployment_telemetry.payloads import build_heartbeat_payload

    window_end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    window_start = window_end - timedelta(hours=6)
    counts = {field: 0 for field in sender_schema.COUNT_FIELDS}
    counts["traces_count"] = 12
    counts["total_evaluations_count"] = (
        counts["eval_logger_count"]
        + counts["model_hub_evaluations_count"]
        + counts["dataset_eval_runs_count"]
    )
    payload = build_heartbeat_payload(
        uuid.uuid4(), window_start, window_end, counts
    )

    serializer = DeploymentHeartbeatSerializer(data=payload)
    assert serializer.is_valid(), serializer.errors


def test_sender_null_count_validates_on_receiver():
    """A failed collector (null count) round-trips, with a null total."""
    from ee.usage.serializers.deployment_telemetry import (
        DeploymentHeartbeatSerializer,
    )

    from tfc.deployment_telemetry.payloads import build_heartbeat_payload

    window_end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    window_start = window_end - timedelta(hours=6)
    counts = {field: 0 for field in sender_schema.COUNT_FIELDS}
    counts["eval_logger_count"] = None
    counts["total_evaluations_count"] = None
    payload = build_heartbeat_payload(
        uuid.uuid4(), window_start, window_end, counts
    )

    serializer = DeploymentHeartbeatSerializer(data=payload)
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["counts"]["eval_logger_count"] is None


def test_overlarge_count_is_400_not_db_error():
    """The bigint bug: a count above the 63-bit ceiling is rejected, not 500'd."""
    from ee.usage.serializers.deployment_telemetry import (
        DeploymentHeartbeatSerializer,
    )

    from tfc.deployment_telemetry.payloads import build_heartbeat_payload

    window_end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    window_start = window_end - timedelta(hours=6)
    counts = {field: 0 for field in sender_schema.COUNT_FIELDS}
    counts["traces_count"] = sender_schema.MAX_COUNT_VALUE + 1
    payload = build_heartbeat_payload(
        uuid.uuid4(), window_start, window_end, counts
    )

    serializer = DeploymentHeartbeatSerializer(data=payload)
    assert not serializer.is_valid()
    assert "traces_count" in serializer.errors


def test_hmac_signing_matches_across_repos():
    from ee.usage.services.deployment_telemetry import (
        compute_signature as receiver_sign,
    )

    from tfc.deployment_telemetry.transport import (
        compute_signature as sender_sign,
    )

    secret = "test-secret-value"
    body = b'{"instance_id":"x","traces_count":1}'
    assert sender_sign(secret, body) == receiver_sign(secret, body)


def test_signature_header_names_correspond():
    from ee.usage.services.deployment_telemetry import SIGNATURE_HEADER as recv_meta

    from tfc.deployment_telemetry.transport import SIGNATURE_HEADER as send_header

    # Django exposes header "X-Foo-Bar" as META key "HTTP_X_FOO_BAR".
    expected_meta = "HTTP_" + send_header.upper().replace("-", "_")
    assert expected_meta == recv_meta
