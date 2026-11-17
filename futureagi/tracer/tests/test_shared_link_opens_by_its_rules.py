"""A shared link that has a workspace opens by its own rules.

These rows open ``/tracer/shared/<token>/``, and every link they open has a
workspace. Each viewer gets the answer the link's rules give: a public link is
served; a restricted link is served to an account whose e-mail it names and
refused (403 ``permission_denied``) to a signed-in account it neither names
nor was created by; a revoked link answers 410 ``gone``; an unknown token
answers 404 ``not_found``. The viewers are a signed-out viewer, a member
acting in the link's organization, a member of another organization, an
account with no organization, and an account whose organization has no
workspace it can land in. What such a link serves is read in the link's own
workspace, so every viewer it serves reads the same data: for a trace, a
call, a dashboard (with its widgets) and a project.

Every request takes the production request path (``MIDDLEWARE`` and
``REST_FRAMEWORK`` as production defines them), and every signed-in viewer
signs in through ``/accounts/token/`` (``tfc.utils.tests.production_path``),
so authentication alone decides each viewer's scope.
"""

import hashlib
import ipaddress
import itertools
import json
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import WorkspaceMembership
from model_hub.models.ai_model import AIModel
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.utils.tests.production_path import (
    PASSWORD,
    make_tenant,
    removed_member,
    sign_in,
    use_production_request_settings,
)
from tracer.models.dashboard import Dashboard, DashboardWidget
from tracer.models.project import Project
from tracer.models.shared_link import AccessType, SharedLink, SharedLinkAccess

KINDS = ("trace", "call", "dashboard", "project")
RESOURCE_TYPES = {
    "trace": "trace",
    "call": "call_execution",
    "dashboard": "dashboard",
    "project": "project",
}
# Signed in through the product, with no workspace scope: an account whose
# only membership was removed, and an active member of the link's
# organization who belongs to none of its workspaces.
WITHOUT_A_WORKSPACE = ("no-organization", "no-workspace")
EVERY_VIEWER = (
    "signed-out",
    "member",
    "other-organization",
    "no-organization",
    "no-workspace",
)
UNSHARED_NAME = "Unshared payroll project"

# Separate browsers, separate addresses (198.18.192.0/18 lies in the
# 198.18.0.0/15 test range).
_ADDRESSES = itertools.count(1)


def _address():
    return str(ipaddress.ip_address("198.18.192.0") + next(_ADDRESSES))


def _signed_out():
    return APIClient(enforce_csrf_checks=True, REMOTE_ADDR=_address())


def _member_with_a_workspace(tenant):
    """An active member of ``tenant`` with a membership in its workspace."""
    clear_workspace_context()
    user = User.objects.create_user(
        email=f"member-{uuid.uuid4().hex[:8]}@example.test",
        password=PASSWORD,
        name="Placed Member",
        organization=tenant.organization,
        organization_role="Member",
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=tenant.organization,
        role="Member",
        level=Level.MEMBER,
        is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=tenant.workspace,
        role="Workspace Member",
        level=Level.WORKSPACE_MEMBER,
        is_active=True,
        organization_membership=membership,
    )
    clear_workspace_context()
    return user


def _member_without_a_workspace(tenant):
    """An active member of ``tenant`` who belongs to none of its workspaces,
    so a signed-in request lands in no workspace."""
    clear_workspace_context()
    user = User.objects.create_user(
        email=f"unplaced-{uuid.uuid4().hex[:8]}@example.test",
        password=PASSWORD,
        name="Unplaced Member",
        organization=tenant.organization,
        organization_role="Member",
    )
    OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=tenant.organization,
        role="Member",
        level=Level.MEMBER,
        is_active=True,
    )
    clear_workspace_context()
    return user


