"""The hidden simulation project stays out of the project managers."""

import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.db import IntegrityError, transaction
from rest_framework import status

from accounts.models.workspace import Workspace
from ai_tools.base import ToolContext
from ai_tools.registry import registry
from model_hub.models.ai_model import AIModel
from simulate.models.agent_definition import AgentDefinition
from simulate.models.run_test import RunTest
from tfc.utils.error_codes import get_error_message
from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion
from tracer.models.trace import Trace
from tracer.serializers.observation_span import SpanReferenceQuerySerializer
from tracer.serializers.trace import (
    TraceDetailQuerySerializer,
    TraceVoiceCallDetailQuerySerializer,
)
from tracer.services.simulation_project import (
    RESERVED_PROJECT_NAME_MESSAGE,
    ReservedProjectNameError,
    ensure_simulation_project,
    simulation_project_name,
)
from tracer.tests.test_detail_identity_collision import (
    STARTED,
    _copy,
    _PhysicalCopies,
)
from tracer.tests.test_simulation_project import (
    NOT_RESERVED_NAMES,
    RESERVED_NAME,
    _agent_without_context,
    _other_organization,
    _run,
)
from tracer.utils.otel import get_or_create_project

pytestmark = pytest.mark.django_db


def _agent(organization, workspace, name):
    return AgentDefinition.objects.create(
        agent_name=name,
        description=f"{name} description",
        agent_type="text",
        inbound=True,
        organization=organization,
        workspace=workspace,
    )


def _project(manager, organization, workspace, name, deleted=False, **extra):
    return manager.create(
        name=name,
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
        deleted=deleted,
        **extra,
    )


def _four_projects(organization, workspace):
    visible_live = _project(Project.objects, organization, workspace, "visible live")
    visible_deleted = _project(
        Project.objects, organization, workspace, "visible deleted", deleted=True
    )
    hidden_live = _project(
        Project.with_simulation,
        organization,
        workspace,
        "hidden live",
        simulation_agent=_agent(organization, workspace, "live agent"),
    )
    hidden_deleted = _project(
        Project.with_simulation,
        organization,
        workspace,
        "hidden deleted",
        deleted=True,
        simulation_agent=_agent(organization, workspace, "deleted agent"),
    )
    return visible_live, visible_deleted, hidden_live, hidden_deleted


@pytest.mark.parametrize("manager", ["objects", "all_objects", "no_workspace_objects"])
def test_the_three_managers_exclude_it_and_with_simulation_includes_it(
    manager, organization, workspace
):
    visible_live, visible_deleted, hidden_live, hidden_deleted = _four_projects(
        organization, workspace
    )
    expected = {
        "objects": {visible_live.id},
        "no_workspace_objects": {visible_live.id},
        "all_objects": {visible_live.id, visible_deleted.id},
    }[manager]

    returned = set(
        getattr(Project, manager)
        .filter(organization=organization)
        .values_list("id", flat=True)
    )

    assert returned == expected
    assert not returned & {hidden_live.id, hidden_deleted.id}
    assert set(
        Project.with_simulation.filter(organization=organization).values_list(
            "id", flat=True
        )
    ) == {visible_live.id, visible_deleted.id, hidden_live.id, hidden_deleted.id}
    assert '"simulation_agent_id" IS NULL' in str(getattr(Project, manager).all().query)
    assert '"simulation_agent_id" IS NULL' not in str(
        Project.with_simulation.all().query
    )
    assert "JOIN" not in str(Project.no_workspace_objects.all().query)
    assert Project._meta.default_manager_name == "objects"
    assert Project._default_manager.name == "objects"
    assert [m.name for m in Project._meta.managers] == [
        "objects",
        "all_objects",
        "no_workspace_objects",
        "with_simulation",
    ]


def test_forward_foreign_key_still_reaches_it(organization, workspace):
    agent = _agent(organization, workspace, "forward agent")
    hidden = _project(
        Project.with_simulation,
        organization,
        workspace,
        "forward hidden",
        simulation_agent=agent,
    )
    version = ProjectVersion.objects.create(
        project=hidden, name="Run", version="v1", metadata={}
    )
    trace = Trace.objects.create(
        project=hidden,
        project_version=version,
        name="Trace",
        metadata={},
        input={"prompt": "hi"},
        output={"response": "hello"},
    )

    assert ProjectVersion.no_workspace_objects.get(pk=version.pk).project == hidden
    assert Trace.no_workspace_objects.get(pk=trace.pk).project == hidden
    assert AgentDefinition._base_manager.get(pk=agent.pk).simulation_project == hidden


