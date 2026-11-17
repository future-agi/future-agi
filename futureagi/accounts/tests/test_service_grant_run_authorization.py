"""A console-run run authorization is a registered person grant naming the
models and the agent service deployment (fe1r). Its row stores both, so the
row re-derives the exact token; revoking the person revokes it; no other kind
may carry a deployment."""

import uuid

import pytest
from django.db import IntegrityError, transaction

from accounts.models.service_grant import ServiceGrant
from accounts.services.envelope_keys import pair_keys
from accounts.services.service_grants import issue_person_grant, revoke_person_grants
from tfc.service_envelope import EnvelopeRejected, derive_grant, verify_grant

pytestmark = pytest.mark.django_db

RUN = "chat:66666666-6666-4666-8666-666666666666"


def authorize(user, workspace, **overrides):
    data = {
        "kind": "console-run-auth",
        "org": user.organization_id,
        "user": user.pk,
        "workspace": workspace.pk,
        "sub": RUN,
        "ttl": 43200,
        "models": ["gpt-4o-mini"],
        "ee": "ee-prod-1",
    }
    data.update(overrides)
    with transaction.atomic():
        return issue_person_grant(**data)


def test_the_row_carries_models_and_deployment(user, workspace):
    token, claims, row = authorize(user, workspace)
    assert token.startswith("fe1r.")
    assert (row.kind, row.model_ids, row.ee) == (
        "console-run-auth",
        "gpt-4o-mini",
        "ee-prod-1",
    )
    stored = ServiceGrant.objects.get(pk=claims["id"])
    keys = pair_keys("fa", "fa-console-run-signer")
    assert derive_grant(stored.registry_row(), keys=keys) == token
    got = verify_grant(
        token,
        keys=keys,
        audience="fa-console-run-signer",
        row=stored.registry_row(),
        now=int(claims["iat"]) + 1,
    )
    assert got == claims


def test_revoking_the_person_revokes_the_authorization(user, workspace):
    token, claims, _ = authorize(user, workspace)
    revoke_person_grants(user.pk, user.organization_id)
    stored = ServiceGrant.objects.get(pk=claims["id"])
    assert stored.revoked_at is not None
    with pytest.raises(EnvelopeRejected):
        verify_grant(
            token,
            keys=pair_keys("fa", "fa-console-run-signer"),
            audience="fa-console-run-signer",
            row=stored.registry_row(),
            now=int(claims["iat"]) + 1,
        )


def test_only_a_run_authorization_row_carries_a_deployment(user, workspace):
    _, _, row = authorize(user, workspace)
    with pytest.raises(IntegrityError), transaction.atomic():
        ServiceGrant.objects.filter(pk=row.pk).update(ee="")
    with transaction.atomic():
        _, _, grant = issue_person_grant(
            kind="console-run",
            org=user.organization_id,
            user=user.pk,
            workspace=workspace.pk,
            sub="chat:" + str(uuid.uuid4()),
            ttl=3600,
            models=["gpt-4o-mini"],
        )
    assert grant.ee == ""
    assert "ee" not in grant.registry_row()
    with pytest.raises(IntegrityError), transaction.atomic():
        ServiceGrant.objects.filter(pk=grant.pk).update(ee="ee-prod-1")
