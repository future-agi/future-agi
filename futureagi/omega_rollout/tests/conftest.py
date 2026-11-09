"""Tenants for the rollout tests (READINESS 3.13): A (admitted to the product under test), B (not
admitted), and A2, a second workspace of org A with a workspace-level deny. Plus a staff operator
holding `operate_rollout`, a staff reader without it, and an ordinary member."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import Permission

from accounts.models.workspace import Workspace, WorkspaceMembership
from omega_hub.tests.helpers import make_person, session_client
from omega_rollout import ops
from omega_rollout import products as p
from tfc.constants.levels import Level

WORKSPACE_CONTEXT_MIDDLEWARE = (
    "tfc.middleware.workspace_context.WorkspaceContextMiddleware"
)


@pytest.fixture(autouse=True)
def request_scoped_workspace_context(settings):
    # tfc.settings.test drops this middleware, which lets one test request's workspace filter the next
    # one's queries; production runs it (see omega_hub/tests/conftest.py).
    if WORKSPACE_CONTEXT_MIDDLEWARE not in settings.MIDDLEWARE:
        settings.MIDDLEWARE = [*settings.MIDDLEWARE, WORKSPACE_CONTEXT_MIDDLEWARE]
    yield settings


class Tenants:
    pass


def key_client(api_key, secret_key):
    """A client that signs in with an org API key pair, as an SDK or a script does."""
    from rest_framework.test import APIClient

    client = APIClient()
    client.credentials(HTTP_X_API_KEY=api_key, HTTP_X_SECRET_KEY=secret_key)
    return client


def client_for(person, workspace=None):
    client = session_client(person)
    if workspace is not None:
        client.credentials(
            **{
                **client._credentials,
                "HTTP_X_WORKSPACE_ID": str(workspace.pk),
            }
        )
    return client


@pytest.fixture
def tenants(db):
    t = Tenants()
    t.operator = make_person("rollout-operator", org_level=Level.OWNER)
    t.operator.user.is_staff = True
    t.operator.user.save(update_fields=["is_staff"])
    t.operator.user.user_permissions.add(
        Permission.objects.get(
            content_type__app_label="omega_rollout", codename="operate_rollout"
        )
    )
    t.reader = make_person("rollout-reader", org_level=Level.OWNER)
    t.reader.user.is_staff = True
    t.reader.user.save(update_fields=["is_staff"])
    t.a = make_person("tenant-a", org_level=Level.OWNER)
    t.b = make_person("tenant-b", org_level=Level.OWNER)
    t.a2 = Workspace.objects.create(
        name="A2",
        organization=t.a.organization,
        is_default=False,
        is_active=True,
        created_by=t.a.user,
    )
    from accounts.models.organization_membership import OrganizationMembership

    membership = OrganizationMembership.no_workspace_objects.get(
        user=t.a.user, organization=t.a.organization
    )
    WorkspaceMembership.no_workspace_objects.get_or_create(
        user=t.a.user,
        workspace=t.a2,
        defaults={
            "role": "Workspace Admin",
            "level": Level.WORKSPACE_ADMIN,
            "is_active": True,
            "organization_membership": membership,
        },
    )
    t.member_b = make_person(
        "member-b", org_level=Level.MEMBER, organization=t.b.organization
    )
    return t


def admit(tenants, product, *, stage=p.S1, deny_a2=True):
    """Put `product` at `stage`, admit org A as a partner, and deny A's second workspace A2."""
    actor = tenants.operator.user
    ops.set_stage(product, stage, actor=actor, reason="test")
    ops.set_access(
        product,
        tenants.a.organization,
        effect=p.EFFECT_ALLOW,
        cohort=p.COHORT_PARTNER,
        actor=actor,
        reason="test partner",
    )
    if deny_a2:
        ops.set_access(
            product,
            tenants.a.organization,
            effect=p.EFFECT_DENY,
            workspace=tenants.a2,
            actor=actor,
            reason="test workspace deny",
        )


@pytest.fixture
def admit_all(tenants):
    for product in p.HOSTED_PRODUCTS:
        admit(tenants, product)
    return tenants
