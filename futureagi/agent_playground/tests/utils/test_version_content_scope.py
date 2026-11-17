"""Reference helpers inherit destination scope without relaxing tenant isolation."""

import uuid

import pytest
from django.core.exceptions import ValidationError

from accounts.models import Organization, User, Workspace
from accounts.models.organization_membership import OrganizationMembership
from agent_playground.models.choices import GraphVersionStatus, NodeType, PortDirection
from agent_playground.models.graph import Graph
from agent_playground.models.graph_version import GraphVersion
from agent_playground.models.node import Node
from agent_playground.models.port import Port
from agent_playground.utils.version_content import (
    create_node,
    create_port,
    update_version_content,
)


def _create_reference(kind, destination, reference, node_template, **scope):
    node_data = {
        "id": str(uuid.uuid4()),
        "type": NodeType.SUBGRAPH,
        "name": "Scoped subgraph",
        "ref_graph_version_id": str(reference.id),
    }
    if kind == "node":
        return create_node(destination, node_data, **scope)

    child = Node.no_workspace_objects.create(
        graph_version=reference,
        type=NodeType.ATOMIC,
        name="Referenced child",
        node_template=node_template,
    )
    child_port = Port.no_workspace_objects.create(
        node=child,
        key="input1",
        display_name="input1",
        direction=PortDirection.INPUT,
    )
    # An existing subgraph node must not let create_port avoid its own scope check.
    parent = Node.no_workspace_objects.create(
        graph_version=destination,
        type=NodeType.SUBGRAPH,
        name="Scoped subgraph",
        ref_graph_version=reference,
    )
    return create_port(
        parent,
        {
            "key": "custom",
            "display_name": "Scoped input",
            "direction": PortDirection.INPUT,
            "ref_port_id": str(child_port.id),
        },
        **scope,
    )


def _assert_reference(result, kind, reference):
    if kind == "node":
        assert result.ref_graph_version == reference
    else:
        assert result.ref_port.node.graph_version == reference
    assert result.pk is not None


@pytest.mark.parametrize("kind", ["node", "port"])
@pytest.mark.parametrize(
    "context", ["omitted", "organization", "workspace", "objects", "ids"]
)
def test_reference_defaults_to_destination_scope(
    kind, context, graph_version, active_referenced_graph_version, node_template
):
    graph = graph_version.graph
    scope = {
        "omitted": {},
        "organization": {"organization": graph.organization},
        "workspace": {"workspace": graph.workspace},
        "objects": {"organization": graph.organization, "workspace": graph.workspace},
        "ids": {"organization": graph.organization_id, "workspace": graph.workspace_id},
    }[context]
    result = _create_reference(
        kind, graph_version, active_referenced_graph_version, node_template, **scope
    )
    _assert_reference(result, kind, active_referenced_graph_version)


@pytest.mark.parametrize("kind", ["node", "port"])
@pytest.mark.parametrize("boundary", ["organization", "workspace"])
@pytest.mark.parametrize("skip_validation", [False, True])
def test_reference_defaults_reject_other_tenant_scope(
    kind, boundary, skip_validation, graph_version, node_template
):
    destination = graph_version.graph
    organization, user = destination.organization, destination.created_by
    if boundary == "organization":
        organization = Organization.objects.create(name="Other reference organization")
        user = User.objects.create_user(
            email="reference-owner@example.invalid",
            password="testpassword123",
            name="Other reference owner",
            organization=organization,
        )
        OrganizationMembership.no_workspace_objects.get_or_create(
            user=user, organization=organization, defaults={"is_active": True}
        )
    workspace = Workspace.objects.create(
        name="Other reference workspace", organization=organization, created_by=user
    )
    graph = Graph.no_workspace_objects.create(
        name="Other reference graph",
        organization=organization,
        workspace=workspace,
        created_by=user,
    )
    reference = GraphVersion.no_workspace_objects.create(
        graph=graph, version_number=1, status=GraphVersionStatus.ACTIVE
    )
    message = "Referenced graph version" if kind == "node" else "Referenced port"
    with pytest.raises(ValidationError, match=message):
        _create_reference(
            kind,
            graph_version,
            reference,
            node_template,
            skip_validation=skip_validation,
        )


@pytest.mark.parametrize("kind", ["node", "port"])
@pytest.mark.parametrize("field", ["organization", "workspace"])
def test_explicit_scope_is_not_replaced_by_destination_scope(
    kind, field, graph_version, active_referenced_graph_version, node_template
):
    message = "Referenced graph version" if kind == "node" else "Referenced port"
    with pytest.raises(ValidationError, match=message):
        _create_reference(
            kind,
            graph_version,
            active_referenced_graph_version,
            node_template,
            **{field: uuid.uuid4()},
        )


@pytest.mark.parametrize("kind", ["node", "port"])
def test_explicit_empty_organization_remains_template_only(
    kind, graph_version, active_referenced_graph_version, node_template
):
    message = "Referenced graph version" if kind == "node" else "Referenced port"
    with pytest.raises(ValidationError, match=message):
        _create_reference(
            kind,
            graph_version,
            active_referenced_graph_version,
            node_template,
            organization="",
        )


@pytest.mark.parametrize("kind", ["node", "port"])
def test_template_reference_remains_accessible(
    kind, graph_version, active_template_graph_version, node_template
):
    result = _create_reference(
        kind, graph_version, active_template_graph_version, node_template
    )
    _assert_reference(result, kind, active_template_graph_version)


@pytest.mark.parametrize("kind", ["node", "port"])
def test_destination_without_organization_cannot_reference_private_graph(
    kind, active_template_graph_version, active_referenced_graph_version, node_template
):
    message = "Referenced graph version" if kind == "node" else "Referenced port"
    with pytest.raises(ValidationError, match=message):
        _create_reference(
            kind,
            active_template_graph_version,
            active_referenced_graph_version,
            node_template,
        )


@pytest.mark.parametrize("kind", ["node", "port"])
def test_legacy_destination_without_workspace_uses_organization(
    kind, graph_version_no_ws, active_referenced_graph_version, node_template
):
    result = _create_reference(
        kind, graph_version_no_ws, active_referenced_graph_version, node_template
    )
    _assert_reference(result, kind, active_referenced_graph_version)


@pytest.mark.parametrize("other_workspace", [False, True])
def test_snapshot_with_organization_defaults_to_destination_workspace(
    other_workspace, graph_version, active_referenced_graph_version
):
    destination = graph_version.graph
    reference = active_referenced_graph_version
    if other_workspace:
        workspace = Workspace.objects.create(
            name="Other snapshot workspace",
            organization=destination.organization,
            created_by=destination.created_by,
        )
        reference.graph.workspace = workspace
        reference.graph.save(update_fields=["workspace"])
    node_id = uuid.uuid4()
    kwargs = {
        "graph": destination,
        "version": graph_version,
        "nodes_data": [
            {
                "id": str(node_id),
                "type": NodeType.SUBGRAPH,
                "name": "Snapshot reference",
                "ref_graph_version_id": str(reference.id),
            }
        ],
        "new_status": GraphVersionStatus.DRAFT,
        "commit_message": None,
        "organization": destination.organization,
        "workspace": None,
    }
    if other_workspace:
        with pytest.raises(ValidationError, match="Referenced graph version"):
            update_version_content(**kwargs)
        assert not Node.no_workspace_objects.filter(id=node_id).exists()
    else:
        update_version_content(**kwargs)
        node = Node.no_workspace_objects.get(id=node_id)
        assert node.graph_version == graph_version
        assert node.ref_graph_version == reference
