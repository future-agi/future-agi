"""W10: sink destinations are capabilities of the owning graph, not arbitrary IDs."""

from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from agent_playground.models.node import Node
from agent_playground.services.engine.output_sink import call_sinks
from agent_playground.services.engine.output_sinks.cell_sink import CellOutputSink
from agent_playground.utils.version_content import update_version_content
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = pytest.mark.django_db


@pytest.fixture
def destinations(organization, workspace, user):
    clear_workspace_context()
    other_org = Organization.objects.create(name="sink foreign org")
    other_ws = Workspace.no_workspace_objects.create(
        name="sink foreign workspace", organization=other_org, created_by=user
    )
    sibling_ws = Workspace.no_workspace_objects.create(
        name="sink sibling workspace", organization=organization, created_by=user
    )
    result = {}
    for name, org, ws in (
        ("own", organization, workspace),
        ("foreign", other_org, other_ws),
        ("sibling", organization, sibling_ws),
        ("other_dataset", organization, workspace),
        ("null_workspace", organization, None),
    ):
        dataset = Dataset.no_workspace_objects.create(
            name=name, organization=org, workspace=ws
        )
        column = Column.no_workspace_objects.create(
            dataset=dataset, name="output", data_type="text", source="run_prompt"
        )
        row = Row.no_workspace_objects.create(dataset=dataset, order=0)
        cell = Cell.no_workspace_objects.create(
            dataset=dataset, column=column, row=row, value="untouched", status="pass"
        )
        result[name] = (dataset, column, row, cell)
    return result


def sink_config(destination):
    dataset, column, row, _ = destination
    return {
        "dataset_id": str(dataset.pk),
        "column_id": str(column.pk),
        "row_id": str(row.pk),
    }


def node_payload(node, config):
    return {
        # Snapshot replacement creates new rows; reusing a live node PK makes
        # the unfixed baseline fail on duplicate-PK instead of sink authorization.
        "id": str(uuid4()),
        "name": node.name,
        "type": "atomic",
        "node_template_id": str(node.node_template_id),
        "config": {"_output_sinks": [{"name": "cell", "config": config}]},
    }


@pytest.mark.parametrize("target", ["foreign", "sibling", "null_workspace"])
def test_graph_save_refuses_foreign_sink_atomically(
    authenticated_client, graph, graph_version, node, destinations, target
):
    payload = {
        "status": "draft",
        "nodes": [node_payload(node, sink_config(destinations[target]))],
    }
    versions_before = graph.versions.count()
    url = reverse("graph-versions", kwargs={"pk": graph.pk})
    response = authenticated_client.post(url, payload, format="json")
    assert response.status_code == 400, response.data
    node.refresh_from_db()
    assert not node.deleted and node.config == {}
    assert graph.versions.count() == versions_before
    destinations[target][3].refresh_from_db()
    assert destinations[target][3].value == "untouched"


@pytest.mark.parametrize("target", ["foreign", "sibling", "null_workspace"])
def test_graph_node_update_refuses_foreign_sinks(node, destinations, target):
    node.config = {
        "_output_sinks": [{"name": "cell", "config": sink_config(destinations[target])}]
    }
    with pytest.raises(ValidationError):
        node.save(skip_validation=True)
    node.refresh_from_db()
    assert node.config == {}


@pytest.mark.parametrize("part", ["column_id", "row_id"])
def test_snapshot_rejects_mixed_parent_ids_before_deleting_nodes(
    graph, graph_version, node, destinations, part
):
    config = sink_config(destinations["own"])
    config[part] = sink_config(destinations["other_dataset"])[part]
    with pytest.raises(ValidationError):
        update_version_content(
            graph, graph_version, [node_payload(node, config)], "draft", None
        )
    node.refresh_from_db()
    assert not node.deleted and node.config == {}


