"""Skills catalog contracts and lossless reads through the real MCP executor."""

import hashlib
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from mcp_server.api_executor import (
    APIExecutionError,
    DjangoAPIExecutor,
    MCPRequestContext,
)
from mcp_server.constants import DEFAULT_TOOL_GROUPS, TOOL_GROUPS
from mcp_server.generated_registry import GeneratedTool, GeneratedToolRegistry
from mcp_server.response_limits import bounded_response
from model_hub.models.skills_hub import HubSkillUsageDaily, HubSkillVersion
from model_hub.services.skills_hub.types import Actor
from model_hub.services.skills_hub.versions import publish_by_name

SKILL_READS = {
    "list_skills",
    "get_skill",
    "read_skill_file",
    "list_skill_versions",
    "list_agent_contexts",
    "get_agent_context",
    "list_agent_context_versions",
    "get_agent_context_snapshot",
    "get_skill_usage",
    "get_latest_skill_load",
}
SKILL_WRITES = {"publish_skill", "set_skill_label", "update_skill_tags"}
SKILLS = SKILL_READS | SKILL_WRITES


def required_tool(registry: GeneratedToolRegistry, name: str) -> GeneratedTool:
    tool = registry.get(name)
    assert tool is not None, f"Missing Skills tool: {name}"
    return tool


