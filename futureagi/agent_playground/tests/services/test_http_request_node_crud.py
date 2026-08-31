"""Tests for http_request node creation and config updates in node_crud."""

import uuid

import pytest

from agent_playground.models.choices import NodeType, PortDirection
from agent_playground.models.node_template import NodeTemplate
from agent_playground.models.port import Port
from agent_playground.services.node_crud import (
    _extract_http_variables,
    create_node,
    update_node,
)
from agent_playground.templates.http_request import HTTP_REQUEST_TEMPLATE


@pytest.fixture
def http_template(db):
    return NodeTemplate.no_workspace_objects.create(
        name=HTTP_REQUEST_TEMPLATE["name"],
        display_name=HTTP_REQUEST_TEMPLATE["display_name"],
        description=HTTP_REQUEST_TEMPLATE["description"],
        icon=HTTP_REQUEST_TEMPLATE["icon"],
        categories=HTTP_REQUEST_TEMPLATE["categories"],
        input_definition=HTTP_REQUEST_TEMPLATE["input_definition"],
        output_definition=HTTP_REQUEST_TEMPLATE["output_definition"],
        input_mode=HTTP_REQUEST_TEMPLATE["input_mode"],
        output_mode=HTTP_REQUEST_TEMPLATE["output_mode"],
        config_schema=HTTP_REQUEST_TEMPLATE["config_schema"],
    )


def _http_data(**overrides):
    data = {
        "id": uuid.uuid4(),
        "type": NodeType.ATOMIC,
        "name": "Fetch User",
        "config": {
            "method": "GET",
            "url": "https://api.example.com/users/{{user_id}}",
            "headers": {"X-Token": "{{token}}"},
        },
    }
    data.update(overrides)
    return data


@pytest.mark.unit
class TestExtractHttpVariables:
    def test_extracts_from_url_and_headers(self):
        config = {
            "url": "https://x.example.com/{{a}}",
            "headers": {"H": "{{b}}"},
        }
        assert _extract_http_variables(config) == ["a", "b"]

    def test_dedupes_and_preserves_order(self):
        config = {"url": "{{a}}/{{b}}/{{a}}"}
        assert _extract_http_variables(config) == ["a", "b"]

    def test_extracts_from_nested_body(self):
        config = {"body": {"outer": {"inner": "{{deep}}", "list": ["{{item}}"]}}}
        assert _extract_http_variables(config) == ["deep", "item"]

    def test_extracts_from_auth_fields(self):
        config = {"auth": {"type": "bearer", "token": "{{secret}}"}}
        assert _extract_http_variables(config) == ["secret"]

    def test_empty_config_no_variables(self):
        assert _extract_http_variables({}) == []


@pytest.mark.integration
class TestCreateHttpNode:
    def test_create_stores_config(
        self, db, graph_version, http_template, user, organization, workspace
    ):
        data = _http_data(node_template_id=http_template.id)
        node, nc = create_node(graph_version, data, user, organization, workspace)

        assert node.config["method"] == "GET"
        assert node.config["url"] == "https://api.example.com/users/{{user_id}}"
        assert nc is None

    def test_create_builds_input_ports_from_variables(
        self, db, graph_version, http_template, user, organization, workspace
    ):
        data = _http_data(node_template_id=http_template.id)
        node, _ = create_node(graph_version, data, user, organization, workspace)

        input_ports = Port.no_workspace_objects.filter(
            node=node, direction=PortDirection.INPUT, deleted=False
        )
        names = sorted(p.display_name for p in input_ports)
        assert names == ["token", "user_id"]
        assert all(p.key == "custom" for p in input_ports)

    def test_create_builds_response_output_port(
        self, db, graph_version, http_template, user, organization, workspace
    ):
        data = _http_data(node_template_id=http_template.id)
        node, _ = create_node(graph_version, data, user, organization, workspace)

        output_ports = Port.no_workspace_objects.filter(
            node=node, direction=PortDirection.OUTPUT, deleted=False
        )
        assert [p.key for p in output_ports] == ["response"]

    def test_create_does_not_create_prompt_template_node(
        self, db, graph_version, http_template, user, organization, workspace
    ):
        from agent_playground.models.prompt_template_node import PromptTemplateNode

        data = _http_data(node_template_id=http_template.id)
        node, _ = create_node(graph_version, data, user, organization, workspace)

        assert not PromptTemplateNode.no_workspace_objects.filter(node=node).exists()

    def test_create_with_body_variables(
        self, db, graph_version, http_template, user, organization, workspace
    ):
        data = _http_data(
            node_template_id=http_template.id,
            config={
                "method": "POST",
                "url": "https://api.example.com/items",
                "body": {"name": "{{item_name}}", "qty": "{{qty}}"},
            },
        )
        node, _ = create_node(graph_version, data, user, organization, workspace)

        input_ports = Port.no_workspace_objects.filter(
            node=node, direction=PortDirection.INPUT, deleted=False
        )
        assert sorted(p.display_name for p in input_ports) == ["item_name", "qty"]


@pytest.mark.integration
class TestUpdateHttpNodeConfig:
    def _create(self, graph_version, http_template, user, organization, workspace):
        data = _http_data(node_template_id=http_template.id)
        node, _ = create_node(graph_version, data, user, organization, workspace)
        return node

    def test_update_config_persists(
        self, db, graph_version, http_template, user, organization, workspace
    ):
        node = self._create(graph_version, http_template, user, organization, workspace)
        new_config = {"method": "POST", "url": "https://api.example.com/v2"}

        updated = update_node(
            node=node,
            data={"config": new_config},
            user=user,
            organization=organization,
            workspace=workspace,
        )

        assert updated.config == new_config

    def test_update_config_reconciles_ports_add(
        self, db, graph_version, http_template, user, organization, workspace
    ):
        node = self._create(graph_version, http_template, user, organization, workspace)

        update_node(
            node=node,
            data={
                "config": {
                    "method": "GET",
                    "url": "https://api.example.com/{{user_id}}/{{region}}",
                }
            },
            user=user,
            organization=organization,
            workspace=workspace,
        )

        input_ports = Port.no_workspace_objects.filter(
            node=node, direction=PortDirection.INPUT, deleted=False
        )
        assert sorted(p.display_name for p in input_ports) == ["region", "user_id"]

    def test_update_config_reconciles_ports_remove_and_cascades_edges(
        self, db, graph_version, http_template, user, organization, workspace
    ):
        node = self._create(graph_version, http_template, user, organization, workspace)

        token_port = Port.no_workspace_objects.get(
            node=node, display_name="token", direction=PortDirection.INPUT
        )

        update_node(
            node=node,
            data={"config": {"method": "GET", "url": "https://api.example.com/static"}},
            user=user,
            organization=organization,
            workspace=workspace,
        )

        token_port.refresh_from_db()
        assert token_port.deleted is True

        remaining = Port.no_workspace_objects.filter(
            node=node, direction=PortDirection.INPUT, deleted=False
        )
        assert list(remaining) == []

    def test_update_without_config_leaves_ports_untouched(
        self, db, graph_version, http_template, user, organization, workspace
    ):
        node = self._create(graph_version, http_template, user, organization, workspace)

        update_node(
            node=node,
            data={"name": "Renamed"},
            user=user,
            organization=organization,
            workspace=workspace,
        )

        input_ports = Port.no_workspace_objects.filter(
            node=node, direction=PortDirection.INPUT, deleted=False
        )
        assert sorted(p.display_name for p in input_ports) == ["token", "user_id"]