def _collector_trace(project):
    """A trace whose root span only ClickHouse holds, as the collector
    writes it. Returns the trace id."""
    from tracer.tests._ch_seed import seed_ch_span

    trace_id = str(uuid.uuid4())
    started = timezone.now() - timedelta(seconds=5)
    seed_ch_span(
        SimpleNamespace(
            id=f"span_{uuid.uuid4().hex[:16]}",
            trace_id=trace_id,
            project_id=project.id,
            project=project,
            parent_span_id=None,
            name="checkout root",
            observation_type="agent",
            status="OK",
            start_time=started,
            end_time=timezone.now(),
            latency_ms=5000,
            input={"prompt": "Where is my order?"},
            output={"response": "It ships today."},
            metadata={"source": "collector"},
            tags=["checkout"],
            created_at=started,
            updated_at=started,
        )
    )
    return trace_id


def _chat_call(tenant):
    """A text call of a finished simulation run in ``tenant``'s workspace."""
    from model_hub.models.choices import StatusType
    from simulate.models import AgentDefinition, Scenarios
    from simulate.models.chat_message import ChatMessageModel
    from simulate.models.run_test import RunTest
    from simulate.models.simulator_agent import SimulatorAgent
    from simulate.models.test_execution import CallExecution, TestExecution

    clear_workspace_context()
    agent = AgentDefinition.objects.create(
        agent_name="Returns Desk Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+1230002222",
        inbound=True,
        description="Agent behind a shared call",
        organization=tenant.organization,
        workspace=tenant.workspace,
        languages=["en"],
    )
    simulator = SimulatorAgent.objects.create(
        name="Returns Desk Simulator",
        prompt="You are a test simulator.",
        voice_provider="elevenlabs",
        voice_name="marissa",
        model="gpt-4",
        organization=tenant.organization,
        workspace=tenant.workspace,
    )
    run = RunTest.objects.create(
        name="Returns Desk Run",
        agent_definition=agent,
        simulator_agent=simulator,
        organization=tenant.organization,
        workspace=tenant.workspace,
    )
    execution = TestExecution.objects.create(
        run_test=run,
        status=TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=1,
        completed_calls=1,
        simulator_agent=simulator,
        agent_definition=agent,
    )
    scenario = Scenarios.objects.create(
        name="Returns Desk Scenario",
        description="Scenario behind a shared call",
        source="Test source",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=tenant.organization,
        workspace=tenant.workspace,
        agent_definition=agent,
        status=StatusType.COMPLETED.value,
    )
    run.scenarios.add(scenario)
    call = CallExecution.objects.create(
        test_execution=execution,
        scenario=scenario,
        simulation_call_type=CallExecution.SimulationCallType.TEXT,
        status="completed",
        call_metadata={"harness_outcome_status": "failed"},
    )
    ChatMessageModel.objects.create(
        call_execution=call,
        role=ChatMessageModel.RoleChoices.USER,
        messages=["Where is my refund?"],
        content=[{"role": "user", "content": "Where is my refund?"}],
        session_id="returns-desk-session",
        organization=tenant.organization,
        workspace=tenant.workspace,
    )
    clear_workspace_context()
    return call


