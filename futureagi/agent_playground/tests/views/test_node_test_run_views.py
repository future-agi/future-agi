"""Tests for NodeCrudViewSet.test_run — POST /nodes/{node_id}/test/.

Covers the "test a node before committing it to the workflow" feature:
running a node with sample/unsaved data must never write to Node.config,
PromptTemplateNode, or the version status.
"""

import uuid
from unittest.mock import patch

import pytest
from django.urls import reverse
from rest_framework import status

from agent_playground.models.choices import GraphVersionStatus, NodeType
from agent_playground.models.node import Node

# ── helpers ────────────────────────────────────────────────────────────


def _node_test_url(graph, version, node_id):
    return reverse(
        "graph-version-node-test",
        kwargs={"pk": graph.id, "version_id": version.id, "node_id": node_id},
    )


def _node_create_url(graph, version):
    return reverse(
        "graph-version-node-create",
        kwargs={"pk": graph.id, "version_id": version.id},
    )


def _fake_litellm_response(response_text="Hello from the model"):
    return (response_text, {"cost": 0, "tokens": 0})


# =====================================================================
# TEST RUN — llm_prompt nodes
# =====================================================================


@pytest.mark.unit
class TestTestNodeAPI:
    def test_test_run_with_unsaved_prompt_override(
        self, authenticated_client, graph, graph_version, llm_node_template
    ):
        """Test-running unsaved edits works without any PromptTemplateNode."""
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            node_template=llm_node_template,
            type=NodeType.ATOMIC,
            name="Untested LLM Node",
            config={},
            position={"x": 0, "y": 0},
        )
        url = _node_test_url(graph, graph_version, node.id)
        payload = {
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Say hi to {{name}}"}
                        ],
                    }
                ],
                "model": "gpt-4o-mini",
            },
            "inputs": {"name": "World"},
        }

        with patch(
            "agent_playground.services.engine.runners.llm_prompt.RunPrompt"
        ) as mock_run_prompt:
            mock_run_prompt.return_value.litellm_response.return_value = (
                _fake_litellm_response("Hi World!")
            )
            response = authenticated_client.post(url, payload, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] is True
        result = response.data["result"]
        assert result["status"] == "SUCCESS"
        assert result["outputs"]["response"] == "Hi World!"
        assert result["error"] is None

        # Never persisted anything on the node itself.
        node.refresh_from_db()
        assert node.config == {}

    def test_test_run_never_changes_saved_workflow(
        self, authenticated_client, graph, graph_version, llm_node_template
    ):
        """Running a test must not alter Node.config or version status."""
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            node_template=llm_node_template,
            type=NodeType.ATOMIC,
            name="Stable Node",
            config={"original": True},
            position={"x": 0, "y": 0},
        )
        url = _node_test_url(graph, graph_version, node.id)
        payload = {
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "hello"}],
                    }
                ],
                "model": "gpt-4o-mini",
            },
        }

        with patch(
            "agent_playground.services.engine.runners.llm_prompt.RunPrompt"
        ) as mock_run_prompt:
            mock_run_prompt.return_value.litellm_response.return_value = (
                _fake_litellm_response()
            )
            authenticated_client.post(url, payload, format="json")

        node.refresh_from_db()
        graph_version.refresh_from_db()
        assert node.config == {"original": True}
        assert graph_version.status == GraphVersionStatus.DRAFT

    def test_test_run_uses_saved_config_when_no_override_given(
        self, authenticated_client, graph, graph_version, llm_node_template
    ):
        """Omitting prompt_template falls back to the saved PromptTemplateNode."""
        create_url = _node_create_url(graph, graph_version)
        create_payload = {
            "id": str(uuid.uuid4()),
            "type": "atomic",
            "name": "Saved LLM Node",
            "node_template_id": str(llm_node_template.id),
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "Say {{greeting}}"}],
                    }
                ],
                "model": "gpt-4o-mini",
            },
        }
        create_response = authenticated_client.post(
            create_url, create_payload, format="json"
        )
        assert create_response.status_code == status.HTTP_201_CREATED
        node_id = create_response.data["result"]["id"]

        url = _node_test_url(graph, graph_version, node_id)
        with patch(
            "agent_playground.services.engine.runners.llm_prompt.RunPrompt"
        ) as mock_run_prompt:
            mock_run_prompt.return_value.litellm_response.return_value = (
                _fake_litellm_response("Howdy")
            )
            response = authenticated_client.post(
                url, {"inputs": {"greeting": "howdy"}}, format="json"
            )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["result"]["status"] == "SUCCESS"
        assert response.data["result"]["outputs"]["response"] == "Howdy"

    def test_test_run_reports_runner_errors_without_500(
        self, authenticated_client, graph, graph_version, llm_node_template
    ):
        """A runner exception is surfaced as a FAILED result, not a 500."""
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            node_template=llm_node_template,
            type=NodeType.ATOMIC,
            name="Broken Node",
            config={},
            position={"x": 0, "y": 0},
        )
        url = _node_test_url(graph, graph_version, node.id)
        payload = {
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "hi"}],
                    }
                ],
                # No model provided -> LLMPromptRunner raises ValueError
            },
        }

        response = authenticated_client.post(url, payload, format="json")

        assert response.status_code == status.HTTP_200_OK
        result = response.data["result"]
        assert result["status"] == "FAILED"
        assert result["outputs"] == {}
        assert "model" in result["error"].lower()

    def test_test_run_allowed_on_active_version(
        self,
        authenticated_client,
        graph,
        active_graph_version,
        llm_node_template,
    ):
        """Testing is allowed on non-draft versions since nothing is written."""
        node = Node.no_workspace_objects.create(
            graph_version=active_graph_version,
            node_template=llm_node_template,
            type=NodeType.ATOMIC,
            name="Active Node",
            config={},
            position={"x": 0, "y": 0},
        )
        url = _node_test_url(graph, active_graph_version, node.id)
        payload = {
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "hi"}],
                    }
                ],
                "model": "gpt-4o-mini",
            },
        }

        with patch(
            "agent_playground.services.engine.runners.llm_prompt.RunPrompt"
        ) as mock_run_prompt:
            mock_run_prompt.return_value.litellm_response.return_value = (
                _fake_litellm_response()
            )
            response = authenticated_client.post(url, payload, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["result"]["status"] == "SUCCESS"

    def test_test_run_rejects_subgraph_nodes(
        self,
        authenticated_client,
        graph,
        graph_version,
        active_referenced_graph_version,
    ):
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            type=NodeType.SUBGRAPH,
            ref_graph_version=active_referenced_graph_version,
            name="Sub Node",
            config={},
            position={"x": 0, "y": 0},
        )
        url = _node_test_url(graph, graph_version, node.id)

        response = authenticated_client.post(url, {}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["status"] is False

    def test_test_run_node_not_found(self, authenticated_client, graph, graph_version):
        url = _node_test_url(graph, graph_version, uuid.uuid4())

        response = authenticated_client.post(url, {}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_test_run_graph_not_found(self, authenticated_client, graph_version):
        fake_graph_id = uuid.uuid4()
        url = reverse(
            "graph-version-node-test",
            kwargs={
                "pk": fake_graph_id,
                "version_id": graph_version.id,
                "node_id": uuid.uuid4(),
            },
        )

        response = authenticated_client.post(url, {}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_test_run_no_runner_registered(
        self, authenticated_client, graph, graph_version, node_template
    ):
        """node_template (generic test fixture) has no registered runner."""
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="Unrunnable Node",
            config={},
            position={"x": 0, "y": 0},
        )
        url = _node_test_url(graph, graph_version, node.id)

        response = authenticated_client.post(url, {}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["status"] is False
