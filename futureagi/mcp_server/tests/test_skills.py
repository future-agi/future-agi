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
    manifest_tool = registry.get("get_skill")
    assert manifest_tool.input_schema["properties"]["file_limit"]["maximum"] == 25
    assert manifest_tool.input_schema["properties"]["file_offset"]["maximum"] == 200
    assert {"file_offset", "file_limit"} <= set(manifest_tool.request["parameters"]["query"])
    assert "next_file_offset" in manifest_tool.description


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
    assert sum(len(methods.intersection(item)) for item in paths.values()) == 30
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


@pytest.mark.django_db
def test_set_history_label_through_executor(user, organization, workspace):
    registry = GeneratedToolRegistry.from_manifest()
    executor = DjangoAPIExecutor()
    context = MCPRequestContext(
        user=user, organization=organization, workspace=workspace
    )
    versions = []
    for body in ("Read the request.", "Read the updated instructions."):
        versions.append(
            publish_by_name(
                organization=organization,
                workspace=workspace,
                actor=Actor(user=user, api_key=None),
                files=[
                    {
                        "path": "SKILL.md",
                        "content": (
                            "---\nname: history-skill\n"
                            "description: Use when a workspace needs reusable instructions for reviewing a request.\n"
                            f"---\n\n{body}\n"
                        ),
                    }
                ],
                note="",
                labels=["history"] if not versions else [],
                tags=[],
                acknowledge_warnings=True,
            ).version
        )
    skill = versions[0].skill
    moved = executor.execute_sync(
        registry.get("set_skill_label"),
        {
            "skill_id": str(skill.pk),
            "label": "history",
            "version": 2,
            "expected_version": 1,
        },
        context,
    )
    assert moved["name"] == "history" and moved["version_id"] == str(versions[1].pk)
    with pytest.raises(APIExecutionError) as error:
        executor.execute_sync(
            registry.get("set_skill_label"),
            {
                "skill_id": str(skill.pk),
                "label": "history",
                "version": 1,
                "expected_version": 1,
            },
            context,
        )
    assert error.value.status_code == 409
    assert skill.labels.get(name="history").version_id == versions[1].pk


@pytest.mark.django_db
@pytest.mark.parametrize("path_character", ["x", "雪"])
def test_get_skill_pages_maximum_manifest_through_executor(
    user, organization, workspace, path_character
):
    registry = GeneratedToolRegistry.from_manifest()
    executor = DjangoAPIExecutor()
    context = MCPRequestContext(
        user=user, organization=organization, workspace=workspace
    )
    # 200 files, 255-character paths and legal frontmatter whose expanded metadata
    # would exceed the response budget. SKILL.md remains the lossless source.
    content = (
        "---\nname: many-files\n"
        "description: Use when every supporting file must be discoverable without truncation.\n"
        "metadata:\n  original: &original "
        + "m" * 12000
        + "\n"
        + "".join(f"  copy{index}: *original\n" for index in range(8))
        + "---\n\nRead only the references needed for the task.\n"
    )
    files = [{"path": "SKILL.md", "content": content}] + [
        {
            "path": "references/" + path_character * 236 + f"-{index:04}.md",
            "content": f"Reference {index}.",
        }
        for index in range(199)
    ]
    assert len(files) == 200 and len(files[-1]["path"]) == 255
    published = publish_by_name(
        organization=organization,
        workspace=workspace,
        actor=Actor(user=user, api_key=None),
        files=files,
        note="",
        labels=["production"],
        tags=[],
        acknowledge_warnings=True,
    ).version
    arguments = {"name": "many-files"}
    received = []
    while True:
        manifest = executor.execute_sync(registry.get("get_skill"), arguments, context)
        assert len(json.dumps(manifest, ensure_ascii=False).encode()) < 65536
        assert "_mcp" not in manifest and "frontmatter" not in manifest
        assert manifest["version_id"] == str(published.pk)
        assert manifest["digest"] == published.digest
        assert manifest["file_count"] == 200
        assert manifest["file_offset"] == len(received)
        assert 0 < len(manifest["files"]) <= 25
        received.extend(manifest["files"])
        offset = manifest["next_file_offset"]
        if offset is None:
            break
        assert offset == len(received)
        # Pin continuation requests even if the original request selected a label.
        arguments = {
            "name": "many-files",
            "version": manifest["version"],
            "file_offset": offset,
        }
    expected = [
        {
            "path": file["path"],
            "size_bytes": len(file["content"].encode()),
            "sha256": hashlib.sha256(file["content"].encode()).hexdigest(),
        }
        for file in sorted(files, key=lambda item: item["path"])
    ]
    assert received == expected
    # The final, formerly unreachable file remains readable using immutable IDs.
    last = files[-1]
    chunk = executor.execute_sync(
        registry.get("read_skill_file"),
        {
            "skill_id": manifest["skill_id"],
            "number": manifest["version"],
            "path": last["path"],
        },
        context,
    )
    assert chunk["eof"] and chunk["text"] == last["content"]
    assert chunk["sha256"] == hashlib.sha256(last["content"].encode()).hexdigest()