def test_save_full_clean_does_not_see_a_hidden_name_but_the_database_does(
    organization, workspace
):
    hidden = _project(
        Project.with_simulation,
        organization,
        workspace,
        "taken by a hidden project",
        simulation_agent=_agent(organization, workspace, "name agent"),
    )
    twin = Project(
        name=hidden.name,
        trace_type=hidden.trace_type,
        organization=hidden.organization,
        workspace=hidden.workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
    )

    twin.full_clean()
    with pytest.raises(IntegrityError), transaction.atomic():
        twin.save()


VIEW_PATHS = ("view_create", "view_rename", "view_patch")
# The assistant's tools do not trim a name, so only the exact name is reserved.
TOOL_PATHS = ("tool_create", "tool_rename")
VIEW_NOT_RESERVED_NAMES = [name for name in NOT_RESERVED_NAMES if name == name.strip()]
HARMLESS_NAME = "A harmless name"
RESERVED_PATH_CASES = (
    [
        (path, RESERVED_NAME, True)
        for path in (*VIEW_PATHS, *TOOL_PATHS, "get_or_create_project")
    ]
    + [(path, f" {RESERVED_NAME} ", True) for path in VIEW_PATHS]
    + [(path, name, False) for path in VIEW_PATHS for name in VIEW_NOT_RESERVED_NAMES]
    + [
        (path, name, False)
        for path in (*TOOL_PATHS, "get_or_create_project")
        for name in NOT_RESERVED_NAMES
    ]
)


def _run_tool(name, params, user, organization, workspace):
    context = ToolContext(user=user, organization=organization, workspace=workspace)
    return registry.get(name).run(params, context)


@pytest.mark.parametrize(
    "path, name, refused",
    RESERVED_PATH_CASES,
    ids=[
        f"{path}-{'refused' if refused else 'accepted'}-{index}"
        for index, (path, _name, refused) in enumerate(RESERVED_PATH_CASES)
    ],
)
def test_reserved_name_is_refused_by_create_rename_tools_and_get_or_create(
    path, name, refused, auth_client, user, organization, workspace
):
    target = _project(Project.objects, organization, workspace, "Target project")

    if path == "get_or_create_project":
        if refused:
            with pytest.raises(ReservedProjectNameError) as raised:
                get_or_create_project(
                    name, str(organization.id), "observe", str(user.id), workspace.id
                )
            assert str(raised.value) == RESERVED_PROJECT_NAME_MESSAGE
        else:
            project = get_or_create_project(
                name, str(organization.id), "observe", str(user.id), workspace.id
            )
            assert project.name == name
            assert Project.no_workspace_objects.filter(pk=project.pk).exists()
    elif path in TOOL_PATHS:
        if path == "tool_create":
            params = {"name": name, "model_type": "GenerativeLLM"}
        else:
            params = {"project_id": str(target.id), "name": name}
        result = _run_tool(
            "create_project" if path == "tool_create" else "update_project",
            params,
            user,
            organization,
            workspace,
        )
        if refused:
            assert result.is_error
            assert RESERVED_PROJECT_NAME_MESSAGE in result.content
        else:
            assert not result.is_error, result.content
            assert result.data["name"] == name
            assert Project.objects.filter(pk=result.data["project_id"]).exists()
    elif path == "view_create":
        response = auth_client.post(
            "/tracer/project/",
            {"name": name, "model_type": "GenerativeLLM", "trace_type": "observe"},
            format="json",
        )
        if refused:
            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert response.json()["message"] == RESERVED_PROJECT_NAME_MESSAGE
        else:
            assert response.status_code == status.HTTP_200_OK
            assert Project.objects.get(name=name).trace_type == "observe"
    elif path == "view_rename":
        response = auth_client.post(
            "/tracer/project/update_project_name/",
            {"project_id": str(target.id), "name": name},
            format="json",
        )
        if refused:
            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert response.json()["message"] == RESERVED_PROJECT_NAME_MESSAGE
        else:
            assert response.status_code == status.HTTP_200_OK
            assert Project.objects.get(pk=target.pk).name == name
    else:
        response = auth_client.patch(
            f"/tracer/project/{target.id}/", {"name": name}, format="json"
        )
        if refused:
            body = response.json()
            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert body["details"]["name"] == [RESERVED_PROJECT_NAME_MESSAGE]
            assert body["message"] == "name: " + RESERVED_PROJECT_NAME_MESSAGE
        else:
            assert response.status_code == status.HTTP_200_OK
            assert Project.objects.get(pk=target.pk).name == name

    if refused:
        assert not Project.with_simulation.filter(
            name__in={name, name.strip()}
        ).exists()
        if path in ("view_rename", "view_patch", "tool_rename"):
            assert Project.objects.get(pk=target.pk).name == "Target project"


