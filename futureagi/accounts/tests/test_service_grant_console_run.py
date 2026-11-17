"""A console-run grant is a registered person grant carrying the models the
Console run may call (fe1c field 20). Its row stores them, so the row
re-derives the exact token, revoking the person revokes it, and no other kind
may carry models."""

import uuid

import pytest
from django.db import IntegrityError, transaction

from accounts.models.service_grant import ServiceGrant
from accounts.services.envelope_keys import pair_keys
from accounts.services.service_grants import issue_person_grant, revoke_person_grants
from tfc.service_envelope import EnvelopeRejected, derive_grant, verify_grant

pytestmark = pytest.mark.django_db

RUN = "chat:44444444-4444-4444-8444-444444444444"
MODELS = ["gpt-4o-mini", "anthropic/claude-sonnet-5.5"]


def mint(user, workspace, **overrides):
    data = {
        "kind": "console-run",
        "org": user.organization_id,
        "user": user.pk,
        "workspace": workspace.pk,
        "sub": RUN,
        "ttl": 3600,
        "models": MODELS,
    }
    data.update(overrides)
    with transaction.atomic():
        return issue_person_grant(**data)


def test_a_console_run_grant_is_registered_with_its_models(user, workspace):
    token, claims, row = mint(user, workspace)
    assert token.startswith("fe1c.")
    assert row.kind == "console-run"
    assert row.model_ids == "anthropic/claude-sonnet-5.5,gpt-4o-mini"
    stored = ServiceGrant.objects.get(pk=claims["id"])
    keys = pair_keys("fa", "fa-eval-grant")
    assert derive_grant(stored.registry_row(), keys=keys) == token
    got = verify_grant(
        token,
        keys=keys,
        audience="fa-eval-grant",
        row=stored.registry_row(),
        now=int(claims["iat"]) + 1,
    )
    assert got == claims


def test_revoking_the_person_revokes_the_console_run_grant(user, workspace):
    token, claims, _ = mint(user, workspace)
    revoke_person_grants(user.pk, user.organization_id)
    stored = ServiceGrant.objects.get(pk=claims["id"])
    assert stored.revoked_at is not None
    with pytest.raises(EnvelopeRejected):
        verify_grant(
            token,
            keys=pair_keys("fa", "fa-eval-grant"),
            audience="fa-eval-grant",
            row=stored.registry_row(),
            now=int(claims["iat"]) + 1,
        )


def test_a_console_run_grant_without_models_is_refused(user, workspace):
    with pytest.raises(EnvelopeRejected):
        mint(user, workspace, models=None)
    assert not ServiceGrant.objects.filter(kind="console-run").exists()


def test_only_a_console_run_row_carries_models(user, workspace):
    _, _, row = mint(user, workspace)
    with pytest.raises(IntegrityError), transaction.atomic():
        ServiceGrant.objects.filter(pk=row.pk).update(model_ids="")
    with transaction.atomic():
        _, _, batch = issue_person_grant(
            kind="batch",
            org=user.organization_id,
            user=user.pk,
            workspace=workspace.pk,
            sub="batch:" + str(uuid.uuid4()),
            ttl=600,
        )
    assert batch.model_ids == ""
    assert "models" not in batch.registry_row()
    with pytest.raises(IntegrityError), transaction.atomic():
        ServiceGrant.objects.filter(pk=batch.pk).update(model_ids="gpt-4o")
