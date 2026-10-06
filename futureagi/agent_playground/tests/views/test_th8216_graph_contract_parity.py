"""TH-8216 A: captured Workbench graph-version and linked-prompt node responses.

Same pattern as ``model_hub/tests/test_th8216_prompt_contract_parity.py``:
real rows, real endpoints, sanitised captures under
``fixtures/contracts/th8216/captured/`` and parity against swagger.json.
Regenerate captures with ``TH8216_REGENERATE_CAPTURES=1``.
"""

import uuid
from pathlib import Path

import pytest

from agent_playground.models.choices import GraphVersionStatus, NodeType, PortDirection
from agent_playground.models.edge import Edge
from agent_playground.models.graph_version import GraphVersion
from agent_playground.models.node import Node
from agent_playground.models.node_connection import NodeConnection
from agent_playground.models.port import Port
from agent_playground.models.prompt_template_node import PromptTemplateNode
from tfc.tests.openapi_parity import (
    assert_capture,
    assert_response_matches_contract,
    capture_record,
    load_swagger,
    operation,
    validation_errors,
)

CAPTURED = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "contracts"
    / "th8216"
    / "captured"
)

VERSIONS = "/agent-playground/graphs/{id}/versions/"
VERSION = "/agent-playground/graphs/{id}/versions/{version_id}/"
NODES = "/agent-playground/graphs/{id}/versions/{version_id}/nodes/"
NODE = "/agent-playground/graphs/{id}/versions/{version_id}/nodes/{node_id}/"


@pytest.fixture(scope="module")
def swagger():
    return load_swagger()


def _sort_ports(node):
    node["ports"] = sorted(
        node["ports"], key=lambda port: (port["direction"], port["key"])
    )


def _sorted_nodes(body):
    """Node and version reads do not order nodes or ports; sort for captures."""
    result = body.get("result")
    if not isinstance(result, dict):
        return body
    if isinstance(result.get("ports"), list):
        _sort_ports(result)
    if isinstance(result.get("nodes"), list):
        for node in result["nodes"]:
            _sort_ports(node)
        result["nodes"] = sorted(result["nodes"], key=lambda node: node["name"])
    return body


def _check(swagger, name, operation_id, method, path, response, **capture):
    capture.setdefault("normalize", _sorted_nodes)
    assert_capture(
        CAPTURED / f"{name}.json",
        capture_record(operation_id, method, path, response, **capture),
    )
    assert_response_matches_contract(swagger, path, method, response)


def _prompt_snapshot():
    return {
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "Hi {{topic}}"}]}
        ],
        "configuration": {
            "model": "gpt-4o-mini",
            "temperature": 0.3,
            "response_format": "text",
            "model_detail": {"type": "chat", "providers": "openai"},
        },
    }


@pytest.fixture
def linked_version(
    graph,
    graph_version,
    node,
    second_node,
    subgraph_node,
    prompt_template,
    active_referenced_graph_version,
):
    """A draft version with a linked-prompt node, a subgraph node and an edge."""
    from model_hub.models.run_prompt import PromptVersion

    graph_version.commit_message = "th8216 draft"
    graph_version.save(update_fields=["commit_message"])
    prompt_version = PromptVersion.no_workspace_objects.create(
        original_template=prompt_template,
        template_version="v1",
        prompt_config_snapshot=_prompt_snapshot(),
        variable_names={"topic": ["contracts"]},
        metadata={"source": "th8216"},
    )
    PromptTemplateNode.no_workspace_objects.create(
        node=node, prompt_template=prompt_template, prompt_version=prompt_version
    )
    output = Port.no_workspace_objects.create(
        node=node,
        key="output1",
        display_name="output1",
        direction=PortDirection.OUTPUT,
        data_schema={"type": "string"},
    )
    second_input = Port.no_workspace_objects.create(
        node=second_node,
        key="input1",
        display_name="input1",
        direction=PortDirection.INPUT,
        data_schema={"type": "string"},
    )
    Port.no_workspace_objects.create(
        node=subgraph_node,
        key="custom",
        display_name="question",
        direction=PortDirection.INPUT,
        data_schema={"type": "string"},
        required=False,
        default_value="fallback question",
    )
    NodeConnection.no_workspace_objects.create(
        graph_version=graph_version, source_node=node, target_node=second_node
    )
    Edge.no_workspace_objects.create(
        graph_version=graph_version, source_port=output, target_port=second_input
    )
    GraphVersion.no_workspace_objects.create(
        graph=graph,
        version_number=3,
        status=GraphVersionStatus.INACTIVE,
        tags=["legacy"],
        commit_message=None,
    )
    return graph_version


