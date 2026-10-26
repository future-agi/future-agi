"""The MCP tools ``get_trace`` and ``get_span`` and the hidden simulation project.

The by-id reads open the hidden simulation project of a run's agent only for a
caller who can read the run (see ``test_simulation_managers``). These tests send
the same cases through the path an MCP tool call takes below the transport: the
generated tool from the registry, the executor, the real DRF view, the real
``Project`` managers, and the in-memory ClickHouse copies of the ``world``
fixture. Nothing in the run scope is mocked. The run scope follows the
organization the credential authenticated as, which an API key of a second
organization of the user makes differ from ``user.organization``.
"""

from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from rest_framework import status

from mcp_server.api_executor import (
    APIExecutionError,
    MCPRequestContext,
    ensure_urlconf_loaded,
    executor,
)
from mcp_server.generated_registry import registry
from tfc.middleware.workspace_context import clear_workspace_context
from tracer.tests._simulation_world import (
    PHYSICAL_HOUR,
    PHYSICAL_SERVICE,
    REFUSALS,
    REFUSED_STATUS,
    RUN_PARAMETERS,
    SERVES,
    STARTED,
    _all_hidden,
    _carried,
    _ids,
    second_organization,
    world,  # noqa: F401  (the fixture, used by name)
)

pytestmark = pytest.mark.django_db

# The MCP tool that carries each by-id read. The exact read of one physical span
# is ``get_span`` with the span reference in its query.
TOOL = {"trace": "get_trace", "span": "get_span", "physical": "get_span"}
READS = list(TOOL)


def _route(read, target):
    """The route the generated tool of ``read`` sends the request for ``target`` to."""
    if read == "trace":
        return f"/tracer/trace/{target.trace_id}/"
    return f"/tracer/observation-span/{target.span_id}/"


@pytest.fixture
def mcp(world, user, workspace):  # noqa: F811
    """The caller an MCP credential authenticated as, on a cold worker."""
    # The HTTP client of the fixture patches ``APIView.initial``; the MCP path
    # resolves the tenant from the request headers instead, so take it off.
    world.client.stop_workspace_injection()
    clear_workspace_context()
    ensure_urlconf_loaded()
    return MCPRequestContext(
        user=user, organization=user.organization, workspace=workspace
    )


@pytest.fixture
def elsewhere(world, user, mcp):  # noqa: F811
    """A second organization of the user, and the caller its API key is.

    ``user.organization`` is still the organization of the ``world``; the key's
    organization is the other one, the way a member of two organizations calls
    with the key of the second.
    """
    tenant = second_organization(world, user)
    clear_workspace_context()
    tenant.context = MCPRequestContext(
        user=user,
        organization=tenant.organization,
        workspace=tenant.workspace,
        api_key=tenant.api_key,
    )
    assert tenant.organization != user.organization
    return tenant


def _arguments(read, target, params):
    """The tool arguments for ``read`` of ``target``; every value is a string."""
    params = {name: str(value) for name, value in params.items()}
    if read == "trace":
        return {"id": target.trace_id, **params}
    if read == "span":
        return {"id": target.span_id, **params}
    reference = {
        "project_id": params.pop("project_id", str(target.project.id)),
        "trace_id": target.trace_id,
        "start_hour": PHYSICAL_HOUR.isoformat(),
        "observation_type": "llm",
        "service_name": PHYSICAL_SERVICE,
        "expected_start_time": STARTED.isoformat(),
        "expected_version": str(target.version),
    }
    return {"id": target.span_id, **reference, **params}


def _call(read, mcp, target, **params):
    """Call the tool for ``read``; return its status and its body or error data."""
    tool = registry.get(TOOL[read])
    try:
        result = executor.execute_sync(tool, _arguments(read, target, params), mcp)
    except APIExecutionError as exc:
        return exc.status_code, exc.data
    return status.HTTP_200_OK, result


def _served_project(read, result):
    if read == "trace":
        return str(result["observation_spans"][0]["observation_span"]["project"])
    return str(result["observation_span"]["project"])


def _missing_answer(read, mcp, world):  # noqa: F811
    """What the tool answers for a trace that does not exist."""
    nowhere = SimpleNamespace(
        trace_id=str(uuid4()), span_id=str(uuid4()), project=world.prod, version=1
    )
    answer = _call(read, mcp, nowhere)
    world.analytics.calls.clear()
    # A schema refusal would carry no body; this is the view's own answer.
    assert answer[0] == REFUSED_STATUS[read]
    assert answer[1]
    return answer


@pytest.mark.parametrize("name", sorted(set(TOOL.values())))
def test_the_generated_tools_declare_both_run_parameters(name):
    tool = registry.get(name)

    for parameter in RUN_PARAMETERS:
        assert parameter in tool.input_schema["properties"]
        assert parameter in tool.request["parameters"]["query"]
        assert parameter not in tool.request["parameters"]["path"]
        assert parameter not in tool.request["parameters"]["body"]


