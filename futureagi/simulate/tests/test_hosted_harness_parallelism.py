"""Track E platform-side scenario-parallelism tests (C4 v1.3 FROZEN).

Covers the three closers §8 makes blocking:
  * ingestion validator accepts exactly the FIVE-member degrade enum and the
    attempt-level projection (round-trip, eviction, multi-attempt, idempotency);
  * the shared W>1 admission guard authoritative at ``register_attempt``
    (flag AND digest, dockerfile-mode flag-only, empty-digest fail-closed,
    requested-value preservation for honest reruns);
  * the create-time serializer belt (clamp-not-reject + inline
    ``environment_values`` loopback warn) and the ``serialize_job`` / preflight
    surfacing.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest
from django.test import override_settings

from simulate.models import HostedHarnessAttempt, HostedHarnessEvent
from simulate.serializers.harness_job import HarnessJobCreateSerializer
from simulate.services.harness_provider import serialize_job
from simulate.services.hosted_harness import (
    canonical_digest,
    clamp_parallelism,
    create_hosted_job,
    parallelism_w_gt_1_enabled,
    register_attempt,
)
from simulate.services.hosted_harness_ingestion import ingest_event_batch

DEGRADE_REASONS = (
    "resource_limited",
    "literal_local_endpoint",
    "world_start_failed",
    "fixed_port",
    "conformance_gate_failed",
)


def _payload(parallelism=1, **overrides):
    value = {
        "schema_version": "futureagi.harness-job.v1",
        "source": {
            "kind": "remote",
            "endpoint": "https://agent.example.com",
            "visibility": "public",
        },
        "agent": {"connector": "vapi", "config": {}, "secret_refs": {}},
        "scenario_count": 1,
        "seed": 7,
        "runtime": {
            "isolation": "dedicated_vm",
            "cpu_units": 8,
            "memory_mb": 4096,
            "parallelism": parallelism,
            "concurrency_weight": 1,
            "max_duration_seconds": 600,
            "network_policy": "live",
        },
        "security": {
            "untrusted_source": True,
            "read_only_source": True,
            "allow_privileged": False,
            "allow_host_runtime_control": False,
            "allowed_egress_domains": ["agent.example.com"],
        },
        "retry": {
            "max_infrastructure_attempts": 2,
            "initial_backoff_seconds": 1,
            "max_backoff_seconds": 15,
            "retryable_domains": ["infrastructure", "connectivity"],
        },
        "artifacts": {
            "level": "full",
            "retention_days": 30,
            "allow_bundle_download": False,
            "max_artifact_bytes": 1024,
        },
        "metadata": {},
    }
    value.update(overrides)
    return value


def _event(attempt, seq, event_type, payload, *, event_id=None, stage="building_environment"):
    return {
        "event_id": event_id or f"event-{seq}",
        "job_id": str(attempt.job_id),
        "attempt_id": str(attempt.id),
        "attempt_number": attempt.attempt_number,
        "sequence": seq,
        "stage": stage,
        "type": event_type,
        "payload": payload,
        "digest": canonical_digest(payload),
        "emitted_at": datetime.now(UTC),
    }


def _degrade(attempt, seq, *, requested, effective, reason, event_id=None):
    return _event(
        attempt,
        seq,
        "parallelism_degraded",
        {"requested": requested, "effective": effective, "reason": reason},
        event_id=event_id,
    )


# ── Ingestion validator + attempt-level projection (C4 §2/§6/§8) ────────────


@pytest.mark.django_db
@pytest.mark.parametrize("reason", DEGRADE_REASONS)
def test_every_degrade_reason_accepted_visible_and_projected(organization, reason):
    job, _ = create_hosted_job(
        organization, _payload(parallelism=4), idempotency_key=f"deg-{reason}"
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    result = ingest_event_batch(
        capability.attempt,
        [_degrade(capability.attempt, 1, requested=4, effective=2, reason=reason)],
    )
    assert result["rejected"] == []
    stored = HostedHarnessEvent.no_workspace_objects.get(event_id="event-1")
    assert stored.accepted is True
    assert stored.payload == {"requested": 4, "effective": 2, "reason": reason}

    dto = serialize_job(job)
    # (b) survives to the exact feed the FE renders
    assert any(e["event_id"] == "event-1" for e in dto["events"])
    # (c) attempt-level projection reflects reason + effective
    assert dto["parallelism"]["effective"] == 2
    assert dto["parallelism"]["requested"] == 4
    assert dto["parallelism"]["degrade_reasons"] == [reason]


@pytest.mark.django_db
def test_port_not_consumable_is_rejected_and_never_touches_attempt(organization):
    # D28: port_not_consumable is a TERMINAL JOB FAILURE, not a degrade reason.
    job, _ = create_hosted_job(
        organization, _payload(parallelism=4), idempotency_key="pnc"
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    result = ingest_event_batch(
        capability.attempt,
        [_degrade(capability.attempt, 1, requested=4, effective=1, reason="port_not_consumable")],
    )
    assert result["rejected"][0]["code"] == "event_payload_invalid"
    stored = HostedHarnessEvent.no_workspace_objects.get(event_id="event-1")
    assert stored.accepted is False
    capability.attempt.refresh_from_db()
    assert capability.attempt.effective_parallelism is None
    assert capability.attempt.degrade_reasons == []
    assert serialize_job(job)["parallelism"]["effective"] == 4


@pytest.mark.django_db
def test_unknown_reason_rejected(organization):
    job, _ = create_hosted_job(
        organization, _payload(parallelism=4), idempotency_key="unknown"
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    result = ingest_event_batch(
        capability.attempt,
        [_degrade(capability.attempt, 1, requested=4, effective=1, reason="made_up_reason")],
    )
    assert result["rejected"][0]["code"] == "event_payload_invalid"
    capability.attempt.refresh_from_db()
    assert capability.attempt.degrade_reasons == []


@pytest.mark.django_db
def test_degrade_survives_event_window_eviction(organization):
    job, _ = create_hosted_job(
        organization, _payload(parallelism=4), idempotency_key="evict"
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    ingest_event_batch(
        capability.attempt,
        [_degrade(capability.attempt, 1, requested=4, effective=2, reason="resource_limited")],
    )
    # Plant >100 subsequent accepted events so the degrade event falls out of the
    # last-100 feed window.
    logs = [
        _event(capability.attempt, seq, "log", {"level": "info", "message": f"tick {seq}"}, stage="running")
        for seq in range(2, 108)
    ]
    ingest_event_batch(capability.attempt, logs)

    dto = serialize_job(job)
    assert len(dto["events"]) == 100
    assert all(e["event_id"] != "event-1" for e in dto["events"])  # evicted
    # ...but the attempt-level projection still drives the display.
    assert dto["parallelism"]["effective"] == 2
    assert dto["parallelism"]["degrade_reasons"] == ["resource_limited"]


@pytest.mark.django_db
def test_projection_is_attempt_level_and_new_attempt_starts_cleared(organization):
    job, _ = create_hosted_job(
        organization, _payload(parallelism=4), idempotency_key="multi"
    )
    first = register_attempt(job.id, endpoint_base_url="https://platform.example")
    ingest_event_batch(
        first.attempt,
        [_degrade(first.attempt, 1, requested=4, effective=2, reason="resource_limited")],
    )
    assert serialize_job(job)["parallelism"]["effective"] == 2

    # Attempt 2 (rerun): degrades differently. serialize_job reads the LATEST
    # attempt, so attempt 1's projection must not bleed through.
    second = register_attempt(job.id, endpoint_base_url="https://platform.example")
    assert second.attempt.effective_parallelism is None
    assert second.attempt.degrade_reasons == []
    ingest_event_batch(
        second.attempt,
        [
            _degrade(
                second.attempt,
                1,
                requested=4,
                effective=1,
                reason="world_start_failed",
                event_id="event-a2-1",
            )
        ],
    )
    dto = serialize_job(job)
    assert dto["parallelism"]["effective"] == 1
    assert dto["parallelism"]["degrade_reasons"] == ["world_start_failed"]


@pytest.mark.django_db
def test_multiple_reasons_accumulate_min_monotone(organization):
    job, _ = create_hosted_job(
        organization, _payload(parallelism=4), idempotency_key="accum"
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    ingest_event_batch(
        capability.attempt,
        [
            _degrade(capability.attempt, 1, requested=4, effective=2, reason="resource_limited"),
            _degrade(capability.attempt, 2, requested=4, effective=1, reason="world_start_failed"),
        ],
    )
    dto = serialize_job(job)
    assert dto["parallelism"]["effective"] == 1
    assert dto["parallelism"]["degrade_reasons"] == [
        "resource_limited",
        "world_start_failed",
    ]


@pytest.mark.django_db
def test_redelivery_and_out_of_order_cannot_raise_effective_or_double_append(
    organization,
):
    job, _ = create_hosted_job(
        organization, _payload(parallelism=4), idempotency_key="idem"
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    ingest_event_batch(
        capability.attempt,
        [_degrade(capability.attempt, 1, requested=4, effective=2, reason="resource_limited")],
    )
    # Re-deliver the identical event (same event_id) — dedup suppresses the store.
    ingest_event_batch(
        capability.attempt,
        [_degrade(capability.attempt, 1, requested=4, effective=2, reason="resource_limited")],
    )
    # Out-of-order duplicate carrying a HIGHER effective (same reason, new id).
    ingest_event_batch(
        capability.attempt,
        [
            _degrade(
                capability.attempt,
                2,
                requested=4,
                effective=3,
                reason="resource_limited",
                event_id="event-hi",
            )
        ],
    )
    dto = serialize_job(job)
    assert dto["parallelism"]["effective"] == 2  # min(2, 3), never raised
    assert dto["parallelism"]["degrade_reasons"] == ["resource_limited"]  # once


@pytest.mark.django_db
def test_cross_event_invariant_violation_warns_without_rejecting_or_changing_projection(
    organization, caplog
):
    # C4 §8: on a parallelism_degraded event the platform runs a per-attempt
    # cross-event check of the two invariants — effective STRICTLY DECREASING
    # across the attempt's accepted degrade events, and <=1 event per reason —
    # and LOGS a warning on violation. It never rejects and never changes the
    # projection, which stays min-monotone + append-if-absent.
    job, _ = create_hosted_job(
        organization, _payload(parallelism=4), idempotency_key="xevent"
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    ingest_event_batch(
        capability.attempt,
        [_degrade(capability.attempt, 1, requested=4, effective=2, reason="resource_limited")],
    )
    with caplog.at_level(
        logging.WARNING, logger="simulate.services.hosted_harness_ingestion"
    ):
        # (a) non-decreasing effective (3 >= recorded 2), a new reason.
        result_hi = ingest_event_batch(
            capability.attempt,
            [
                _degrade(
                    capability.attempt,
                    2,
                    requested=4,
                    effective=3,
                    reason="world_start_failed",
                    event_id="event-hi",
                )
            ],
        )
        # (b) duplicate reason already recorded, strictly-lower effective.
        result_dup = ingest_event_batch(
            capability.attempt,
            [
                _degrade(
                    capability.attempt,
                    3,
                    requested=4,
                    effective=1,
                    reason="resource_limited",
                    event_id="event-dup",
                )
            ],
        )
    # Never rejects.
    assert result_hi["rejected"] == []
    assert result_dup["rejected"] == []
    stored_hi = HostedHarnessEvent.no_workspace_objects.get(event_id="event-hi")
    assert stored_hi.accepted is True
    # Both violations logged exactly one warning each.
    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING
        and r.name == "simulate.services.hosted_harness_ingestion"
    ]
    assert len(warnings) == 2
    # Projection unchanged: min-monotone effective + append-if-absent reasons.
    dto = serialize_job(job)
    assert dto["parallelism"]["effective"] == 1  # min(2, 3, 1), never raised
    assert dto["parallelism"]["degrade_reasons"] == [
        "resource_limited",
        "world_start_failed",
    ]


# ── Authoritative admission guard at register_attempt (C4 §4/§5, D23/D24) ────


@override_settings(
    HARNESS_PARALLELISM_ENABLED=False,
    HARNESS_PARALLEL_SNAPSHOT_DIGESTS=[],
    ALK_DAYTONA_DOCKERFILE="",
)
@pytest.mark.django_db
def test_register_attempt_clamps_w_gt_1_when_flag_off(organization):
    job, _ = create_hosted_job(
        organization, _payload(parallelism=4), idempotency_key="clamp-off"
    )
    capability = register_attempt(
        job.id, endpoint_base_url="https://platform.example"
    )
    job.refresh_from_db()
    # register_attempt is the single admission source of truth: it RETURNS the
    # admitted W so the gateway never re-derives it.
    assert capability.admitted_parallelism == 1
    assert job.payload["metadata"]["parallelism_clamped"] == {"requested": 4}
    # Requested value preserved for an honest later re-evaluation.
    assert job.payload["runtime"]["parallelism"] == 4


@override_settings(
    HARNESS_PARALLELISM_ENABLED=True,
    HARNESS_PARALLEL_SNAPSHOT_DIGESTS=["sha256:good"],
    ALK_DAYTONA_DOCKERFILE="",
)
@pytest.mark.django_db
def test_register_attempt_admits_when_flag_on_and_digest_listed(organization):
    job, _ = create_hosted_job(
        organization, _payload(parallelism=4), idempotency_key="admit"
    )
    capability = register_attempt(
        job.id,
        endpoint_base_url="https://platform.example",
        snapshot_digest="sha256:good",
    )
    job.refresh_from_db()
    # Admitted at the requested W and RETURNED for the gateway to apply.
    assert capability.admitted_parallelism == 4
    assert "parallelism_clamped" not in job.payload["metadata"]


@override_settings(
    HARNESS_PARALLELISM_ENABLED=True,
    HARNESS_PARALLEL_SNAPSHOT_DIGESTS=["sha256:good"],
    ALK_DAYTONA_DOCKERFILE="",
)
@pytest.mark.django_db
def test_register_attempt_fails_closed_on_empty_digest(organization):
    job, _ = create_hosted_job(
        organization, _payload(parallelism=4), idempotency_key="empty-digest"
    )
    register_attempt(
        job.id, endpoint_base_url="https://platform.example", snapshot_digest=""
    )
    job.refresh_from_db()
    assert job.payload["metadata"]["parallelism_clamped"] == {"requested": 4}


@override_settings(
    HARNESS_PARALLELISM_ENABLED=True,
    HARNESS_PARALLEL_SNAPSHOT_DIGESTS=[],
    ALK_DAYTONA_DOCKERFILE="/hosted/Dockerfile",
)
@pytest.mark.django_db
def test_register_attempt_dockerfile_mode_is_flag_only(organization):
    job, _ = create_hosted_job(
        organization, _payload(parallelism=4), idempotency_key="dockerfile"
    )
    register_attempt(
        job.id, endpoint_base_url="https://platform.example", snapshot_digest=None
    )
    job.refresh_from_db()
    assert "parallelism_clamped" not in job.payload["metadata"]


@override_settings(
    HARNESS_PARALLELISM_ENABLED=False,
    HARNESS_PARALLEL_SNAPSHOT_DIGESTS=["sha256:good"],
    ALK_DAYTONA_DOCKERFILE="",
)
@pytest.mark.django_db
def test_saved_w4_job_reruns_at_w1_and_clears_when_it_requalifies(organization):
    # A saved W=4 job registered while the flag is OFF clamps...
    job, _ = create_hosted_job(
        organization, _payload(parallelism=4), idempotency_key="rerun-eval"
    )
    register_attempt(job.id, endpoint_base_url="https://platform.example")
    job.refresh_from_db()
    assert job.payload["metadata"]["parallelism_clamped"] == {"requested": 4}

    # ...and the SAME saved job re-admits at W=4 (clamp cleared) once the flag +
    # digest qualify at the next register_attempt — the requested value was never
    # destroyed.
    with override_settings(HARNESS_PARALLELISM_ENABLED=True):
        register_attempt(
            job.id,
            endpoint_base_url="https://platform.example",
            snapshot_digest="sha256:good",
        )
    job.refresh_from_db()
    assert "parallelism_clamped" not in job.payload["metadata"]
    assert job.payload["runtime"]["parallelism"] == 4


def test_clamp_predicate_matrix():
    with override_settings(HARNESS_PARALLELISM_ENABLED=False):
        assert parallelism_w_gt_1_enabled("sha256:x") is False
    with override_settings(
        HARNESS_PARALLELISM_ENABLED=True,
        HARNESS_PARALLEL_SNAPSHOT_DIGESTS=["sha256:x"],
        ALK_DAYTONA_DOCKERFILE="",
    ):
        assert parallelism_w_gt_1_enabled("sha256:x") is True
        assert parallelism_w_gt_1_enabled("sha256:other") is False
        assert parallelism_w_gt_1_enabled("") is False
        assert clamp_parallelism(4, "sha256:x") == (4, False)
        assert clamp_parallelism(4, "") == (1, True)
        assert clamp_parallelism(1, "") == (1, False)


# ── Create-time serializer belt + surfacing (C4 §5/§6/§7) ───────────────────


def _create_data(parallelism=4, environment_values=None):
    source = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    if environment_values is not None:
        source["environment_values"] = environment_values
    return {
        "schema_version": "futureagi.harness-job.v1",
        "source": source,
        "agent": {"connector": "vapi", "config": {}, "secret_refs": {}},
        "scenario_count": 1,
        "runtime": {
            "isolation": "dedicated_vm",
            "cpu_units": 8,
            "memory_mb": 4096,
            "parallelism": parallelism,
            "concurrency_weight": 1,
            "max_duration_seconds": 600,
            "network_policy": "live",
        },
        "security": {"allowed_egress_domains": ["agent.example.com"]},
        "artifacts": {"level": "full"},
        "metadata": {},
    }


@override_settings(HARNESS_PARALLELISM_ENABLED=False, ALK_DAYTONA_SNAPSHOT_DIGEST="")
def test_serializer_clamps_not_rejects_and_preserves_requested():
    serializer = HarnessJobCreateSerializer(data=_create_data(parallelism=4))
    assert serializer.is_valid(), serializer.errors
    data = serializer.validated_data
    assert data["metadata"]["parallelism_clamped"] == {"requested": 4}
    # Requested value preserved so register_attempt re-evaluates honestly.
    assert data["runtime"]["parallelism"] == 4


@override_settings(
    HARNESS_PARALLELISM_ENABLED=True,
    HARNESS_PARALLEL_SNAPSHOT_DIGESTS=["sha256:good"],
    ALK_DAYTONA_SNAPSHOT_DIGEST="sha256:good",
    ALK_DAYTONA_DOCKERFILE="",
)
def test_serializer_admits_when_enabled():
    serializer = HarnessJobCreateSerializer(data=_create_data(parallelism=4))
    assert serializer.is_valid(), serializer.errors
    assert "parallelism_clamped" not in serializer.validated_data["metadata"]


@override_settings(HARNESS_PARALLELISM_ENABLED=True, ALK_DAYTONA_DOCKERFILE="d")
def test_serializer_scans_environment_values_loopback_at_w_gt_1():
    data = _create_data(
        parallelism=4,
        environment_values={
            "OK": "https://api.example.com",
            "LOCAL": "http://localhost:18090/x",
            "V6": "http://[::1]:9000",
        },
    )
    serializer = HarnessJobCreateSerializer(data=data)
    assert serializer.is_valid(), serializer.errors
    warnings = serializer.validated_data["metadata"]["parallelism_warnings"]
    assert warnings[0]["code"] == "literal_local_endpoint"
    assert warnings[0]["aliases"] == ["LOCAL", "V6"]


@override_settings(HARNESS_PARALLELISM_ENABLED=True, ALK_DAYTONA_DOCKERFILE="d")
def test_serializer_does_not_scan_at_w1():
    data = _create_data(
        parallelism=1,
        environment_values={"LOCAL": "http://127.0.0.1:5000"},
    )
    serializer = HarnessJobCreateSerializer(data=data)
    assert serializer.is_valid(), serializer.errors
    assert "parallelism_warnings" not in serializer.validated_data["metadata"]
    assert "parallelism_clamped" not in serializer.validated_data["metadata"]


def _create_data_with_secret_refs(environment_values, secret_refs):
    # github source so the env/secret alias-collision guard (not the remote
    # "must own credentials" rule) is what governs.
    return {
        "schema_version": "futureagi.harness-job.v1",
        "source": {
            "kind": "github",
            "repository": "acme/agent",
            "visibility": "public",
            "environment_values": environment_values,
        },
        "agent": {"connector": "vapi", "config": {}, "secret_refs": secret_refs},
        "scenario_count": 1,
        "runtime": {"parallelism": 1},
        "security": {"allowed_egress_domains": ["agent.example.com"]},
        "artifacts": {"level": "full"},
        "metadata": {},
    }


def _secret_ref(key="provider/token"):
    return {"manager": "platform-vault", "key": key, "purpose": "target_provider"}


def test_serializer_rejects_alias_in_both_env_values_and_secret_refs():
    # An alias present in BOTH the inline plaintext environment_values (Channel 1)
    # and agent.secret_refs is rejected at the top-level create serializer.
    data = _create_data_with_secret_refs(
        environment_values={"PROVIDER_KEY": "plaintext-value"},
        secret_refs={"PROVIDER_KEY": _secret_ref()},
    )
    serializer = HarnessJobCreateSerializer(data=data)
    assert not serializer.is_valid()
    assert "cannot be both uploaded and a secret reference" in str(serializer.errors)


def test_serializer_allows_distinct_env_and_secret_aliases():
    data = _create_data_with_secret_refs(
        environment_values={"PLAINTEXT_KEY": "plaintext-value"},
        secret_refs={"PROVIDER_KEY": _secret_ref()},
    )
    serializer = HarnessJobCreateSerializer(data=data)
    assert serializer.is_valid(), serializer.errors


# ── serialize_job runtime block + no-degrade default (C4 §6) ────────────────


def _preflight(payload):
    from types import SimpleNamespace

    from simulate.services.harness_provider import DaytonaHarnessProvider

    request = SimpleNamespace(validated_data=payload)
    return DaytonaHarnessProvider().preflight(request)


def test_preflight_advisory_reflects_flag_and_no_stale_echo():
    payload = _payload(parallelism=4)
    with override_settings(
        HARNESS_PARALLELISM_ENABLED=False, ALK_DAYTONA_SNAPSHOT_DIGEST=""
    ):
        resp = _preflight(payload)
        assert resp.data["parallelism_enabled"] is False
        # No stale echo: the advertised effective is the clamped value, not 4.
        assert resp.data["effective_parallelism"] == 1
    with override_settings(
        HARNESS_PARALLELISM_ENABLED=True,
        HARNESS_PARALLEL_SNAPSHOT_DIGESTS=["sha256:good"],
        ALK_DAYTONA_SNAPSHOT_DIGEST="sha256:good",
        ALK_DAYTONA_DOCKERFILE="",
    ):
        resp = _preflight(payload)
        assert resp.data["parallelism_enabled"] is True
        assert resp.data["effective_parallelism"] == 4


@pytest.mark.django_db
def test_serialize_job_exposes_runtime_and_default_effective(organization):
    job, _ = create_hosted_job(
        organization, _payload(parallelism=4), idempotency_key="runtime-block"
    )
    register_attempt(job.id, endpoint_base_url="https://platform.example")
    dto = serialize_job(job)
    assert dto["job"]["runtime"] == {"parallelism": 4, "cpu_units": 8}
    # No degrade event yet -> effective == requested.
    assert dto["parallelism"] == {
        "requested": 4,
        "effective": 4,
        "degrade_reasons": [],
    }