def _resources(tenant):
    """One resource of each kind in ``tenant``'s workspace, and a project
    beside them that no link names. Returns ``(ids, widget_ids)``."""
    clear_workspace_context()
    project = Project.no_workspace_objects.create(
        name="Checkout agent",
        organization=tenant.organization,
        workspace=tenant.workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    Project.no_workspace_objects.create(
        name=UNSHARED_NAME,
        organization=tenant.organization,
        workspace=tenant.workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    dashboard = Dashboard.no_workspace_objects.create(
        workspace=tenant.workspace,
        name="Checkout health",
        description="Latency of the checkout agent",
        created_by=tenant.user,
        updated_by=tenant.user,
    )
    widget = DashboardWidget.no_workspace_objects.create(
        dashboard=dashboard,
        name="Latency",
        position=0,
        width=6,
        height=4,
        query_config={
            "time_range": {"preset": "7D"},
            "metrics": [{"name": "latency", "type": "system_metric"}],
        },
        chart_config={"chart_type": "line"},
        created_by=tenant.user,
    )
    ids = {
        "trace": _collector_trace(project),
        "call": str(_chat_call(tenant).id),
        "dashboard": str(dashboard.id),
        "project": str(project.id),
    }
    clear_workspace_context()
    return ids, [str(widget.id)]


def _link(tenant, kind, resource_id, access_type, emails=(), revoked=False):
    clear_workspace_context()
    link = SharedLink.no_workspace_objects.create(
        resource_type=RESOURCE_TYPES[kind],
        resource_id=resource_id,
        access_type=access_type,
        created_by=tenant.user,
        organization=tenant.organization,
        workspace=tenant.workspace,
        is_active=not revoked,
    )
    for email in emails:
        SharedLinkAccess.no_workspace_objects.create(
            shared_link=link, email=email, granted_by=tenant.user
        )
    clear_workspace_context()
    return link


def _links(tenant, kind, resource_id, named):
    """The links of one resource: public, restricted to ``named``,
    restricted to an address no viewer has, and a revoked public one."""
    return SimpleNamespace(
        public=_link(tenant, kind, resource_id, AccessType.PUBLIC),
        named=_link(tenant, kind, resource_id, AccessType.RESTRICTED, named),
        unnamed=_link(
            tenant,
            kind,
            resource_id,
            AccessType.RESTRICTED,
            ["someone-else@example.test"],
        ),
        revoked=_link(tenant, kind, resource_id, AccessType.PUBLIC, revoked=True),
    )


def _url(link):
    return f"/tracer/shared/{link.token}/"


def _answer(response):
    """The facts of a resolve answer: its status and code, the id and widgets
    it serves, and a digest of the served data. The body is not kept."""
    body = response.json()
    data = body.get("data") if isinstance(body, dict) else None
    facts = SimpleNamespace(
        status=response.status_code,
        code=body.get("code") if isinstance(body, dict) else None,
        served=None,
        widgets=None,
        digest=None,
    )
    if isinstance(data, dict):
        if body.get("resource_type") == "trace":
            facts.served = (data.get("trace") or {}).get("id")
        else:
            facts.served = data.get("id")
        facts.widgets = [widget.get("id") for widget in data.get("widgets") or []]
        encoded = json.dumps(data, sort_keys=True, default=str).encode()
        facts.digest = hashlib.sha256(encoded).hexdigest()
    return facts


def _names(response, text):
    """Whether ``text`` appears anywhere in the answer."""
    return text in response.content.decode()


@pytest.fixture
def world(db, settings, monkeypatch):
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "true")
    use_production_request_settings(settings)
    link_tenant = make_tenant("Link")
    other_tenant = make_tenant("Other")
    former_tenant = make_tenant("Former")
    ids, widget_ids = _resources(link_tenant)
    no_organization, _ = removed_member(former_tenant, "Former")
    no_workspace = _member_without_a_workspace(link_tenant)
    member = _member_with_a_workspace(link_tenant)
    named = [no_organization.email, no_workspace.email, other_tenant.user.email]
    data = SimpleNamespace(
        ids=ids,
        widget_ids=widget_ids,
        links={
            kind: _links(link_tenant, kind, resource_id, named)
            for kind, resource_id in ids.items()
        },
        creator=sign_in(link_tenant.user.email, workspace=link_tenant.workspace),
        clients={
            "signed-out": _signed_out(),
            "member": sign_in(member.email, workspace=link_tenant.workspace),
            "other-organization": sign_in(
                other_tenant.user.email, workspace=other_tenant.workspace
            ),
            "no-organization": sign_in(no_organization.email),
            "no-workspace": sign_in(no_workspace.email),
        },
    )
    yield data
    clear_workspace_context()


def _served(world, kind, answer):
    """The link serves its own resource. A dashboard answers no id of its
    own, so its widgets name it."""
    assert answer.status == status.HTTP_200_OK
    if kind == "dashboard":
        assert answer.widgets == world.widget_ids
    else:
        assert answer.served == world.ids[kind]


WITHOUT_A_WORKSPACE_BY_KIND = [
    (viewer, kind) for viewer in WITHOUT_A_WORKSPACE for kind in KINDS
]
BY_KIND_IDS = [f"{viewer}-{kind}" for viewer, kind in WITHOUT_A_WORKSPACE_BY_KIND]


@pytest.mark.parametrize(
    ("viewer", "kind"), WITHOUT_A_WORKSPACE_BY_KIND, ids=BY_KIND_IDS
)
def test_a_public_link_opens_for_every_signed_in_account(world, viewer, kind):
    link = world.links[kind].public
    signed_out = _answer(world.clients["signed-out"].get(_url(link)))

    answer = _answer(world.clients[viewer].get(_url(link)))

    _served(world, kind, answer)
    assert answer.digest == signed_out.digest


@pytest.mark.parametrize(
    ("viewer", "kind"), WITHOUT_A_WORKSPACE_BY_KIND, ids=BY_KIND_IDS
)
def test_a_restricted_link_opens_for_the_account_it_names(world, viewer, kind):
    link = world.links[kind].named
    creator = _answer(world.creator.get(_url(link)))

    answer = _answer(world.clients[viewer].get(_url(link)))

    _served(world, kind, answer)
    assert answer.digest == creator.digest


@pytest.mark.parametrize("viewer", WITHOUT_A_WORKSPACE)
def test_a_restricted_link_refuses_an_account_it_does_not_name(world, viewer):
    answer = _answer(world.clients[viewer].get(_url(world.links["project"].unnamed)))

    assert answer.status == status.HTTP_403_FORBIDDEN
    assert answer.code == "permission_denied"
    assert answer.digest is None


@pytest.mark.parametrize("viewer", WITHOUT_A_WORKSPACE)
def test_a_revoked_link_answers_gone_to_every_signed_in_account(world, viewer):
    answer = _answer(world.clients[viewer].get(_url(world.links["project"].revoked)))

    assert answer.status == status.HTTP_410_GONE
    assert answer.code == "gone"
    assert answer.digest is None


@pytest.mark.parametrize("kind", KINDS)
def test_a_signed_out_viewer_opens_a_public_link(world, kind):
    answer = _answer(world.clients["signed-out"].get(_url(world.links[kind].public)))

    _served(world, kind, answer)


@pytest.mark.parametrize("kind", KINDS)
def test_a_member_acting_in_the_links_organization_opens_it(world, kind):
    link = world.links[kind].public
    signed_out = _answer(world.clients["signed-out"].get(_url(link)))

    answer = _answer(world.clients["member"].get(_url(link)))

    _served(world, kind, answer)
    assert answer.digest == signed_out.digest


@pytest.mark.parametrize("viewer", EVERY_VIEWER)
def test_an_unknown_token_is_not_found_for_every_viewer(world, viewer):
    answer = _answer(world.clients[viewer].get(f"/tracer/shared/{uuid.uuid4().hex}/"))

    assert answer.status == status.HTTP_404_NOT_FOUND
    assert answer.code == "not_found"


@pytest.mark.parametrize("kind", KINDS)
def test_a_member_of_another_organization_reads_only_what_the_link_serves(world, kind):
    links = world.links[kind]
    client = world.clients["other-organization"]
    signed_out = _answer(world.clients["signed-out"].get(_url(links.public)))
    creator = _answer(world.creator.get(_url(links.named)))

    public = client.get(_url(links.public))
    named = client.get(_url(links.named))
    unnamed = _answer(client.get(_url(links.unnamed)))
    revoked = _answer(client.get(_url(links.revoked)))

    _served(world, kind, _answer(public))
    assert _answer(public).digest == signed_out.digest
    assert not _names(public, UNSHARED_NAME)
    _served(world, kind, _answer(named))
    assert _answer(named).digest == creator.digest
    assert not _names(named, UNSHARED_NAME)
    assert unnamed.status == status.HTTP_403_FORBIDDEN
    assert unnamed.code == "permission_denied"
    assert revoked.status == status.HTTP_410_GONE
    assert revoked.code == "gone"


# A restricted link serves the account that created it, without naming its
# e-mail, while that account acts in the link's organization. Once the
# organization's owner deactivates or removes its membership there, the link
# serves it only when the link's access list names its e-mail.

CREATOR_LEAVES = {
    "deactivated": ("post", "/accounts/user/deactivate/"),
    "removed": ("delete", "/accounts/organization/members/remove/"),
}
LEFT_BY_KIND = [(how, kind) for how in CREATOR_LEAVES for kind in KINDS]
LEFT_BY_KIND_IDS = [f"{how}-{kind}" for how, kind in LEFT_BY_KIND]


@pytest.fixture
def creator_world(db, settings, monkeypatch):
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "true")
    use_production_request_settings(settings)
    tenant = make_tenant("Creator")
    ids, widget_ids = _resources(tenant)
    yield SimpleNamespace(tenant=tenant, ids=ids, widget_ids=widget_ids)
    clear_workspace_context()