@pytest.mark.parametrize("read", READS)
@pytest.mark.parametrize("parameter", RUN_PARAMETERS)
def test_the_tools_pass_each_run_parameter_on_as_a_query_parameter(
    read, parameter, mcp, world, monkeypatch  # noqa: F811
):
    requests = []
    generic = executor._request_factory.generic

    def recording(method, path, *args, **kwargs):
        requests.append((method, path))
        return generic(method, path, *args, **kwargs)

    monkeypatch.setattr(executor._request_factory, "generic", recording)
    run = {"test_execution_id": world.run_x, "call_execution_id": world.call_x}

    answer = _call(read, mcp, world.h, **{parameter: run[parameter]})

    assert answer[0] == status.HTTP_200_OK, answer
    ((method, target),) = requests
    url = urlsplit(target)
    query = parse_qs(url.query)
    # Every argument but the path id is sent as a query parameter, and only that.
    sent = _arguments(read, world.h, {parameter: run[parameter]})
    sent.pop("id")
    assert method == "GET"
    assert url.path == _route(read, world.h)
    assert query == {name: [value] for name, value in sent.items()}
    assert query[parameter] == [str(run[parameter])]
    assert set(query) & set(RUN_PARAMETERS) == {parameter}


@pytest.mark.parametrize("read", READS)
@pytest.mark.parametrize(
    ("case", "build", "forbidden"), REFUSALS, ids=[r[0] for r in REFUSALS]
)
def test_the_tools_refuse_the_hidden_project_to_a_caller_who_cannot_read_the_run(
    read, case, build, forbidden, mcp, world  # noqa: F811
):
    params, target = build(world)
    missing = _missing_answer(read, mcp, world)

    answer = _call(read, mcp, target, **params)

    assert answer == missing, (case, answer)
    assert _carried(world.analytics).isdisjoint(forbidden(world))


@pytest.mark.parametrize("read", READS)
@pytest.mark.parametrize(("case", "build"), SERVES, ids=[r[0] for r in SERVES])
def test_the_tools_serve_the_hidden_project_to_a_caller_who_can_read_the_run(
    read, case, build, mcp, world  # noqa: F811
):
    params, target, expected = build(world)
    missing = _missing_answer(read, mcp, world)

    answer = _call(read, mcp, target, **params)

    carried = _carried(world.analytics)
    if expected is None:
        assert answer == missing, (case, answer)
        assert carried.isdisjoint(_ids(world.hidden_x, world.prod))
        return
    assert answer[0] == status.HTTP_200_OK, (case, answer)
    assert _served_project(read, answer[1]) == str(expected.id)
    if expected in (world.hidden_x, world.hidden_y):
        assert carried == _ids(expected)
    else:
        assert carried.isdisjoint(_all_hidden(world))


@pytest.mark.parametrize("read", READS)
def test_the_tools_without_a_run_parameter_stay_in_the_visible_projects(
    read, mcp, world  # noqa: F811
):
    answer = _call(read, mcp, world.t_prod)

    assert answer[0] == status.HTTP_200_OK, answer
    assert _served_project(read, answer[1]) == str(world.prod.id)
    assert _carried(world.analytics).isdisjoint(_all_hidden(world))


@pytest.mark.parametrize("read", READS)
@pytest.mark.parametrize("parameter", RUN_PARAMETERS)
def test_the_run_scope_follows_the_organization_of_the_credential(
    read, parameter, world, elsewhere  # noqa: F811
):
    run = {"test_execution_id": elsewhere.run, "call_execution_id": elsewhere.call}

    answer = _call(
        read, elsewhere.context, elsewhere.trace, **{parameter: run[parameter]}
    )

    assert answer[0] == status.HTTP_200_OK, answer
    assert _served_project(read, answer[1]) == str(elsewhere.hidden.id)
    assert _carried(world.analytics) == _ids(elsewhere.hidden)


# A run of ``user.organization``, which the key does not authenticate as. The
# last two have no workspace, which a default workspace of the key matches, so
# only the organization rule refuses them.
OWN_ORGANIZATION_RUNS = [
    ("run", lambda w, e: {"test_execution_id": w.run_x}),
    ("call", lambda w, e: {"call_execution_id": w.call_x}),
    ("run, no workspace", lambda w, e: {"test_execution_id": e.own_run}),
    ("call, no workspace", lambda w, e: {"call_execution_id": e.own_call}),
]


@pytest.mark.parametrize("read", READS)
@pytest.mark.parametrize(
    ("case", "build"), OWN_ORGANIZATION_RUNS, ids=[r[0] for r in OWN_ORGANIZATION_RUNS]
)
def test_the_run_scope_refuses_a_run_of_the_other_organization_of_the_user(
    read, case, build, world, elsewhere  # noqa: F811
):
    missing = _missing_answer(read, elsewhere.context, world)

    answer = _call(read, elsewhere.context, world.h, **build(world, elsewhere))

    assert answer == missing, (case, answer)
    assert _carried(world.analytics).isdisjoint(
        _all_hidden(world) | _ids(elsewhere.hidden)
    )


@pytest.mark.parametrize("read", READS)
@pytest.mark.parametrize("parameter", RUN_PARAMETERS)
def test_the_run_scope_of_the_users_organization_refuses_the_second_organization(
    read, parameter, world, mcp, elsewhere  # noqa: F811
):
    run = {"test_execution_id": elsewhere.run, "call_execution_id": elsewhere.call}
    missing = _missing_answer(read, mcp, world)

    answer = _call(read, mcp, elsewhere.trace, **{parameter: run[parameter]})

    assert answer == missing, answer
    assert _carried(world.analytics).isdisjoint(_ids(elsewhere.hidden))
