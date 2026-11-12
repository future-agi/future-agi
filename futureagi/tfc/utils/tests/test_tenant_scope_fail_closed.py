"""Cloud-only DB regressions for caller scope, including three model apps.

The census has caller code in only model_hub and tracer. These real callers
resolve agent_playground graph versions, model_hub prompt versions and tracer
configs; none of their querysets or authorization methods is mocked.
"""

import ipaddress
import socket
from types import SimpleNamespace

import pytest

from accounts.authentication import APIKeyAuthentication
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from agent_playground.models.graph import Graph
from agent_playground.models.graph_version import GraphVersion
from model_hub.models.ai_model import AIModel
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)
from tfc.utils.tenant_scope import scope_to_caller
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.project import Project

pytestmark = pytest.mark.django_db
CALLERS = ("graph_versions", "prompt_versions", "eval_configs")


@pytest.fixture(autouse=True)
def isolated_scope(monkeypatch):
    """No provider traffic: only Unix/loopback infrastructure may connect."""
    original = socket.socket.connect
    original_ex = socket.socket.connect_ex

    def check(sock, address):
        if sock.family == socket.AF_UNIX:
            return
        host = address[0]
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = host == "localhost"
        if not local:
            raise AssertionError("Non-loopback network access is forbidden")

    def connect(sock, address):
        check(sock, address)
        return original(sock, address)

    def connect_ex(sock, address):
        check(sock, address)
        return original_ex(sock, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    clear_workspace_context()
    yield
    clear_workspace_context()


@pytest.fixture
def tenants(db, isolated_scope):
    org = Organization.objects.create(name="Fail closed home")
    foreign_org = Organization.objects.create(name="Fail closed foreign")
    user = User.objects.create_user(
        email="scope-closed@example.test",
        password="test-password",
        name="Scope member",
        organization=org,
        organization_role="Member",
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user, organization=org, role="Member", level=Level.MEMBER
    )
    foreign_user = User.objects.create_user(
        email="scope-foreign@example.test",
        password="test-password",
        name="Foreign member",
        organization=foreign_org,
        organization_role="Member",
    )
    OrganizationMembership.no_workspace_objects.create(
        user=foreign_user, organization=foreign_org, role="Member", level=Level.MEMBER
    )
    workspaces = {}
    for label, organization, default in (
        ("a", org, False),
        ("b", org, False),
        ("default", org, True),
        ("foreign", foreign_org, False),
    ):
        workspaces[label] = Workspace.no_workspace_objects.create(
            name=label,
            organization=organization,
            created_by=foreign_user if label == "foreign" else user,
            is_default=default,
        )
    for label in ("a", "default"):
        WorkspaceMembership.no_workspace_objects.create(
            user=user,
            workspace=workspaces[label],
            organization_membership=membership,
            role="Workspace Member",
            level=Level.WORKSPACE_MEMBER,
        )
    template = EvalTemplate.no_workspace_objects.create(name="Scope system template")
    rows = {}
    for label in ("a", "b", "default", "foreign", "legacy"):
        workspace = workspaces.get(label)
        organization = foreign_org if label == "foreign" else org
        graph = Graph.no_workspace_objects.create(
            name=label,
            organization=organization,
            workspace=workspace,
            created_by=foreign_user if label == "foreign" else user,
        )
        graph_version = GraphVersion.no_workspace_objects.create(
            graph=graph, version_number=1
        )
        prompt = PromptTemplate.no_workspace_objects.create(
            name=label, organization=organization, workspace=workspace
        )
        # A draft avoids committing a prompt/webhook as a fixture side effect.
        prompt_version = PromptVersion.no_workspace_objects.create(
            original_template=prompt, template_version="v1", is_draft=True
        )
        project = Project.no_workspace_objects.create(
            name=label,
            organization=organization,
            workspace=workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
        )
        config = CustomEvalConfig.no_workspace_objects.create(
            name=label, project=project, eval_template=template
        )
        rows[label] = {
            "graph_versions": graph_version,
            "prompt_versions": prompt_version,
            "eval_configs": config,
        }
    return SimpleNamespace(
        org=org,
        foreign_org=foreign_org,
        user=user,
        membership=membership,
        workspaces=workspaces,
        rows=rows,
    )


def request_for(tenants, workspace="a"):
    return SimpleNamespace(
        user=tenants.user,
        organization=tenants.org,
        workspace=tenants.workspaces.get(workspace),
    )


def caller_rows(caller, request):
    if caller == "graph_versions":
        from model_hub.views.experiments import _request_graph_versions

        return _request_graph_versions(request)
    if caller == "prompt_versions":
        from model_hub.views.experiments import _request_prompt_versions

        return _request_prompt_versions(request)
    from tracer.serializers.eval_task import caller_custom_eval_configs

    return caller_custom_eval_configs(request)


@pytest.mark.parametrize("caller", CALLERS)
def test_real_callers_without_workspace_return_no_rows(tenants, caller):
    WorkspaceMembership.no_workspace_objects.filter(user=tenants.user).update(
        is_active=False
    )
    request = request_for(tenants, None)
    assert tenants.user.can_access_organization(tenants.org)
    assert not caller_rows(caller, request).exists()


@pytest.mark.parametrize("caller", CALLERS)
def test_real_callers_keep_a_and_exclude_b_and_foreign_org(tenants, caller):
    request = request_for(tenants)
    assert set(caller_rows(caller, request)) == {tenants.rows["a"][caller]}


@pytest.mark.parametrize("caller", CALLERS)
def test_real_callers_preserve_default_and_legacy_rows(tenants, caller):
    request = request_for(tenants, "default")
    assert set(caller_rows(caller, request)) == {
        tenants.rows["default"][caller],
        tenants.rows["legacy"][caller],
    }


@pytest.mark.parametrize("caller", CALLERS)
@pytest.mark.parametrize(
    "state",
    (
        "inactive_workspace",
        "deleted_workspace",
        "foreign_workspace",
        "foreign_org",
        "deleted_workspace_membership",
        "inactive_workspace_membership",
        "deleted_org_membership",
    ),
)
def test_real_callers_recheck_dsx_access_after_authentication(tenants, caller, state):
    request = request_for(tenants)
    request.headers = {"X-Workspace-Id": str(request.workspace.pk)}
    request.GET = {}
    request.method = "GET"
    request.path = "/model-hub/experiments/"
    APIKeyAuthentication()._set_workspace_context(request, tenants.user)
    assert request.workspace.pk == tenants.workspaces["a"].pk
    assert caller_rows(caller, request).exists()
    if state == "inactive_workspace":
        Workspace.no_workspace_objects.filter(pk=request.workspace.pk).update(
            is_active=False
        )
    elif state == "deleted_workspace":
        Workspace.no_workspace_objects.filter(pk=request.workspace.pk).update(
            deleted=True
        )
    elif state == "foreign_workspace":
        request.workspace = tenants.workspaces["b"]
    elif state == "foreign_org":
        Workspace.no_workspace_objects.filter(pk=request.workspace.pk).update(
            organization=tenants.foreign_org
        )
    elif state == "deleted_org_membership":
        OrganizationMembership.no_workspace_objects.filter(
            pk=tenants.membership.pk
        ).update(deleted=True)
    else:
        changes = (
            {"deleted": True}
            if state == "deleted_workspace_membership"
            else {"is_active": False}
        )
        WorkspaceMembership.no_workspace_objects.filter(
            user=tenants.user, workspace=request.workspace
        ).update(**changes)
    assert not caller_rows(caller, request).exists()


@pytest.mark.parametrize("manager", ("no_workspace_objects", "_base_manager"))
def test_prebuilt_queryset_cannot_bypass_authenticated_empty_scope(tenants, manager):
    rows = getattr(Project, manager).all()
    assert rows.exists()
    request = request_for(tenants, None)
    set_workspace_context(
        workspace=None,
        organization=tenants.org,
        user=tenants.user,
        require_workspace=True,
    )
    assert not scope_to_caller(rows, request).exists()


def test_explicit_empty_request_cannot_borrow_ambient_workspace(tenants):
    request = request_for(tenants, None)
    set_workspace_context(
        workspace=tenants.workspaces["a"], organization=tenants.org, user=tenants.user
    )
    assert not scope_to_caller(Project._base_manager.all(), request).exists()


def test_context_only_caller_requires_live_user_access(tenants):
    set_workspace_context(
        workspace=tenants.workspaces["a"], organization=tenants.org, user=tenants.user
    )
    rows = Project._base_manager.all()
    assert set(scope_to_caller(rows).values_list("name", flat=True)) == {"a"}
    WorkspaceMembership.no_workspace_objects.filter(
        user=tenants.user, workspace=tenants.workspaces["a"]
    ).update(deleted=True)
    assert not scope_to_caller(rows).exists()


@pytest.mark.parametrize("role,level", (("Owner", Level.OWNER), ("Admin", Level.ADMIN)))
def test_global_workspace_roles_need_no_workspace_membership(tenants, role, level):
    OrganizationMembership.no_workspace_objects.filter(pk=tenants.membership.pk).update(
        role=role, level=level
    )
    request = request_for(tenants, "b")
    assert set(caller_rows("graph_versions", request)) == {
        tenants.rows["b"]["graph_versions"]
    }


def test_explicit_org_only_scope_preserves_memberships_without_workspace(tenants):
    # OrganizationMembership.organization has no workspace ownership path.
    request = request_for(tenants, None)
    OrganizationMembership.no_workspace_objects.create(
        user=tenants.user, organization=tenants.foreign_org, role="Member"
    )
    rows = OrganizationMembership.no_workspace_objects.all()
    assert set(scope_to_caller(rows, request, workspace_field=None)) == {
        tenants.membership
    }
    request.organization = None
    assert not scope_to_caller(rows, request, workspace_field=None).exists()


@pytest.mark.parametrize("caller", CALLERS)
def test_real_callers_without_organization_return_no_rows(tenants, caller):
    request = request_for(tenants)
    request.organization = None
    assert not caller_rows(caller, request).exists()
