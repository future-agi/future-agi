"""Rows for the webhooks Django tests. Secrets and URLs go through the real encryption (a fresh Fernet key per test)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from cryptography.fernet import Fernet

from webhooks import crypto
from webhooks.core.addresses import display_url
from webhooks.core.signing import new_secret, secret_tail
from webhooks.models import Endpoint, EndpointState, Secret, Subscription

T0 = datetime(2026, 10, 22, 10, 0, 0, tzinfo=UTC)


def use_test_keys(monkeypatch) -> None:
    monkeypatch.setenv("WEBHOOKS_ENCRYPTION_KEYS", Fernet.generate_key().decode())


def default_owner(organization):
    """The org's earliest active Owner or Admin member, or None: the owner an endpoint made with no ``owner`` gets.

    Every endpoint the API creates has an owner, and the ``webhooks-health`` pass runs the offboarding sweep, which
    turns an enabled endpoint with no owner off (``creator_removed``). Pass ``owner=None`` for an ownerless one."""
    from accounts.models.organization_membership import OrganizationMembership
    from tfc.constants.levels import Level

    return (
        OrganizationMembership.no_workspace_objects.filter(
            organization=organization,
            is_active=True,
            deleted=False,
            level__gte=Level.ADMIN,
            user__is_active=True,
        )
        .order_by("created_at", "id")
        .values_list("user_id", flat=True)
        .first()
    )


def make_endpoint(
    organization,
    workspace=None,
    *,
    url="https://hooks.acme.ai/omega",
    event_types=("prompt.*",),
    filters=None,
    secret=None,
    **fields,
) -> Endpoint:
    if "owner" not in fields and "owner_id" not in fields:
        fields["owner_id"] = default_owner(organization)
    ep = Endpoint.objects.create(
        organization=organization,
        workspace=workspace,
        name=fields.pop("name", f"ep-{Endpoint.objects.count() + 1}"),
        url_encrypted=crypto.encrypt(url),
        url_host=url.split("/")[2],
        url_display=display_url(url),
        url_sha256=hashlib.sha256(url.encode()).hexdigest(),
        **fields,
    )
    EndpointState.objects.create(endpoint=ep, organization=organization)
    s = secret or new_secret()
    Secret.objects.create(
        organization=organization,
        endpoint=ep,
        secret_encrypted=crypto.encrypt(s),
        tail=secret_tail(s),
        state="active",
    )
    for t in event_types:
        Subscription.objects.create(
            organization=organization, endpoint=ep, event_type=t, filters=filters or {}
        )
    return ep


def label_event_data(prompt_id="p1", label="Production", action="moved") -> dict:
    return {
        "action": action,
        "cause": "label_change",
        "prompt": {"id": prompt_id, "name": "support-reply"},
        "label": {"id": "l1", "name": label, "type": "system"},
        "revision": 1,
    }
