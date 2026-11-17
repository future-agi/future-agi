"""A null workspace is org-wide in the webhooks tables (DESIGN.md sections 4.1, 4.5 and 4.9). FA's global save
signals (``tfc.utils.signals.auto_assign_workspace`` / ``ensure_workspace_after_save``) fill a null ``workspace`` from
the request's workspace context; inside any request that would silently turn an org-wide endpoint into a workspace
endpoint, the org's policy into a workspace policy, and an org-level event into a workspace event. Found by WH-1b's
retention test (the org's 7-day policy was not found); these models opt out."""

from __future__ import annotations

import pytest
from django.db import transaction

from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)
from webhooks.models import Endpoint, Event, Policy
from webhooks.publish import publish
from webhooks.tests.factories import T0, label_event_data, make_endpoint, use_test_keys


@pytest.mark.django_db(transaction=True)
def test_org_wide_rows_keep_a_null_workspace_inside_a_workspace_context(
    organization, workspace, monkeypatch
):
    use_test_keys(monkeypatch)
    set_workspace_context(workspace=workspace, organization=organization)
    try:
        ep = make_endpoint(organization, None)
        pol = Policy.objects.create(organization=organization, log_retention_days=7)
        with transaction.atomic():
            res = publish(
                type="prompt.label.changed",
                organization_id=organization.id,
                workspace_id=None,
                data=label_event_data(),
                source_key="k1",
                source_hash="h",
                producer="test",
                occurred_at=T0,
            )
    finally:
        clear_workspace_context()
    assert Endpoint.objects.get(id=ep.id).workspace_id is None
    assert Policy.objects.get(id=pol.id).workspace_id is None
    assert Event.objects.get(id=res.event_id).workspace_id is None
    # The workspace-scoped rows are untouched by this: an explicit workspace is kept as given.
    ws_ep = make_endpoint(organization, workspace)
    assert Endpoint.objects.get(id=ws_ep.id).workspace_id == workspace.id