def test_skills_registry_count_and_default_group():
    registry = GeneratedToolRegistry.from_manifest()
    # dev's 128 generated tools plus the 13 Skills tools.
    assert registry.count() == 141
    assert {tool.name for tool in registry.list_by_groups(["skills"])} == SKILLS
    assert len(SKILL_READS) == 10 and len(SKILL_WRITES) == 3
    assert "skills" in TOOL_GROUPS and "skills" in DEFAULT_TOOL_GROUPS
    for name in SKILLS:
        tool = required_tool(registry, name)
        assert tool.annotations["readOnlyHint"] == (name in SKILL_READS)
        assert not tool.annotations["destructiveHint"]
        assert not tool.annotations["openWorldHint"]
        # Reads are plain GETs; no tool maps a caller header such as If-Match.
        assert (tool.request["method"] == "GET") == (name in SKILL_READS)
        assert not tool.request["parameters"].get("header")
    assert (
        required_tool(registry, "list_skills").input_schema["properties"]["page_size"][
            "maximum"
        ]
        == 100
    )
    manifest_tool = required_tool(registry, "get_skill")
    assert manifest_tool.input_schema["properties"]["file_limit"]["maximum"] == 25
    assert manifest_tool.input_schema["properties"]["file_offset"]["maximum"] == 200
    assert {"file_offset", "file_limit"} <= set(
        manifest_tool.request["parameters"]["query"]
    )
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
    foundation_paths = {
        "/model-hub/skills/contexts/credentials/",
        "/model-hub/skills/contexts/credentials/{credential_id}/",
    }
    for prefix in ("consumption", "contexts"):
        foundation_paths.add(f"/model-hub/skills/{prefix}/resolve/")
        for suffix in ("access-check", "files", "manifest"):
            foundation_paths.add(
                f"/model-hub/skills/{prefix}/{{resource_id}}/versions/{{number}}/{suffix}/"
            )
    integration_paths = {
        "/model-hub/skills/spend-policy/",
        "/model-hub/skills/version-safety/{version_id}/",
        "/model-hub/skills/contexts/{context_id}/bindings/",
        "/model-hub/skills/contexts/{context_id}/consumers/",
        "/model-hub/skills/contexts/{context_id}/release-state/",
        "/model-hub/skills/contexts/bindings/{binding_id}/",
        "/model-hub/skills/contexts/bindings/{binding_id}/sessions/",
        "/model-hub/skills/contexts/bindings/{binding_id}/sessions/{session_id}/access-check/",
        "/model-hub/skills/contexts/bindings/{binding_id}/receipts/",
    }
    for prefix in ("", "contexts/"):
        base = f"/model-hub/skills/{prefix}"
        integration_paths.update(
            {
                base + "{resource_id}/draft/checkpoints/",
                base + "{resource_id}/draft/checkpoints/{checkpoint_id}/restore/",
                base + "{context_id}/versions/{number}/inspection/",
                base + "{context_id}/versions/{number}/inspection/files/",
            }
        )
    # Human authoring, release review and guarded campaigns are independent
    # additions, not extra legacy leaf routes. Pin every new route and method
    # rather than weakening the retained legacy inventory or error contracts.
    authoring_operations = {}
    for base, operations in (
        (
            "/model-hub/skills/contexts/",
            {
                "": {"get", "post"},
                "capabilities/": {"get"},
                "{context_id}/": {"get"},
                "{context_id}/dependencies/apply/": {"post"},
                "{context_id}/dependencies/preview/": {"post"},
                "{context_id}/draft/": {"delete", "get", "put"},
                "{context_id}/draft/acknowledge-head/": {"post"},
                "{context_id}/draft/files/": {"get"},
                "{context_id}/draft/head-review/": {"post"},
                "{context_id}/draft/publish/": {"post"},
                "{context_id}/draft/start/": {"post"},
                "{context_id}/release/review/": {"post"},
                "{context_id}/versions/": {"get"},
                "{context_id}/versions/{number}/": {"get"},
            },
        ),
        (
            "/model-hub/skills/contexts/campaigns/",
            {
                "": {"get", "post"},
                "{campaign_id}/": {"get"},
                "{campaign_id}/authorize/": {"post"},
                "{campaign_id}/cancel/": {"post"},
                "{campaign_id}/pause/": {"post"},
                "{campaign_id}/resume/": {"post"},
                "{campaign_id}/rollback/preview/": {"post"},
                "{campaign_id}/targets/": {"get"},
                "{campaign_id}/targets/{binding_id}/attempts/": {"post"},
                "{campaign_id}/targets/{binding_id}/claim/": {"post"},
                "{campaign_id}/targets/{binding_id}/observations/": {"get"},
                "{campaign_id}/targets/{binding_id}/plan/": {"put"},
            },
        ),
        (
            "/model-hub/skills/",
            {
                "release-requests/": {"get", "post"},
                "release-requests/preview/": {"post"},
                "release-requests/{request_id}/": {"get"},
                "release-requests/{request_id}/decisions/": {"post"},
                "release-requests/{request_id}/execute/": {"post"},
                "release-requests/{request_id}/receipt/": {"get"},
                "review-inbox/": {"get"},
                "review-inbox/{request_id}/seen/": {"post"},
            },
        ),
    ):
        authoring_operations.update(
            {base + suffix: allowed for suffix, allowed in operations.items()}
        )
    workflow_operations = {
        "/model-hub/skills/" + suffix: allowed
        for suffix, allowed in {
            "intake/preview/": {"post"},
            "intake/langfuse/preview/": {"post"},
            "intake/{plan_id}/": {"get"},
            "intake/{plan_id}/accept/": {"post"},
            "intake/{plan_id}/reconcile/": {"post"},
            "intake/{plan_id}/review/": {"post"},
            "intake/{plan_id}/receipts/": {"get"},
            "{resource_id}/versions/{number}/migration-export/": {"get"},
            "contexts/{context_id}/captures/source/": {"post"},
            "contexts/{context_id}/captures/preview/": {"post"},
            "contexts/{context_id}/captures/{capture_id}/accept/": {"post"},
            "evidence/{source}/{source_id}/": {"get"},
            "evidence/{source}/{source_id}/annotations/": {"get", "post"},
            "{skill_id}/context-dependents/": {"get"},
            "contexts/{context_id}/test-runs/": {"get", "post"},
            "contexts/{context_id}/test-runs/preview/": {"post"},
            "contexts/{context_id}/test-runs/{run_id}/": {"get"},
            "contexts/{context_id}/test-runs/{run_id}/results/": {"get"},
            "contexts/{context_id}/test-runs/{run_id}/attempts/{attempt_id}/": {"get"},
            "contexts/{context_id}/test-runs/{run_id}/files/": {"get"},
            "contexts/{context_id}/test-runs/{run_id}/cancel/": {"post"},
            "contexts/{context_id}/test-runs/{run_id}/share/": {"post"},
        }.items()
    }
    # Application verification (native runs and the authenticated guest channel),
    # the whole-context Tests surface and release-request inspection/renewal are
    # a further independent addition. Pinned route by route, like the groups above.
    native_operations = {
        "/model-hub/skills/" + suffix: allowed
        for suffix, allowed in {
            "contexts/{context_id}/native/preview/": {"post"},
            "contexts/{context_id}/native/runs/": {"get", "post"},
            "contexts/{context_id}/native/runs/{evidence_id}/": {"get"},
            "contexts/{context_id}/native/runs/{evidence_id}/share/": {"post"},
            "contexts/{context_id}/native/runtime-configuration/": {"get"},
            "contexts/{context_id}/native/targets/": {"get", "post"},
            "contexts/{context_id}/native/targets/{binding_id}/": {"put"},
            "contexts/{context_id}/tests/": {"get", "put"},
            "native/attempts/{attempt_id}/failure/": {"post"},
            "native/attempts/{attempt_id}/health/": {"post"},
            "native/attempts/{attempt_id}/observations/": {"post"},
            "release-requests/inspection/": {"post"},
            "release-requests/native-evidence/{evidence_id}/share/": {"post"},
            "release-requests/options/": {"get"},
            "release-requests/{request_id}/renewal/": {"get", "post"},
        }.items()
    }
    authoring_paths = set(authoring_operations)
    workflow_paths = set(workflow_operations)
    native_paths = set(native_operations)
    assert len(authoring_paths) == 34
    assert len(workflow_paths) == 22
    assert len(native_paths) == 15
    assert foundation_paths <= paths.keys()
    assert integration_paths <= paths.keys()
    assert authoring_paths <= paths.keys()
    assert workflow_paths <= paths.keys()
    assert native_paths <= paths.keys()
    assert foundation_paths.isdisjoint(integration_paths)
    assert authoring_paths.isdisjoint(foundation_paths | integration_paths)
    assert workflow_paths.isdisjoint(
        foundation_paths | integration_paths | authoring_paths
    )
    assert native_paths.isdisjoint(
        foundation_paths | integration_paths | authoring_paths | workflow_paths
    )
    runtime_operations = {
        f"/model-hub/skills/{resource}/": {"get"}
        for resource in ("usage", "comparison", "loads", "filter-values")
    }
    runtime_paths = set(runtime_operations)
    assert runtime_paths <= paths.keys()
    assert runtime_paths.isdisjoint(
        foundation_paths
        | integration_paths
        | authoring_paths
        | workflow_paths
        | native_paths
    )
    legacy_paths = {
        path: item
        for path, item in paths.items()
        if path
        not in foundation_paths
        | integration_paths
        | authoring_paths
        | workflow_paths
        | native_paths
        | runtime_paths
    }
    # A8 adds the admin-only archived list, restore review and restore to the human (legacy) surface.
    archived_operations = {
        "/model-hub/skills/archived/": {"get"},
        "/model-hub/skills/archived/{resource_id}/restore-review/": {"get"},
        "/model-hub/skills/archived/{resource_id}/restore/": {"post"},
    }
    for path, allowed in archived_operations.items():
        assert {"get", "post", "put", "patch", "delete"}.intersection(
            legacy_paths[path]
        ) == allowed, path
    assert len(legacy_paths) == 49
    assert len(paths) == 151
    methods = {"get", "post", "put", "patch", "delete"}
    for path, allowed in (
        authoring_operations
        | workflow_operations
        | native_operations
        | runtime_operations
    ).items():
        assert methods.intersection(paths[path]) == allowed, path
    assert sum(map(len, authoring_operations.values())) == 39
    assert sum(map(len, workflow_operations.values())) == 24
    assert sum(map(len, native_operations.values())) == 19
    assert sum(len(methods.intersection(item)) for item in legacy_paths.values()) == 61
    assert (
        sum(len(methods.intersection(paths[path])) for path in foundation_paths) == 11
    )
    assert (
        sum(len(methods.intersection(paths[path])) for path in integration_paths) == 23
    )
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
        expected_release_revision=0,
        tags=[],
        acknowledge_warnings=True,
    )
    manifest = executor.execute_sync(
        required_tool(registry, "get_skill"),
        {"name": "large-skill", "include_content": True},
        context,
    )
    assert "content" not in manifest["files"][0]
    assert manifest["version_id"] == str(published.version.pk)
    text, offset = "", 0
    while True:
        response = executor.execute_sync(
            required_tool(registry, "read_skill_file"),
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
            required_tool(registry, "get_skill"),
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
        required_tool(registry, "publish_skill"),
        {"files": [{"path": "SKILL.md", "content": content}]},
        context,
    )
    skill_id = pushed["skill"]["id"]
    before = executor.execute_sync(required_tool(registry, "list_skills"), {}, context)[
        "results"
    ][0]
    assert before["protected_label_names"] == ["production"]
    assert before["release_revision"] == 0
    label = executor.execute_sync(
        required_tool(registry, "set_skill_label"),
        {
            "skill_id": skill_id,
            "label": "production",
            "version": 1,
            "expected_version": None,
            "expected_release_revision": before["release_revision"],
        },
        context,
    )
    assert label["version"] == 1
    updated = executor.execute_sync(
        required_tool(registry, "update_skill_tags"),
        {"skill_id": skill_id, "tags": ["approved"]},
        context,
    )
    assert updated["tags"] == ["approved"]
    listed = executor.execute_sync(
        required_tool(registry, "list_skills"), {"tag": "approved"}, context
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
    versions: list[HubSkillVersion] = []
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
        required_tool(registry, "set_skill_label"),
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
            required_tool(registry, "set_skill_label"),
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
        expected_release_revision=0,
    ).version
    arguments = {"name": "many-files"}
    received: list[dict[str, str | int]] = []
    while True:
        manifest = executor.execute_sync(
            required_tool(registry, "get_skill"), arguments, context
        )
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
        required_tool(registry, "read_skill_file"),
        {
            "skill_id": manifest["skill_id"],
            "number": manifest["version"],
            "path": last["path"],
        },
        context,
    )
    assert chunk["eof"] and chunk["text"] == last["content"]
    assert chunk["sha256"] == hashlib.sha256(last["content"].encode()).hexdigest()


