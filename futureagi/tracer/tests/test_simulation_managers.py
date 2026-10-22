"""The hidden simulation project stays out of the project managers."""

import json
import re
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.db import IntegrityError, transaction
from rest_framework import status

from model_hub.models.ai_model import AIModel
from simulate.models.agent_definition import AgentDefinition
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
from tracer.tests._simulation_world import (
    PHYSICAL_HOUR,
    PHYSICAL_SERVICE,
    REFUSALS,
    REFUSED_STATUS,
    RUN_PARAMETERS,
    SERVES,
    _agent,
    _carried,
    _ids,
    _project,
    world,  # noqa: F401  (the fixture, used by name)
)
from tracer.tests.test_detail_identity_collision import STARTED
from tracer.tests.test_simulation_project import (
    NOT_RESERVED_NAMES,
    RESERVED_NAME,
    _agent_without_context,
)
from tracer.utils.otel import get_or_create_project

pytestmark = pytest.mark.django_db

REPO_ROOT = Path(__file__).resolve().parents[3]


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
EXACT_NAME_PATHS = ("get_or_create_project",)
VIEW_NOT_RESERVED_NAMES = [name for name in NOT_RESERVED_NAMES if name == name.strip()]
HARMLESS_NAME = "A harmless name"
RESERVED_PATH_CASES = (
    [(path, RESERVED_NAME, True) for path in (*VIEW_PATHS, *EXACT_NAME_PATHS)]
    + [(path, f" {RESERVED_NAME} ", True) for path in VIEW_PATHS]
    + [(path, name, False) for path in VIEW_PATHS for name in VIEW_NOT_RESERVED_NAMES]
    + [(path, name, False) for path in EXACT_NAME_PATHS for name in NOT_RESERVED_NAMES]
)


@pytest.mark.parametrize(
    "path, name, refused",
    RESERVED_PATH_CASES,
    ids=[
        f"{path}-{'refused' if refused else 'accepted'}-{index}"
        for index, (path, _name, refused) in enumerate(RESERVED_PATH_CASES)
    ],
)
def test_reserved_name_is_refused_by_create_rename_views_and_get_or_create(
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
        if path in ("view_rename", "view_patch"):
            assert Project.objects.get(pk=target.pk).name == "Target project"


@pytest.mark.parametrize("path", ["view_create", "view_rename"])
def test_a_refused_reserved_name_does_not_block_the_hidden_project(
    path, auth_client, user, organization, workspace
):
    agent = _agent_without_context(organization, workspace)
    target = _project(Project.objects, organization, workspace, "Target project")
    name = simulation_project_name(agent.id)
    if path == "view_create":
        response = auth_client.post(
            "/tracer/project/",
            {"name": name, "model_type": "GenerativeLLM", "trace_type": "observe"},
            format="json",
        )
    else:
        response = auth_client.post(
            "/tracer/project/update_project_name/",
            {"project_id": str(target.id), "name": name},
            format="json",
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
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
    swagger = REPO_ROOT / "api_contracts/openapi/swagger.json"
    parameters = json.loads(swagger.read_text())["paths"][path]["get"]["parameters"]
    by_name = {parameter["name"]: parameter for parameter in parameters}

    for key in RUN_PARAMETERS:
        assert by_name[key]["in"] == "query"
        assert by_name[key]["required"] is False
        assert by_name[key]["type"] == "string"
        assert by_name[key]["format"] == "uuid"


def test_only_the_expected_operations_name_the_run_parameters():
    swagger = REPO_ROOT / "api_contracts/openapi/swagger.json"
    paths = json.loads(swagger.read_text())["paths"]

    found = {}
    for path, item in paths.items():
        for method, operation in item.items():
            if not isinstance(operation, dict):
                continue
            # Path parameters of other routes share these names; only the
            # query string is in question here.
            declared = item.get("parameters", []) + operation.get("parameters", [])
            names = {p.get("name") for p in declared if p.get("in") == "query"}
            named = frozenset(names & set(RUN_PARAMETERS))
            if named:
                found[(path, method)] = named

    both = frozenset(RUN_PARAMETERS)
    expected = {
        # The three detail reads that take the run parameters.
        ("/tracer/trace/{id}/", "get"): both,
        ("/tracer/trace/voice_call_detail/", "get"): both,
        ("/tracer/observation-span/{id}/", "get"): both,
        # The list of a run's traces, which needs the call it is read for.
        ("/simulate/v3/test-executions/{test_execution_id}/traces/", "get"): frozenset(
            {"call_execution_id"}
        ),
        # Unrelated listings that already carried one filter of their own.
        ("/sdk/api/v1/simulation/metrics/", "get"): frozenset({"call_execution_id"}),
        ("/sdk/api/v1/simulation/runs/", "get"): frozenset({"call_execution_id"}),
        ("/simulate/api/call-executions/", "get"): frozenset({"test_execution_id"}),
    }
    assert found == expected


@pytest.mark.parametrize(
    ("path", "zod_name"),
    [
        ("/tracer/trace/{id}/", "TracerTraceReadQueryParams"),
        ("/tracer/trace/voice_call_detail/", "TracerTraceVoiceCallDetailQueryParams"),
        ("/tracer/observation-span/{id}/", "TracerObservationSpanReadQueryParams"),
    ],
)
def test_the_generated_frontend_files_name_the_run_parameters(path, zod_name):
    frontend = REPO_ROOT / "frontend/src"
    zod_file = frontend / "generated/api-contracts/api.zod.ts"
    contract_file = frontend / "api/contracts/openapi-contract.generated.js"
    if not (zod_file.is_file() and contract_file.is_file()):
        pytest.skip("the frontend sources are not part of this checkout")

    zod_text = zod_file.read_text()
    start = zod_text.index(f"export const {zod_name} = zod.object({{")
    zod_block = zod_text[start : zod_text.find("\nexport const ", start + 1)]

    contract_text = contract_file.read_text()
    start = contract_text.index(f'    "{path}": {{')
    end = contract_text.find('\n    "/', start + 1)
    contract_block = contract_text[start:end]

    for key in RUN_PARAMETERS:
        assert re.search(
            rf"\b{key}: zod\s*\.string\(\)\s*\.uuid\(\)\s*\.optional\(\)", zod_block
        ), f"{zod_name} does not declare {key} as an optional uuid"
        assert re.search(
            rf'\b{key}: {{\s*required: false,\s*schema: {{\s*type: "string",'
            rf'\s*format: "uuid",',
            contract_block,
        ), f"the contract table has no optional uuid {key} for {path}"


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
