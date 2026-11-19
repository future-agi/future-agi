"""The existing Simulation creation callback must bind private ownership first."""

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from django.db import transaction
from django.utils import timezone

from model_hub.models import (
    HubSkillNativeEvidence,
    HubSkillSpendPeriod,
    HubSkillSpendPolicy,
    HubSkillSpendReservation,
)
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.packages import manifest_digest
from model_hub.services.skills_hub.r3.native_evidence import bind_native_evidence_locked
from model_hub.services.skills_hub.r3.native_reducer import planned_matrix
from model_hub.services.skills_hub.v11.types import SkillPrincipal, SkillScope
from simulate.services.hosted_harness import create_hosted_job
from simulate.services.skills_context_adapter import build_execution_spec

pytestmark = pytest.mark.django_db


def test_hosted_callback_rolls_back_without_shared_reservation(
    organization, workspace, user, hub_skill, make_skill_files, native_configuration
):
    principal = SkillPrincipal(
        SkillScope(organization.id, workspace.id), user.id, None, "human"
    )
    files = make_skill_files()
    snapshots: dict[str, dict | None] = {
        arm: {
            "skill_id": str(hub_skill.id),
            "digest": manifest_digest(files),
            "files": files,
        }
        for arm in ("candidate", "reference")
    }
    case: dict[str, Any] = {
        "id": str(uuid4()),
        "prompt": "Synthetic request",
        "fixtures": [],
    }
    spec = build_execution_spec(
        configuration=native_configuration,
        snapshots=snapshots,
        cases=[case],
        matrix=planned_matrix(
            case_ids=[case["id"]], baselines=["reference"], repetitions=1
        ),
        execution_fingerprint="sha256:" + "c" * 64,
    )
    payload = {
        "runtime": {"max_duration_seconds": 300},
        "scenario_count": 2,
        "artifacts": {"level": "full", "max_artifact_bytes": 1024},
        "skills_context": spec.guest_payload(),
    }

    def bind_without_reservation(job):
        bind_native_evidence_locked(
            principal=principal,
            resource_id=hub_skill.id,
            kind="skill",
            job=job,
            spec=spec,
            payload_expires_at=timezone.now() + timedelta(days=1),
            reservation_id=uuid4(),
        )

    with pytest.raises(SkillsHubError) as error:
        create_hosted_job(
            organization,
            payload,
            workspace=workspace,
            idempotency_key=str(uuid4()),
            on_create=bind_without_reservation,
        )
    assert error.value.code == "skills_unavailable"
    assert not HubSkillNativeEvidence.objects.exists()
    policy = HubSkillSpendPolicy.objects.create(
        organization=organization,
        workspace=workspace,
        enabled=True,
        period_limit_usd=Decimal("2.00"),
        updated_by=user,
    )
    period = HubSkillSpendPeriod.objects.create(
        organization=organization,
        workspace=workspace,
        policy=policy,
        starts_at=timezone.now(),
        ends_at=timezone.now() + timedelta(days=1),
    )

    def bind_with_reserved_admission(job):
        # This is a synthetic authority record. Production admission is L5's
        # shared reservation service, not a second balance in this adapter.
        reservation = HubSkillSpendReservation.objects.create(
            organization=organization,
            workspace=workspace,
            period=period,
            operation_id=uuid4(),
            source="simulation_native",
            source_id=job.pk,
            kind="admission",
            reserved_usd=Decimal("1.00"),
        )
        bind_native_evidence_locked(
            principal=principal,
            resource_id=hub_skill.id,
            kind="skill",
            job=job,
            spec=spec,
            payload_expires_at=timezone.now() + timedelta(days=1),
            reservation_id=reservation.id,
        )

    operation_key = str(uuid4())
    job, created = create_hosted_job(
        organization,
        payload,
        workspace=workspace,
        idempotency_key=operation_key,
        on_create=bind_with_reserved_admission,
    )
    assert created is True
    evidence = HubSkillNativeEvidence.objects.get(simulation_job=job)
    assert evidence.visibility == "private"
    assert evidence.owner_id == user.pk
    assert evidence.planned_matrix == spec.matrix
    replay, created = create_hosted_job(
        organization,
        payload,
        workspace=workspace,
        idempotency_key=operation_key,
        on_create=bind_with_reserved_admission,
    )
    assert replay.pk == job.pk
    assert created is False
    assert HubSkillSpendReservation.objects.count() == 1
    assert HubSkillNativeEvidence.objects.count() == 1
    job.current_attempt_number = 1
    job.save(update_fields=["current_attempt_number"])
    with transaction.atomic(), pytest.raises(SkillsHubError) as error:
        bind_without_reservation(job)
    assert error.value.code == "runtime_incompatible"