def _publish_context(user, organization, workspace, files):
    """Create and publish an agent context through the real authoring services."""
    from model_hub.models.skills_hub import HubSkill
    from model_hub.services.skills_hub.v11.types import SkillPrincipal, SkillScope
    from model_hub.services.skills_hub.v12.authoring import (
        acknowledge_context_head,
        create_context,
        get_compiled_draft,
        publish_context_draft,
        review_context_head,
    )

    principal = SkillPrincipal(
        SkillScope(organization.pk, workspace.pk), user.pk, None, "human"
    )
    created = create_context(
        principal=principal,
        name="support-context",
        description="Support handling with a reviewed shared policy.",
        tags=["support"],
        files=files,
        idempotency_key=uuid4(),
    )
    context = HubSkill.no_workspace_objects.get(pk=UUID(created["context_id"]))
    draft = context.drafts.get(user_id=user.pk)
    compiled = get_compiled_draft(
        principal=principal, context_id=context.pk, revision=draft.revision
    )
    review = review_context_head(
        principal=principal, context_id=context.pk, revision=draft.revision
    )
    draft = acknowledge_context_head(
        principal=principal,
        context_id=context.pk,
        revision=draft.revision,
        expected_digest=compiled.digest,
        expected_head_version=review["expected_head_version"],
        review_fingerprint=review["review_fingerprint"],
    )
    version = publish_context_draft(
        principal=principal,
        context_id=context.pk,
        revision=draft.revision,
        base_version=draft.base_version,
        expected_digest=compiled.digest,
        expected_head_version=draft.expected_head_version,
        review_fingerprint=draft.head_review_fingerprint,
        acknowledge_warnings=True,
        note="first snapshot",
        idempotency_key=uuid4(),
    ).version
    return context, version