def _created_link(tenant, creator, kind, resource_id, emails):
    """A restricted link that ``creator`` made, naming ``emails``."""
    clear_workspace_context()
    link = SharedLink.no_workspace_objects.create(
        resource_type=RESOURCE_TYPES[kind],
        resource_id=resource_id,
        access_type=AccessType.RESTRICTED,
        created_by=creator,
        organization=tenant.organization,
        workspace=tenant.workspace,
    )
    for email in emails:
        SharedLinkAccess.no_workspace_objects.create(
            shared_link=link, email=email, granted_by=creator
        )
    clear_workspace_context()
    return link


def _leaves(tenant, creator, how):
    """The organization's owner deactivates or removes ``creator``'s
    membership through the product."""
    owner = sign_in(tenant.user.email, workspace=tenant.workspace)
    verb, url = CREATOR_LEAVES[how]
    left = getattr(owner, verb)(url, {"user_id": str(creator.id)}, format="json")
    assert left.status_code == status.HTTP_200_OK
    clear_workspace_context()
    membership = OrganizationMembership._base_manager.get(
        user=creator, organization=tenant.organization
    )
    assert membership.is_active is False
    assert (membership.removed_at is not None) == (how == "removed")


def _creator_opens(world, kind, state, names_creator=False):
    """A member of the link's organization makes a restricted link to the
    ``kind`` resource and opens it, signed in through the product: while it
    acts in the organization (``member``), or after its membership there is
    ``deactivated`` or ``removed``. The link names the creator's e-mail only
    when ``names_creator`` is true."""
    tenant = world.tenant
    creator = _member_with_a_workspace(tenant)
    emails = [creator.email] if names_creator else ["someone-else@example.test"]
    link = _created_link(tenant, creator, kind, world.ids[kind], emails)
    if state == "member":
        client = sign_in(creator.email, workspace=tenant.workspace)
    else:
        _leaves(tenant, creator, state)
        client = sign_in(creator.email)
    return _answer(client.get(_url(link)))


@pytest.mark.parametrize("kind", KINDS)
def test_a_restricted_link_opens_for_its_creator_acting_in_its_organization(
    creator_world, kind
):
    answer = _creator_opens(creator_world, kind, "member")

    _served(creator_world, kind, answer)


@pytest.mark.parametrize(("how", "kind"), LEFT_BY_KIND, ids=LEFT_BY_KIND_IDS)
def test_a_restricted_link_refuses_its_creator_once_they_leave_its_organization(
    creator_world, how, kind
):
    answer = _creator_opens(creator_world, kind, how)

    assert answer.status == status.HTTP_403_FORBIDDEN
    assert answer.code == "permission_denied"
    assert answer.digest is None


@pytest.mark.parametrize("kind", KINDS)
def test_a_restricted_link_naming_its_creator_opens_after_they_leave(
    creator_world, kind
):
    answer = _creator_opens(creator_world, kind, "deactivated", names_creator=True)

    _served(creator_world, kind, answer)
