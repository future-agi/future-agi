"""Skills catalog contracts and lossless reads through the real MCP executor."""

import hashlib
import json
from pathlib import Path

import pytest

from mcp_server.api_executor import (
    APIExecutionError,
    DjangoAPIExecutor,
    MCPRequestContext,
)
from mcp_server.constants import DEFAULT_TOOL_GROUPS, TOOL_GROUPS
from mcp_server.generated_registry import GeneratedToolRegistry
from mcp_server.response_limits import bounded_response
from model_hub.models.skills_hub import HubSkillUsageDaily
from model_hub.services.skills_hub.types import Actor
from model_hub.services.skills_hub.versions import publish_by_name

SKILLS = {
    "list_skills",
    "get_skill",
    "read_skill_file",
    "publish_skill",
    "set_skill_label",
    "update_skill_tags",
}


def test_skills_registry_count_and_default_group():
    registry = GeneratedToolRegistry.from_manifest()
    assert registry.count() == 113
    assert {tool.name for tool in registry.list_by_groups(["skills"])} == SKILLS
    assert "skills" in TOOL_GROUPS and "skills" in DEFAULT_TOOL_GROUPS
    for name in SKILLS:
        assert registry.get(name).annotations["readOnlyHint"] == (
            name in {"list_skills", "get_skill", "read_skill_file"}
        )
    assert (
        registry.get("list_skills").input_schema["properties"]["page_size"]["maximum"]
        == 100
    )


def test_skills_openapi_has_all_routes_and_errors():
    contract = json.loads(
        (
            Path(__file__).resolve().parents[3] / "api_contracts/openapi/swagger.json"
        ).read_text()
    )
    paths = {
        path: item
        for path, item in contract["paths"].items()
        if path.startswith("/model-hub/skills/")
    }
    assert len(paths) == 21
    methods = {"get", "post", "put", "patch", "delete"}
    assert sum(len(methods.intersection(item)) for item in paths.values()) == 28
    for item in paths.values():
        for method in methods.intersection(item):
            assert {"400", "401", "403", "404", "409", "413", "422"} <= item[method][
                "responses"
            ].keys()


@pytest.mark.django_db
def test_read_skill_file_reassembles_100kib_through_executor(
    user, organization, workspace
):
    registry = GeneratedToolRegistry.from_manifest()
    executor = DjangoAPIExecutor()
    context = MCPRequestContext(
        user=user, organization=organization, workspace=workspace
    )
    content = (
        "---\nname: large-skill\ndescription: Use when a long instruction file needs verified chunked retrieval.\n---\n\n"
        + ('A quoted "instruction" with unicode 雪 and a backslash \\.\n' * 2200)
    )
    assert len(content.encode()) > 100 * 1024
    published = publish_by_name(
        organization=organization,
        workspace=workspace,
        actor=Actor(user=user, api_key=None),
        files=[{"path": "SKILL.md", "content": content}],
        note="",
        labels=["production"],
        tags=[],
        acknowledge_warnings=True,
    )
    manifest = executor.execute_sync(
        registry.get("get_skill"),
        {"name": "large-skill", "include_content": True},
        context,
    )
    assert "content" not in manifest["files"][0]
    assert manifest["version_id"] == str(published.version.pk)
    text, offset = "", 0
    while True:
        response = executor.execute_sync(
            registry.get("read_skill_file"),
            {
                "skill_id": manifest["skill_id"],
                "number": manifest["version"],
                "path": "SKILL.md",
                "offset": offset,
            },
            context,
        )
        response = bounded_response(response)
        assert "_mcp" not in response
        assert len(json.dumps(response, ensure_ascii=False).encode()) < 65536
        assert len(response["text"].encode()) <= 24000
        assert response["sha256"] == hashlib.sha256(content.encode()).hexdigest()
        text += response["text"]
        if response["eof"]:
            assert response["next_offset"] is None
            break
        assert response["next_offset"] > offset
        offset = response["next_offset"]
    assert text.encode() == content.encode()
    assert list(HubSkillUsageDaily.objects.values_list("channel", "count")) == [
        ("mcp", 2)
    ]
    with pytest.raises(APIExecutionError) as error:
        executor.execute_sync(
            registry.get("get_skill"),
            {"name": "large-skill", "label": "missing"},
            context,
        )
    assert error.value.status_code == 404


@pytest.mark.django_db
def test_all_skill_tools_execute_against_generated_contract(
    user, organization, workspace
):
    registry = GeneratedToolRegistry.from_manifest()
    executor = DjangoAPIExecutor()
    context = MCPRequestContext(
        user=user, organization=organization, workspace=workspace
    )
    content = "---\nname: mcp-skill\ndescription: Use when a workspace needs reusable instructions for reviewing a request.\n---\n\nRead the request.\n"
    pushed = executor.execute_sync(
        registry.get("publish_skill"),
        {"files": [{"path": "SKILL.md", "content": content}]},
        context,
    )
    skill_id = pushed["skill"]["id"]
    label = executor.execute_sync(
        registry.get("set_skill_label"),
        {
            "skill_id": skill_id,
            "label": "production",
            "version": 1,
            "expected_version": None,
        },
        context,
    )
    assert label["version"] == 1
    updated = executor.execute_sync(
        registry.get("update_skill_tags"),
        {"skill_id": skill_id, "tags": ["approved"]},
        context,
    )
    assert updated["tags"] == ["approved"]
    listed = executor.execute_sync(
        registry.get("list_skills"), {"tag": "approved"}, context
    )
    assert listed["count"] == 1
    assert listed["stats"]["in_production"] == 1