@pytest.mark.django_db
def test_history_and_context_reads_are_inspection_only(user, organization, workspace):
    registry = GeneratedToolRegistry.from_manifest()
    executor = DjangoAPIExecutor()
    context = MCPRequestContext(
        user=user, organization=organization, workspace=workspace
    )
    actor = Actor(user=user, api_key=None)
    for body in ("Read the request.", "Read the updated request."):
        published = publish_by_name(
            organization=organization,
            workspace=workspace,
            actor=actor,
            files=[
                {
                    "path": "SKILL.md",
                    "content": (
                        "---\nname: history-reads\n"
                        "description: Use when a workspace needs reusable instructions for reviewing a request.\n"
                        f"---\n\n{body}\n"
                    ),
                }
            ],
            note=f"note for {body}",
            labels=[],
            tags=[],
            acknowledge_warnings=True,
        )
    skill_id = str(published.version.skill_id)
    page = executor.execute_sync(
        required_tool(registry, "list_skill_versions"),
        {"skill_id": skill_id, "page_size": 1},
        context,
    )
    assert page["count"] == 2 and page["total_pages"] == 2
    assert [row["number"] for row in page["results"]] == [2]
    assert page["results"][0]["digest"] == published.version.digest
    second = executor.execute_sync(
        required_tool(registry, "list_skill_versions"),
        {"skill_id": skill_id, "page_size": 1, "page": 2},
        context,
    )
    assert [row["number"] for row in second["results"]] == [1]

    files = [{"path": "AGENTS.md", "content": "# Instructions\n\nBe precise.\n"}] + [
        {"path": f"docs/ref-{index:02}.md", "content": f"Reference {index}.\n"}
        for index in range(29)
    ]
    agent_context, context_version = _publish_context(
        user, organization, workspace, files
    )
    listed = executor.execute_sync(
        required_tool(registry, "list_agent_contexts"), {"tag": "support"}, context
    )
    assert [row["id"] for row in listed["results"]] == [str(agent_context.pk)]
    assert listed["results"][0]["kind"] == "agent_context"
    # Leaf skills never appear in the context list.
    assert skill_id not in {row["id"] for row in listed["results"]}
    detail = executor.execute_sync(
        required_tool(registry, "get_agent_context"),
        {"context_id": str(agent_context.pk)},
        context,
    )
    assert detail["context"]["id"] == str(agent_context.pk)
    assert detail["latest"]["number"] == context_version.number
    assert detail["release_state"]["latest_version"] == context_version.number
    versions = executor.execute_sync(
        required_tool(registry, "list_agent_context_versions"),
        {"context_id": str(agent_context.pk)},
        context,
    )
    assert [row["digest"] for row in versions["results"]] == [context_version.digest]

    received, offset = [], 0
    while True:
        snapshot = executor.execute_sync(
            required_tool(registry, "get_agent_context_snapshot"),
            {
                "context_id": str(agent_context.pk),
                "number": context_version.number,
                "file_offset": offset,
            },
            context,
        )
        assert len(json.dumps(snapshot, ensure_ascii=False).encode()) < 65536
        assert snapshot["distribution_blocked"] is False
        assert snapshot["descriptor"]["entrypoint"] == "AGENTS.md"
        manifest = snapshot["manifest"]
        assert manifest["digest"] == context_version.digest
        # Publishing adds the generated context descriptor to the authored files.
        assert manifest["file_count"] == len(files) + 1
        assert 0 < len(manifest["files"]) <= 25
        received.extend(file["path"] for file in manifest["files"])
        if manifest["next_file_offset"] is None:
            break
        offset = manifest["next_file_offset"]
    assert len(received) == len(set(received)) == len(files) + 1
    assert received == sorted(received)
    assert {file["path"] for file in files} < set(received)
    assert snapshot["descriptor"]["descriptor_path"] in received

    # A user-bound MCP key is a private principal, so key-authenticated MCP
    # sessions reach the private context reads as well.
    from accounts.models import OrgApiKey

    key = OrgApiKey.objects.create(
        name="Synthetic MCP key",
        type="mcp",
        user=user,
        api_key=f"test-{uuid4()}",
        secret_key=f"test-{uuid4()}",
        organization=organization,
        workspace=workspace,
    )
    keyed = MCPRequestContext(
        user=user, organization=organization, workspace=workspace, api_key=key
    )
    for name, arguments in (
        ("get_agent_context", {"context_id": str(agent_context.pk)}),
        (
            "get_agent_context_snapshot",
            {"context_id": str(agent_context.pk), "number": context_version.number},
        ),
    ):
        assert executor.execute_sync(required_tool(registry, name), arguments, keyed)

    # Context IDs are rejected by leaf-skill history, and inspection reads
    # record neither fetches nor loads.
    with pytest.raises(APIExecutionError) as error:
        executor.execute_sync(
            required_tool(registry, "list_skill_versions"),
            {"skill_id": str(agent_context.pk)},
            context,
        )
    assert error.value.status_code == 404
    assert not HubSkillUsageDaily.objects.exists()


