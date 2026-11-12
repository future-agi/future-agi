"""F-CENTRAL companion probes; no repository-wide safety is inferred from N01-N14.

These require the empty-scope contract, even when a queryset predates auth.
They are deliberately not xfailed: a red goes to the bounded companion fix.
"""

from types import SimpleNamespace

import pytest
from rest_framework import serializers

from accounts.tests.test_workspace_landing import landing as _landing_fixture
from model_hub.models.develop_dataset import Dataset, Row
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
    workspace_context,
    workspace_scope_is_empty,
)
from tfc.utils.tenant_scope import scope_to_caller, set_related_queryset

landing = _landing_fixture


@pytest.fixture(autouse=True)
def clean_context():
    clear_workspace_context()
    yield
    clear_workspace_context()


@pytest.mark.parametrize("related", [False, True])
@pytest.mark.parametrize("many", [False, True])
def test_prebuilt_central_scope_and_serializer_refuse_authenticated_empty(
    landing, related, many
):
    dataset = Dataset.objects.create(
        name="Prebuilt central target",
        organization=landing.org,
        workspace=landing.other,
    )
    row = Row.objects.create(dataset=dataset, order=0)
    target = row if related else dataset
    rows = type(target).no_workspace_objects.filter(pk=target.pk)
    assert rows.exists()
    org_field = "dataset__organization" if related else "organization"
    ws_field = "dataset__workspace" if related else "workspace"
    request = SimpleNamespace(
        user=landing.user, organization=landing.org, workspace=None
    )
    field = serializers.PrimaryKeyRelatedField(queryset=rows, many=many)
    supplied = [str(target.pk)] if many else str(target.pk)
    with workspace_context(None, landing.org, landing.user):
        set_workspace_context(require_workspace=True)
        assert workspace_scope_is_empty()
        scoped = scope_to_caller(
            rows, request, organization_field=org_field, workspace_field=ws_field
        )
        set_related_queryset(field, scoped)
        assert not scoped.exists()
        with pytest.raises(serializers.ValidationError):
            field.run_validation(supplied)
        assert type(target)._base_manager.filter(pk=target.pk, deleted=False).exists()
    with workspace_context(landing.other, landing.org, landing.user):
        set_workspace_context(require_workspace=True)
        request.workspace = landing.other
        scoped = scope_to_caller(
            rows, request, organization_field=org_field, workspace_field=ws_field
        )
        set_related_queryset(field, scoped)
        assert list(scoped) == [target]
        assert field.run_validation(supplied) == ([target] if many else target)
