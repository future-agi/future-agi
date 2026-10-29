"""Default-workspace lists must not fold in another tenant's null-workspace rows.

The manager path already scopes ``workspace__isnull`` (TH-3723). These tests
cover the view mixin, which applies its own Q clause before that manager runs.
Organization-scoped models are checked with ``request.organization`` unset:
the later organization filter would hide the unscoped null-workspace clause.
"""

import uuid

import pytest
from django.db import connection, models

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel
from tfc.utils.base_viewset import (
    BaseModelViewSetMixin,
    BaseModelViewSetMixinWithUserOrg,
)


def _table(name):
    return f"csr3667_{name}_{uuid.uuid4().hex[:8]}"


def _model(label, **fields):
    meta = type(
        "Meta",
        (),
        {"app_label": "tfc", "managed": False, "db_table": _table(label)},
    )
    attrs = {"__module__": __name__, "Meta": meta, **fields}
    return type(label, (BaseModel,), attrs)


def _org_workspace_row():
    return _model(
        "OrgWorkspaceRow",
        name=models.CharField(max_length=64),
        organization=models.ForeignKey(Organization, on_delete=models.CASCADE),
        workspace=models.ForeignKey(
            Workspace, on_delete=models.CASCADE, null=True, blank=True
        ),
    )


def _workspace_only_row():
    return _model(
        "WorkspaceOnlyRow",
        name=models.CharField(max_length=64),
        workspace=models.ForeignKey(
            Workspace, on_delete=models.CASCADE, null=True, blank=True
        ),
    )


def _project_pair():
    project = _model(
        "ProjectStub",
        name=models.CharField(max_length=64),
        organization=models.ForeignKey(Organization, on_delete=models.CASCADE),
        workspace=models.ForeignKey(
            Workspace, on_delete=models.CASCADE, null=True, blank=True
        ),
    )
    child = _model(
        "ProjectChildRow",
        name=models.CharField(max_length=64),
        project=models.ForeignKey(project, on_delete=models.CASCADE),
    )
    return project, child


def _create_tables(*models_):
    with connection.schema_editor() as editor:
        for model in models_:
            editor.create_model(model)


def _org(name):
    return Organization.objects.create(name=name)


def _user():
    return User.objects.create(email=f"csr3667-{uuid.uuid4().hex[:8]}@example.test")


def _workspace(org, user, *, default):
    return Workspace.objects.create(
        name=f"ws-{uuid.uuid4().hex[:6]}",
        organization=org,
        is_default=default,
        created_by=user,
    )


class _View:
    def __init__(self, mixin, model, workspace, organization=None):
        self._mixin = mixin
        self.queryset = model._base_manager.all()
        self.request = type(
            "Req", (), {"workspace": workspace, "organization": organization}
        )()

    def get_queryset(self):
        return self._mixin.get_queryset(self)


def _ids(qs):
    return set(qs.values_list("name", flat=True))


@pytest.mark.django_db
def test_default_workspace_null_rows_stay_in_their_org():
    user = _user()
    org_a = _org("csr3667-a")
    org_b = _org("csr3667-b")
    default_a = _workspace(org_a, user, default=True)
    _workspace(org_b, user, default=True)
    row = _org_workspace_row()
    _create_tables(row)
    row._base_manager.create(name="null-a", organization=org_a, workspace=None)
    row._base_manager.create(name="null-b", organization=org_b, workspace=None)
    row._base_manager.create(name="owned-a", organization=org_a, workspace=default_a)
    # organization left unset so the later org filter cannot hide the clause
    view = _View(BaseModelViewSetMixin, row, default_a)
    assert _ids(view.get_queryset()) == {"null-a", "owned-a"}
    other = _View(BaseModelViewSetMixinWithUserOrg, row, default_a)
    assert _ids(other.get_queryset()) == {"null-a", "owned-a"}


@pytest.mark.django_db
def test_workspace_only_model_does_not_include_null_workspace_rows():
    user = _user()
    org = _org("csr3667-only")
    default = _workspace(org, user, default=True)
    row = _workspace_only_row()
    _create_tables(row)
    row._base_manager.create(name="null-row", workspace=None)
    row._base_manager.create(name="owned", workspace=default)
    view = _View(BaseModelViewSetMixin, row, default)
    assert _ids(view.get_queryset()) == {"owned"}
    other = _View(BaseModelViewSetMixinWithUserOrg, row, default)
    assert _ids(other.get_queryset()) == {"owned"}


@pytest.mark.django_db
def test_non_default_workspace_stays_exact():
    user = _user()
    org = _org("csr3667-nondefault")
    other_ws = _workspace(org, user, default=False)
    row = _org_workspace_row()
    _create_tables(row)
    row._base_manager.create(name="null-row", organization=org, workspace=None)
    row._base_manager.create(name="owned", organization=org, workspace=other_ws)
    view = _View(BaseModelViewSetMixin, row, other_ws, org)
    assert _ids(view.get_queryset()) == {"owned"}


@pytest.mark.django_db
def test_project_workspace_null_rows_stay_in_their_org():
    user = _user()
    org_a = _org("csr3667-proj-a")
    org_b = _org("csr3667-proj-b")
    default_a = _workspace(org_a, user, default=True)
    _workspace(org_b, user, default=True)
    project, child = _project_pair()
    _create_tables(project, child)
    proj_a = project._base_manager.create(
        name="proj-a", organization=org_a, workspace=None
    )
    proj_b = project._base_manager.create(
        name="proj-b", organization=org_b, workspace=None
    )
    child._base_manager.create(name="child-a", project=proj_a)
    child._base_manager.create(name="child-b", project=proj_b)
    view = _View(BaseModelViewSetMixin, child, default_a)
    assert _ids(view.get_queryset()) == {"child-a"}