@pytest.mark.django_db
def test_usage_and_load_reads_never_report_a_fetch_as_a_load(
    user, organization, workspace, monkeypatch
):
    from django.utils import timezone

    from model_hub.services.skills_hub import runtime_api, runtime_loads

    registry = GeneratedToolRegistry.from_manifest()
    executor = DjangoAPIExecutor()
    context = MCPRequestContext(
        user=user, organization=organization, workspace=workspace
    )
    version = publish_by_name(
        organization=organization,
        workspace=workspace,
        actor=Actor(user=user, api_key=None),
        files=[
            {
                "path": "SKILL.md",
                "content": "---\nname: runtime-reads\ndescription: Use when a workspace needs reusable instructions for reviewing a request.\n---\n\nRead the request.\n",
            }
        ],
        note="",
        labels=[],
        tags=[],
        acknowledge_warnings=True,
    ).version
    now = timezone.now()
    HubSkillUsageDaily.objects.create(
        skill=version.skill,
        version=version,
        day=now.date(),
        channel="sdk",
        consumer_key="credential:private",
        consumer_label="private credential",
        sdk_version="0.8.0",
        sdk_language="python",
        tracing_mode="on",
        count=1,
        last_at=now,
    )

    class EmptyAnalytics:
        def execute_ch_query(self, sql, params, **kwargs):
            return SimpleNamespace(data=[], row_count=0)

    monkeypatch.setattr(runtime_api, "V2AnalyticsQueryService", EmptyAnalytics)
    monkeypatch.setattr(runtime_loads, "V2AnalyticsQueryService", EmptyAnalytics)

    latest = executor.execute_sync(
        required_tool(registry, "get_latest_skill_load"),
        {
            "resource_id": str(version.skill_id),
            "kind": "skill",
            "since": (now - timedelta(minutes=5)).isoformat(),
        },
        context,
    )
    assert latest["load"] is None
    assert latest["fetch"]["sdk_version"] == "0.8.0"
    assert latest["fetch"]["can_record"] is True
    assert "private" not in json.dumps(latest)

    usage = executor.execute_sync(
        required_tool(registry, "get_skill_usage"),
        {
            "from": (now - timedelta(days=1)).isoformat(),
            "to": now.isoformat(),
            "skill_ids": str(version.skill_id),
            "limit": 100,
        },
        context,
    )
    assert usage["rows"] == []
    assert usage["next_cursor"] is None
    assert len(json.dumps(usage, ensure_ascii=False).encode()) < 65536
    with pytest.raises(APIExecutionError) as error:
        executor.execute_sync(
            required_tool(registry, "get_skill_usage"),
            {"from": now.isoformat(), "to": now.isoformat(), "limit": 101},
            context,
        )
    assert error.value.status_code == 400