@pytest.mark.django_db
def test_op018_graph_versions_list_matches_contract(
    swagger, authenticated_client, graph, linked_version
):
    response = authenticated_client.get(
        f"/agent-playground/graphs/{graph.id}/versions/"
    )

    assert response.status_code == 200
    _check(swagger, "op018_versions_list", "OP-018", "GET", VERSIONS, response)


@pytest.mark.django_db
def test_op018_graph_versions_page_edges_match_contract(
    swagger, authenticated_client, graph, linked_version
):
    first = authenticated_client.get(
        f"/agent-playground/graphs/{graph.id}/versions/",
        {"page_number": 1, "page_size": 1},
    )
    beyond = authenticated_client.get(
        f"/agent-playground/graphs/{graph.id}/versions/",
        {"page_number": 99, "page_size": 1},
    )
    malformed = authenticated_client.get(
        f"/agent-playground/graphs/{graph.id}/versions/", {"page_number": "x"}
    )

    assert first.status_code == 200
    assert first.json()["result"]["metadata"]["next_page"] == 2
    # Django's Paginator.get_page() clamps an out-of-range page to the last page.
    assert beyond.status_code == 200
    assert beyond.json()["result"]["metadata"]["page_number"] == 2
    # int("x") raises inside the handler's catch-all -> 500 (compat decision).
    assert malformed.status_code == 500
    _check(
        swagger,
        "op018_versions_first_page",
        "OP-018",
        "GET",
        VERSIONS,
        first,
        request={"page_number": 1, "page_size": 1},
    )
    _check(
        swagger,
        "op018_versions_page_beyond_range",
        "OP-018",
        "GET",
        VERSIONS,
        beyond,
        request={"page_number": 99, "page_size": 1},
        note="legacy: out-of-range page_number is clamped to the last page",
    )
    _check(
        swagger,
        "op018_versions_malformed_page_number",
        "OP-018",
        "GET",
        VERSIONS,
        malformed,
        request={"page_number": "x"},
        note="legacy: non-numeric page_number is a 500, not a 400",
    )


@pytest.mark.django_db
def test_op018_declares_its_real_query_parameters(swagger):
    parameters = {
        parameter["name"]
        for parameter in operation(swagger, VERSIONS, "GET").get("parameters", [])
        if parameter.get("in") == "query"
    }

    assert parameters == {"page_number", "page_size", "search"}


@pytest.mark.django_db
def test_op020_graph_version_detail_matches_contract(
    swagger, authenticated_client, graph, linked_version
):
    response = authenticated_client.get(
        f"/agent-playground/graphs/{graph.id}/versions/{linked_version.id}/"
    )
    missing = authenticated_client.get(
        f"/agent-playground/graphs/{graph.id}/versions/{uuid.uuid4()}/"
    )

    assert response.status_code == 200
    assert missing.status_code == 404
    nodes = {row["name"]: row for row in response.json()["result"]["nodes"]}
    assert nodes["Test Node"]["prompt_template"]["model"] == "gpt-4o-mini"
    assert nodes["Subgraph Node"]["input_mappings"] == [
        {"key": "question", "value": None}
    ]
    assert nodes["Subgraph Node"]["ports"][0]["default_value"] == "fallback question"
    assert nodes["Subgraph Node"]["ports"][0]["ref_port_id"] is None
    _check(swagger, "op020_version_detail", "OP-020", "GET", VERSION, response)
    _check(swagger, "op020_version_detail_missing", "OP-020", "GET", VERSION, missing)


@pytest.mark.django_db
def test_op020_graph_version_detail_has_unique_operation_id(swagger):
    assert (
        operation(swagger, VERSIONS, "GET")["operationId"]
        != operation(swagger, VERSION, "GET")["operationId"]
    )