@pytest.mark.parametrize("tool", ["create_project", "update_project"])
def test_a_reserved_name_from_the_ai_tool_does_not_block_the_hidden_project(
    tool, user, organization, workspace
):
    agent = _agent_without_context(organization, workspace)
    target = _project(Project.objects, organization, workspace, "Target project")
    name = simulation_project_name(agent.id)
    if tool == "create_project":
        params = {"name": name, "model_type": "GenerativeLLM"}
    else:
        params = {"project_id": str(target.id), "name": name}

    assert _run_tool(tool, params, user, organization, workspace).is_error
    project = ensure_simulation_project(agent)

    assert project.simulation_agent_id == agent.id
    assert project.name == name


@pytest.mark.parametrize("path", ["view_patch", "view_rename"])
def test_a_hidden_project_cannot_be_renamed(path, auth_client, organization, workspace):
    hidden = _project(
        Project.with_simulation,
        organization,
        workspace,
        "Hidden project",
        simulation_agent=_agent(organization, workspace, "Hidden agent"),
    )

    if path == "view_patch":
        response = auth_client.patch(
            f"/tracer/project/{hidden.id}/", {"name": HARMLESS_NAME}, format="json"
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
    else:
        response = auth_client.post(
            "/tracer/project/update_project_name/",
            {"project_id": str(hidden.id), "name": HARMLESS_NAME},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["message"] == get_error_message("PROJECT_NOT_FOUND")
    assert Project.with_simulation.get(pk=hidden.pk).name == "Hidden project"


RUN_ID = str(uuid.uuid4())
CALL_ID = str(uuid.uuid4())
TRACE_ID = str(uuid.uuid4())
PROJECT_ID = str(uuid.uuid4())
SERIALIZERS = [
    (TraceDetailQuerySerializer, {}),
    (TraceVoiceCallDetailQuerySerializer, {"trace_id": TRACE_ID}),
    (SpanReferenceQuerySerializer, {}),
]


def _span_reference(**changes):
    return {
        "project_id": PROJECT_ID,
        "trace_id": "trace",
        "start_hour": "2026-08-08T12:00:00Z",
        "observation_type": "span",
        "service_name": "service-a",
        "expected_start_time": "2026-08-08T12:20:00.123456Z",
        "expected_version": "2",
        **changes,
    }


@pytest.mark.parametrize(("serializer", "base"), SERIALIZERS)
def test_the_three_query_serializers_declare_the_run_parameters(serializer, base):
    for key in ("test_execution_id", "call_execution_id"):
        assert key in serializer().fields
        assert serializer().fields[key].required is False
        checked = serializer(data={**base, key: RUN_ID})
        assert checked.is_valid(), checked.errors
        assert str(checked.validated_data[key]) == RUN_ID
        assert serializer(data=base).is_valid()

    both = serializer(
        data={**base, "test_execution_id": RUN_ID, "call_execution_id": CALL_ID}
    )
    assert not both.is_valid()
    assert "call_execution_id" in both.errors

    bad = serializer(data={**base, "test_execution_id": "not-a-uuid"})
    assert not bad.is_valid()
    assert "test_execution_id" in bad.errors


@pytest.mark.parametrize(
    "path",
    [
        "/tracer/trace/{id}/",
        "/tracer/trace/voice_call_detail/",
        "/tracer/observation-span/{id}/",
    ],
)
def test_the_published_schema_lists_the_run_parameters(path):
    swagger = Path(__file__).resolve().parents[3] / "api_contracts/openapi/swagger.json"
    parameters = json.loads(swagger.read_text())["paths"][path]["get"]["parameters"]
    by_name = {parameter["name"]: parameter for parameter in parameters}

    for key in ("test_execution_id", "call_execution_id"):
        assert by_name[key]["in"] == "query"
        assert by_name[key]["required"] is False
        assert by_name[key]["format"] == "uuid"


def test_unknown_query_fields_are_still_rejected_or_ignored():
    for serializer, base in SERIALIZERS[1:]:
        unknown = serializer(data={**base, "format": "json"})
        assert not unknown.is_valid()
        assert unknown.errors["format"] == ["Unknown field."]
    assert TraceDetailQuerySerializer(data={"format": "json"}).is_valid()


def test_span_selector_ignores_the_run_parameters():
    def check(data):
        serializer = SpanReferenceQuerySerializer(data=data)
        return serializer.is_valid(), serializer.errors

    ok, _ = check({"test_execution_id": RUN_ID})
    assert ok
    ok, _ = check({"project_id": PROJECT_ID, "test_execution_id": RUN_ID})
    assert ok
    ok, _ = check({"project_id": PROJECT_ID, "call_execution_id": CALL_ID})
    assert ok

    full = _span_reference()
    ok, _ = check({**full, "test_execution_id": RUN_ID})
    assert ok
    serializer = SpanReferenceQuerySerializer(
        data={**full, "test_execution_id": RUN_ID}
    )
    assert serializer.is_valid()
    assert serializer.validated_data.keys() - {"test_execution_id"} == full.keys()

    partial = {k: v for k, v in full.items() if k not in ("service_name", "trace_id")}
    for run in ({}, {"test_execution_id": RUN_ID}):
        ok, errors = check({**partial, **run})
        assert not ok
        assert set(errors) == {"service_name", "trace_id"}
        assert "test_execution_id" not in errors


# --- the by-id reads and the run parameters ---------------------------------

READS = ["trace", "voice", "span", "physical"]
# The answer of each read for a trace it will not serve (a missing trace).
REFUSED_STATUS = {
    "trace": status.HTTP_400_BAD_REQUEST,
    "voice": status.HTTP_404_NOT_FOUND,
    "span": status.HTTP_400_BAD_REQUEST,
    "physical": status.HTTP_404_NOT_FOUND,
}
PHYSICAL_SERVICE = "svc"
PHYSICAL_HOUR = STARTED.replace(minute=0, second=0, microsecond=0)


class _RunScopeCopies(_PhysicalCopies):
    """The physical copies, plus the exact read of one physical span.

    The exact read passes no ``timeout_ms`` and names its project
    ``physical_project_id``; every call is recorded.
    """

    def execute_ch_query(self, query, params, *, timeout_ms=None, settings=None):
        if "physical_project_id" not in params:
            return super().execute_ch_query(
                query, params, timeout_ms=timeout_ms, settings=settings
            )
        self.calls.append((query, params))
        rows = [
            {
                **row,
                "service_name": PHYSICAL_SERVICE,
                "start_hour": PHYSICAL_HOUR,
                "_version": str(row["_version"]),
                "is_deleted": 0,
            }
            for row in self.rows
            if row["project_id"] == params["physical_project_id"]
            and row["trace_id"] == params["physical_trace_id"]
            and row["id"] == params["physical_span_id"]
            and row["observation_type"] == params["physical_observation_type"]
        ]
        return SimpleNamespace(data=rows)


def _place(rows, project, tag, trace_id, version):
    """Put a conversation root and one child of ``trace_id`` into ``project``."""
    rows.append(_copy(project, trace_id, f"{tag}-root", version=version))
    rows.append(
        _copy(
            project,
            trace_id,
            f"{tag}-child",
            version=version,
            parent=f"{tag}-root",
            kind="llm",
        )
    )
    return SimpleNamespace(
        trace_id=trace_id, span_id=f"{tag}-child", project=project, version=version
    )


@pytest.fixture
def world(auth_client, user, organization, workspace, monkeypatch):
    """Real projects, runs and agents; only the ClickHouse rows are in memory."""
    prod = _project(Project.objects, organization, workspace, "production")
    empty = _project(Project.objects, organization, workspace, "empty")
    agent_x = _agent_without_context(organization, workspace, "X")
    agent_y = _agent_without_context(organization, workspace, "Y")
    agent_z = _agent_without_context(organization, workspace, "Z, no hidden project")
    agent_w = _agent_without_context(organization, workspace, "W, deleted")
    hidden_x = ensure_simulation_project(agent_x)
    hidden_y = ensure_simulation_project(agent_y)
    hidden_w = ensure_simulation_project(agent_w)
    AgentDefinition.objects.filter(pk=agent_w.pk).update(deleted=True)

    rows = []
    # T is held by the production project (newest write) and by X's hidden one.
    t_prod = _place(rows, prod, "t", str(uuid.uuid4()), 20)
    t_hidden = _place(rows, hidden_x, "t", t_prod.trace_id, 10)
    h = _place(rows, hidden_x, "h", str(uuid.uuid4()), 10)
    hy = _place(rows, hidden_y, "hy", str(uuid.uuid4()), 10)
    hw = _place(rows, hidden_w, "hw", str(uuid.uuid4()), 10)

    _, execution_x, call_x = _run(organization, workspace, None, agent_x)
    _, execution_by_run_test, _ = _run(organization, workspace, agent_x, None)
    _, execution_y, _ = _run(organization, workspace, None, agent_y)
    _, execution_z, _ = _run(organization, workspace, None, agent_z)
    _, execution_w, _ = _run(organization, workspace, None, agent_w)
    deleted_run, deleted_execution, _ = _run(organization, workspace, None, agent_x)
    RunTest.objects.filter(pk=deleted_run.pk).update(deleted=True)

    other_organization, other_organization_workspace = _other_organization(user)
    foreign_agent = _agent_without_context(
        other_organization, other_organization_workspace, "Foreign"
    )
    ensure_simulation_project(foreign_agent)
    _, foreign_execution, foreign_call = _run(
        other_organization, other_organization_workspace, None, foreign_agent
    )
    # A run of the other organization with no workspace, naming this
    # organization's agent: the default-workspace rule matches a run without a
    # workspace, so only the organization filter refuses it.
    _, foreign_unscoped_execution, foreign_unscoped_call = _run(
        other_organization, None, None, agent_x
    )
    second_workspace = Workspace.objects.create(
        name="Second", organization=organization, created_by=user
    )
    _, second_workspace_execution, _ = _run(
        organization, second_workspace, None, agent_x
    )

    analytics = _RunScopeCopies(rows)
    monkeypatch.setattr("tracer.views.trace.V2AnalyticsQueryService", lambda: analytics)
    monkeypatch.setattr(
        "tracer.views.observation_span.V2AnalyticsQueryService", lambda: analytics
    )
    monkeypatch.setattr(
        "tracer.views.trace.ObservabilityService.process_raw_logs",
        lambda *_args, **_kwargs: {},
    )
    return SimpleNamespace(
        client=auth_client,
        analytics=analytics,
        prod=prod,
        empty=empty,
        hidden_x=hidden_x,
        hidden_y=hidden_y,
        hidden_w=hidden_w,
        t_prod=t_prod,
        t_hidden=t_hidden,
        h=h,
        hy=hy,
        hw=hw,
        run_x=execution_x.id,
        call_x=call_x.id,
        run_x_by_run_test=execution_by_run_test.id,
        run_y=execution_y.id,
        run_z=execution_z.id,
        run_w=execution_w.id,
        run_deleted=deleted_execution.id,
        run_foreign=foreign_execution.id,
        call_foreign=foreign_call.id,
        run_foreign_unscoped=foreign_unscoped_execution.id,
        call_foreign_unscoped=foreign_unscoped_call.id,
        run_second_workspace=second_workspace_execution.id,
    )


def _read(read, world, target, **params):
    """Call one of the four by-id reads for ``target``.

    The physical read names the project in its reference, so its ``project_id``
    is the one it asks for (default: where the target lives).
    """
    client = world.client
    if read == "trace":
        return client.get(f"/tracer/trace/{target.trace_id}/", params)
    if read == "voice":
        return client.get(
            "/tracer/trace/voice_call_detail/", {"trace_id": target.trace_id, **params}
        )
    if read == "span":
        return client.get(f"/tracer/observation-span/{target.span_id}/", params)
    reference = {
        "project_id": str(params.pop("project_id", target.project.id)),
        "trace_id": target.trace_id,
        "start_hour": PHYSICAL_HOUR.isoformat(),
        "observation_type": "llm",
        "service_name": PHYSICAL_SERVICE,
        "expected_start_time": STARTED.isoformat(),
        "expected_version": str(target.version),
    }
    return client.get(
        f"/tracer/observation-span/{target.span_id}/", {**reference, **params}
    )


def _served_project(read, response):
    result = response.data["result"]
    if read == "trace":
        return result["observation_spans"][0]["observation_span"]["project"]
    if read == "voice":
        return result["project_id"]
    return result["observation_span"]["project"]


def _carried(analytics):
    """Every project id any recorded statement was bound to."""
    ids = set()
    for _query, params in analytics.calls:
        ids.update(params.get("detail_project_ids", ()))
        if "physical_project_id" in params:
            ids.add(params["physical_project_id"])
    return ids


def _ids(*projects):
    return {str(project.id) for project in projects}


def _missing_answer(read, world):
    """The status and body ``read`` gives for a trace that does not exist."""
    nowhere = SimpleNamespace(
        trace_id=str(uuid.uuid4()),
        span_id=str(uuid.uuid4()),
        project=world.prod,
        version=1,
    )
    response = _read(read, world, nowhere)
    world.analytics.calls.clear()
    return response.status_code, response.data


def _all_hidden(w):
    return _ids(w.hidden_x, w.hidden_y, w.hidden_w)


# (case, build(world) -> (query params, target), projects no statement may carry)
# Case 3 names a run whose own agent (Y) is readable, so Y's project may be
# carried; the target still lives in X's, which must never be.
REFUSALS = [
    ("no run, hidden trace", lambda w: ({}, w.h), _all_hidden),
    (
        "no run, project_id is the hidden project",
        lambda w: ({"project_id": str(w.hidden_x.id)}, w.h),
        _all_hidden,
    ),
    (
        "run of another agent asks for this agent's trace",
        lambda w: ({"test_execution_id": w.run_y}, w.h),
        lambda w: _ids(w.hidden_x, w.hidden_w),
    ),
    (
        "run of another organization",
        lambda w: (
            {"test_execution_id": w.run_foreign, "project_id": str(w.hidden_x.id)},
            w.h,
        ),
        _all_hidden,
    ),
    (
        "run of another organization, no workspace, naming this agent",
        lambda w: ({"test_execution_id": w.run_foreign_unscoped}, w.h),
        _all_hidden,
    ),
    (
        "call of another organization, no workspace, naming this agent",
        lambda w: ({"call_execution_id": w.call_foreign_unscoped}, w.h),
        _all_hidden,
    ),
    (
        "run in another workspace",
        lambda w: ({"test_execution_id": w.run_second_workspace}, w.h),
        _all_hidden,
    ),
    ("unknown run", lambda w: ({"test_execution_id": uuid.uuid4()}, w.h), _all_hidden),
    (
        "call of another organization",
        lambda w: ({"call_execution_id": w.call_foreign}, w.h),
        _all_hidden,
    ),
    (
        "agent with no hidden project",
        lambda w: ({"test_execution_id": w.run_z}, w.h),
        _all_hidden,
    ),
    (
        "deleted agent, its own hidden trace",
        lambda w: ({"test_execution_id": w.run_w}, w.hw),
        _all_hidden,
    ),
    (
        "deleted run test",
        lambda w: ({"test_execution_id": w.run_deleted}, w.h),
        _all_hidden,
    ),
]


@pytest.mark.parametrize("read", READS)
@pytest.mark.parametrize(
    ("case", "build", "forbidden"), REFUSALS, ids=[r[0] for r in REFUSALS]
)
def test_by_id_reads_refuse_the_hidden_project_without_a_run(
    read, case, build, forbidden, world
):
    params, target = build(world)
    missing = _missing_answer(read, world)

    response = _read(read, world, target, **params)

    assert response.status_code == REFUSED_STATUS[read], (case, response.data)
    assert (response.status_code, response.data) == missing, case
    assert _carried(world.analytics).isdisjoint(forbidden(world))


# (case, build(world) -> (query params, target, project served or None))
SERVES = [
    (
        "test_execution_id of a run of the agent",
        lambda w: ({"test_execution_id": w.run_x}, w.h, w.hidden_x),
    ),
    (
        "call_execution_id, no project_id",
        lambda w: ({"call_execution_id": w.call_x}, w.h, w.hidden_x),
    ),
    (
        "test_execution_id of a run of another agent, its own trace",
        lambda w: ({"test_execution_id": w.run_y}, w.hy, w.hidden_y),
    ),
    (
        "agent named on the run test only",
        lambda w: ({"test_execution_id": w.run_x_by_run_test}, w.h, w.hidden_x),
    ),
    (
        "project_id is the hidden project",
        lambda w: (
            {"test_execution_id": w.run_x, "project_id": str(w.hidden_x.id)},
            w.h,
            w.hidden_x,
        ),
    ),
    (
        "project_id is a visible project",
        lambda w: (
            {"test_execution_id": w.run_x, "project_id": str(w.prod.id)},
            w.h,
            None,
        ),
    ),
    (
        "same trace id in production and hidden, with the run",
        lambda w: ({"test_execution_id": w.run_x}, w.t_hidden, w.hidden_x),
    ),
    (
        "same trace id in production and hidden, without the run",
        lambda w: ({}, w.t_prod, w.prod),
    ),
]


@pytest.mark.parametrize("read", READS)
@pytest.mark.parametrize(("case", "build"), SERVES, ids=[r[0] for r in SERVES])
def test_by_id_reads_serve_it_with_a_readable_run_of_the_same_agent_only(
    read, case, build, world
):
    params, target, expected = build(world)
    missing = _missing_answer(read, world)

    response = _read(read, world, target, **params)

    carried = _carried(world.analytics)
    if expected is None:
        assert response.status_code == REFUSED_STATUS[read], (case, response.data)
        assert (response.status_code, response.data) == missing, case
        assert carried.isdisjoint(_ids(world.hidden_x, world.prod))
        return
    assert response.status_code == status.HTTP_200_OK, (case, response.data)
    assert _served_project(read, response) == str(expected.id)
    if expected in (world.hidden_x, world.hidden_y):
        assert carried == _ids(expected)
    else:
        assert carried.isdisjoint(_ids(world.hidden_x, world.hidden_y, world.hidden_w))


@pytest.mark.parametrize("read", READS)
def test_by_id_reads_without_a_run_parameter_are_unchanged(read, world, monkeypatch):
    scope_calls = []
    monkeypatch.setattr(
        "tracer.services.simulation_project.simulation_scope_for_run",
        lambda *args, **kwargs: scope_calls.append((args, kwargs)),
    )

    unpinned = _read(read, world, world.t_prod)
    caller_set = _carried(world.analytics)
    world.analytics.calls.clear()
    pinned = _read(read, world, world.t_prod, project_id=str(world.prod.id))
    pinned_set = _carried(world.analytics)
    hidden = _read(read, world, world.h, project_id=str(world.hidden_x.id))

    assert unpinned.status_code == status.HTTP_200_OK, unpinned.data
    assert _served_project(read, unpinned) == str(world.prod.id)
    assert pinned.status_code == status.HTTP_200_OK, pinned.data
    assert pinned_set == _ids(world.prod)
    if read == "physical":
        assert caller_set == _ids(world.prod)
    else:
        assert caller_set >= _ids(world.prod, world.empty)
        assert caller_set.isdisjoint(_ids(world.hidden_x, world.hidden_y))
    assert hidden.status_code == REFUSED_STATUS[read]
    assert _carried(world.analytics) == _ids(world.prod)
    assert scope_calls == []


@pytest.mark.parametrize("read", READS)
@pytest.mark.parametrize(
    ("params", "field"),
    [
        (
            lambda w: {
                "test_execution_id": w.run_x,
                "call_execution_id": w.call_x,
            },
            "call_execution_id",
        ),
        (lambda w: {"test_execution_id": "not-a-uuid"}, "test_execution_id"),
        (lambda w: {"call_execution_id": "not-a-uuid"}, "call_execution_id"),
    ],
    ids=["both", "test run not a uuid", "call not a uuid"],
)
def test_a_bad_run_parameter_is_a_validation_error_on_every_read(
    read, params, field, world
):
    response = _read(read, world, world.h, **params(world))

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert field in json.dumps(response.data)
    assert world.analytics.calls == []