@pytest.mark.parametrize("status", ["SUCCESS", "FAILED"])
@pytest.mark.parametrize(
    "target",
    ["foreign", "sibling", "null_workspace", "missing", "mixed_column", "mixed_row"],
)
def test_legacy_config_execution_cannot_write_foreign_cell(
    node, destinations, status, target
):
    config = sink_config(destinations.get(target, destinations["own"]))
    if target == "missing":
        config["dataset_id"] = str(uuid4())
    elif target.startswith("mixed_"):
        key = target.removeprefix("mixed_") + "_id"
        config[key] = sink_config(destinations["other_dataset"])[key]
    # Simulate a persisted pre-fix malicious graph without invoking model validation.
    Node.no_workspace_objects.filter(pk=node.pk).update(
        config={"_output_sinks": [{"name": "cell", "config": config}]}
    )
    node.refresh_from_db()
    from agent_playground.services.engine.output_sink import register_sink

    register_sink("cell", CellOutputSink())
    results = call_sinks(
        node.config["_output_sinks"],
        {
            "node_id": str(node.pk),
            "node_name": node.name,
            "template_name": "test",
            "inputs": {},
            "outputs": {"text": "foreign write"},
            "status": status,
            "metadata": {"error": "foreign failure"},
        },
    )
    assert results[0]["status"] == "FAILED"
    assert results[0].get("error_type") == "SinkScopeError"
    for _, _, _, cell in destinations.values():
        cell.refresh_from_db()
        assert cell.value == "untouched"
    assert Cell.no_workspace_objects.count() == len(destinations)


@pytest.mark.parametrize(
    "status,expected", [("SUCCESS", "same tenant"), ("FAILED", "failed locally")]
)
def test_same_tenant_sink_still_saves_and_executes(
    node, destinations, status, expected
):
    from agent_playground.services.engine.output_sink import OutputSinkContext

    config = sink_config(destinations["own"])
    node.config = {"_output_sinks": [{"name": "cell", "config": config}]}
    node.save(skip_validation=True)
    CellOutputSink().store(
        OutputSinkContext(
            node_id=str(node.pk),
            node_name=node.name,
            template_name="test",
            config=config,
            inputs={},
            outputs={"text": "same tenant"},
            status=status,
            metadata={"error": "failed locally"},
        )
    )
    cell = destinations["own"][3]
    cell.refresh_from_db()
    assert cell.value == expected


@pytest.mark.parametrize("part", [0, 1, 2], ids=["dataset", "column", "row"])
@pytest.mark.parametrize("operation", ["delete", "metadata"])
def test_removed_same_tenant_sink_allows_non_capability_save(
    authenticated_client, graph, graph_version, node, destinations, part, operation
):
    node.config = {
        "_output_sinks": [{"name": "cell", "config": sink_config(destinations["own"])}]
    }
    node.save(skip_validation=True)
    original_config = node.config
    destination = destinations["own"][part]
    type(destination).no_workspace_objects.filter(pk=destination.pk).update(
        deleted=True
    )
    url = reverse(
        "graph-version-node-detail",
        kwargs={"pk": graph.pk, "version_id": graph_version.pk, "node_id": node.pk},
    )
    if operation == "delete":
        response = authenticated_client.delete(url)
    else:
        response = authenticated_client.patch(url, {"name": "renamed"}, format="json")
    assert response.status_code == 200, response.data
    node.refresh_from_db()
    assert node.config == original_config
    assert node.deleted is (operation == "delete")
    if operation == "metadata":
        assert node.name == "renamed"
    # Retaining the JSON never authorizes execution against a removed destination.
    from agent_playground.services.engine.output_sink import OutputSinkContext

    with pytest.raises(ValidationError):
        CellOutputSink().store(
            OutputSinkContext(
                node_id=str(node.pk),
                node_name=node.name,
                template_name="test",
                config=sink_config(destinations["own"]),
                inputs={},
                outputs={},
                status="SUCCESS",
                metadata={},
            )
        )


@pytest.mark.parametrize("target", ["foreign", "sibling"])
@pytest.mark.parametrize("deleted", [False, True])
def test_unchanged_foreign_sink_is_still_refused_on_save(
    node, destinations, target, deleted
):
    config = {
        "_output_sinks": [{"name": "cell", "config": sink_config(destinations[target])}]
    }
    Node.no_workspace_objects.filter(pk=node.pk).update(config=config)
    node.refresh_from_db()
    node.deleted = deleted
    node.name = "not authorized"
    with pytest.raises(ValidationError):
        node.save(skip_validation=True)
    node.refresh_from_db()
    assert not node.deleted and node.name != "not authorized"


def test_changed_sink_cannot_gain_deleted_destination(node, destinations):
    node.config = {
        "_output_sinks": [{"name": "cell", "config": sink_config(destinations["own"])}]
    }
    node.save(skip_validation=True)
    other = destinations["other_dataset"]
    Row.no_workspace_objects.filter(pk=other[2].pk).update(deleted=True)
    node.config = {"_output_sinks": [{"name": "cell", "config": sink_config(other)}]}
    with pytest.raises(ValidationError):
        node.save(skip_validation=True)