@pytest.mark.django_db
def test_op028_linked_prompt_node_read_matches_contract(
    swagger, authenticated_client, graph, linked_version, node
):
    response = authenticated_client.get(
        f"/agent-playground/graphs/{graph.id}/versions/{linked_version.id}/nodes/{node.id}/"
    )

    assert response.status_code == 200
    _check(swagger, "op028_linked_prompt_node", "OP-028", "GET", NODE, response)


def _linked_prompt_write(llm_node_template):
    return {
        "id": str(uuid.uuid4()),
        "type": NodeType.ATOMIC,
        "name": "th8216_llm",
        "node_template_id": str(llm_node_template.id),
        "prompt_template": {
            "messages": [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": "Summarise {{topic}}"}],
                }
            ],
            "model": "gpt-4o-mini",
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "model_detail": {
                "model_name": "gpt-4o-mini",
                "providers": "openai",
                "is_available": True,
                "type": "chat",
            },
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "lookup",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
        },
    }


@pytest.mark.django_db
def test_op027_linked_prompt_node_create_response_matches_contract(
    swagger, authenticated_client, graph, graph_version, llm_node_template
):
    payload = _linked_prompt_write(llm_node_template)
    response = authenticated_client.post(
        f"/agent-playground/graphs/{graph.id}/versions/{graph_version.id}/nodes/",
        payload,
        format="json",
    )

    assert response.status_code == 201, response.content
    result = response.json()["result"]
    assert result["prompt_template"]["model_detail"]["is_available"] is True
    assert result["prompt_template"]["tools"] == payload["prompt_template"]["tools"]
    _check(
        swagger,
        "op027_linked_prompt_node_create",
        "OP-027",
        "POST",
        NODES,
        response,
        request={"body": "see _linked_prompt_write()"},
    )


@pytest.mark.django_db
@pytest.mark.xfail(
    strict=True,
    reason=(
        "TH-8216 compat decision: PromptTemplateData declares tools[] and "
        "model_detail as string-valued maps, but the server accepts nested "
        "JSON values. Node-write JSON typing is TH-6029's JsonValueField "
        "audit; consume from TH-6029."
    ),
)
def test_s08_linked_prompt_write_body_matches_declared_request(llm_node_template):
    swagger = load_swagger()
    body_schema = next(
        parameter["schema"]
        for parameter in operation(swagger, NODES, "POST")["parameters"]
        if parameter.get("in") == "body"
    )

    errors = validation_errors(
        swagger, body_schema, _linked_prompt_write(llm_node_template)
    )

    assert errors == []


@pytest.mark.django_db
def test_s07_legacy_list_snapshot_reads_first_entry(
    swagger, authenticated_client, graph, graph_version, prompt_template, node_template
):
    from model_hub.models.run_prompt import PromptVersion

    legacy = PromptVersion.no_workspace_objects.create(
        original_template=prompt_template,
        template_version="v7",
        prompt_config_snapshot=_prompt_snapshot(),
    )
    legacy_node = Node.no_workspace_objects.create(
        graph_version=graph_version,
        node_template=node_template,
        type=NodeType.ATOMIC,
        name="Legacy Prompt Node",
        config={},
        position={},
    )
    PromptTemplateNode.no_workspace_objects.create(
        node=legacy_node, prompt_template=prompt_template, prompt_version=legacy
    )
    # PromptTemplateNode.save() cannot link a list snapshot today, so the
    # legacy row shape is written directly, as pre-normalisation rows were.
    PromptVersion.no_workspace_objects.filter(pk=legacy.pk).update(
        prompt_config_snapshot=[_prompt_snapshot(), {"messages": []}],
        variable_names=None,
        metadata=None,
    )

    response = authenticated_client.get(
        f"/agent-playground/graphs/{graph.id}/versions/{graph_version.id}/nodes/{legacy_node.id}/"
    )

    assert response.status_code == 200
    linked = response.json()["result"]["prompt_template"]
    assert linked["messages"] == _prompt_snapshot()["messages"]
    assert linked["variable_names"] is None and linked["metadata"] is None
    _check(
        swagger,
        "s07_legacy_list_snapshot_node",
        "OP-028",
        "GET",
        NODE,
        response,
        note="legacy list snapshot: read projects only the first entry",
    )
