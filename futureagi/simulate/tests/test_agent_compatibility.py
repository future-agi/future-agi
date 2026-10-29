"""The compatibility check: two stored agent documents compared with no model call,
read per environment against the version its world was built from."""

import copy

import pytest
from rest_framework import status

from simulate.models import AgentDefinition, HostedHarnessJob
from simulate.services.agent_compatibility import (
    CLEAN,
    NEW_RULE,
    NEW_TOOL,
    TOOL_REMOVED_OR_CHANGED,
    UNMEASURED,
    compare_documents,
    environment_compatibility,
)
from simulate.services.agent_setup import store_agent
from simulate.services.hosted_harness import BUILD_PIN_KEY
from simulate.tests.test_agent_overview_api import _environment
from simulate.tests.test_agent_setup_service import source_agent

pytestmark = pytest.mark.django_db


def _with_tool(document, name, *, required=("order_id",), kind="builtin"):
    document = copy.deepcopy(document)
    document["tools"].append(
        {
            "name": name,
            "description": f"{name} it",
            "kind": kind,
            "parameters": {
                "type": "object",
                "properties": {field: {} for field in required},
                "required": list(required),
            },
            "endpoint": None,
        }
    )
    return document


def test_prompt_and_model_changes_alone_are_clean():
    before = source_agent()
    after = copy.deepcopy(before)
    after["behaviour"][0]["instructions"] = "Be brief."
    after["model"] = {"provider": "openai", "name": "gpt-6", "temperature": 0.2}

    result = compare_documents(before, after)

    assert result["status"] == CLEAN
    assert result["prompt_changed"] is True
    assert result["model_changed"] is True
    assert result["tools_added"] == []


def test_a_new_tool_or_outside_service_is_new_tool():
    before = source_agent()

    added = compare_documents(before, _with_tool(before, "issue_refund"))
    assert added["status"] == NEW_TOOL
    assert added["tools_added"] == ["issue_refund"]

    after = copy.deepcopy(before)
    after["extension"] = {
        "contract": {
            "tool_entrypoints": [{"service": "payments", "endpoint": "/refund"}],
        }
    }
    assert compare_documents(before, after)["services_added"] == ["payments"]
    assert compare_documents(before, after)["status"] == NEW_TOOL


def test_a_removed_or_changed_tool_outranks_an_added_one():
    before = _with_tool(source_agent(), "issue_refund", required=("order_id",))
    changed = _with_tool(
        source_agent(), "issue_refund", required=("order_id", "amount")
    )
    removed = source_agent()

    result = compare_documents(before, _with_tool(changed, "escalate"))
    assert result["status"] == TOOL_REMOVED_OR_CHANGED
    assert result["tools_added"] == ["escalate"]
    assert [one["name"] for one in result["tools_changed"]] == ["issue_refund"]
    assert result["tools_changed"][0]["after"]["required"] == ["amount", "order_id"]

    assert compare_documents(before, removed)["tools_removed"] == ["issue_refund"]
    assert compare_documents(before, removed)["status"] == TOOL_REMOVED_OR_CHANGED


def test_a_new_hard_constraint_is_new_rule():
    before = source_agent()
    after = copy.deepcopy(before)
    after["extension"] = {"contract": {"hard_constraints": ["One refund per order"]}}

    result = compare_documents(before, after)

    assert result["status"] == NEW_RULE
    assert result["rules_added"] == ["One refund per order"]


def _agent(organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="support",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        organization=organization,
        workspace=workspace,
    )


def _stored(version, document):
    assert store_agent(agent_version_id=version.id, agent=document, sdk_version="0.2.3")
    return version


def _built_from(environment, version):
    HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
        payload={
            **environment.payload,
            "metadata": {
                **environment.payload.get("metadata", {}),
                BUILD_PIN_KEY: str(version.id),
            },
        }
    )
    environment.refresh_from_db()
    return environment


def test_an_environment_is_checked_against_the_version_it_was_built_from(
    organization, workspace
):
    agent = _agent(organization, workspace)
    first = _stored(
        agent.create_version(description="p", commit_message="v1"), source_agent()
    )
    second = _stored(
        agent.create_version(description="p", commit_message="v2"),
        _with_tool(source_agent(), "issue_refund"),
    )
    environment = _environment(agent, 1)

    assert environment_compatibility(environment, second)["status"] == UNMEASURED

    _built_from(environment, first)
    result = environment_compatibility(environment, second)

    assert result["status"] == NEW_TOOL
    assert result["base"] == str(first.id)
    assert environment_compatibility(environment, first) == {
        "status": CLEAN,
        "base": str(first.id),
        "same_version": True,
    }


def test_a_version_with_no_document_is_unmeasured(organization, workspace):
    agent = _agent(organization, workspace)
    first = _stored(
        agent.create_version(description="p", commit_message="v1"), source_agent()
    )
    unread = agent.create_version(description="p", commit_message="v2")
    environment = _built_from(_environment(agent, 1), first)

    result = environment_compatibility(environment, unread)

    assert result == {
        "status": UNMEASURED,
        "reason": "version_not_read",
        "base": str(first.id),
    }


def test_the_compatibility_endpoint_reads_per_environment(
    auth_client, organization, workspace
):
    agent = _agent(organization, workspace)
    first = _stored(
        agent.create_version(description="p", commit_message="v1"), source_agent()
    )
    second = _stored(
        agent.create_version(description="p", commit_message="v2"),
        _with_tool(source_agent(), "issue_refund"),
    )
    environment = _built_from(_environment(agent, 1), first)
    url = f"/simulate/agent-definitions/{agent.id}/versions/{second.id}/compatibility/"

    missing = auth_client.get(url)
    assert missing.status_code == status.HTTP_400_BAD_REQUEST

    response = auth_client.get(url, {"environment_id": str(environment.id)})

    assert response.status_code == status.HTTP_200_OK, response.content
    body = response.json()
    assert body["status"] == NEW_TOOL
    assert body["tools_added"] == ["issue_refund"]
    assert body["base"] == str(first.id)
    assert body["environment_id"] == str(environment.id)
