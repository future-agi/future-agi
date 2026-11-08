"""Byte identity, selector policy, hostile paths and complete-closure limits."""

import hashlib
import json
from copy import deepcopy
from uuid import UUID

import pytest

from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.packages import manifest_digest
from model_hub.services.skills_hub.v12.compiler import (
    check_context_files,
    compile_context,
)

CHILD = UUID("00000000-0000-0000-0000-000000000001")
VERSION = UUID("00000000-0000-0000-0000-000000000002")


@pytest.fixture
def definition(make_skill_files):
    child = make_skill_files(name="refund", body="Keep café bytes and CRLF.\r\n")
    selector = {"version": 8}
    return {
        "name": "support",
        "files": [
            {"path": "AGENTS.md", "content": "Use the shared refund policy.\r\n"}
        ],
        "spec": {
            "schema": 1,
            "description": "Support rules",
            "links": [{"skill_id": str(CHILD), "selector": selector}],
        },
        "lock": {
            "schema": 1,
            "links": [
                {
                    "mount": "skills/refund/",
                    "skill_id": str(CHILD),
                    "requested": selector,
                    "version": 8,
                    "version_id": str(VERSION),
                    "digest": manifest_digest(child),
                }
            ],
        },
        "child_snapshots": {VERSION: child},
    }


def test_canonical_descriptor_and_unchanged_v1_manifest_algorithm(definition):
    result = compile_context(**definition)
    expected = {
        "schema": 1,
        "kind": "agent_context",
        "name": "support",
        "description": "Support rules",
        "entrypoint": "AGENTS.md",
        "owned_paths": ["AGENTS.md"],
        "links": definition["lock"]["links"],
    }
    encoded = json.dumps(
        expected, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    assert result.descriptor == expected
    files = {file["path"]: file["content"] for file in result.files}
    assert files[".futureagi/context.json"] == encoded
    assert not encoded.endswith("\n")
    assert files["AGENTS.md"] == definition["files"][0]["content"]
    assert (
        files["skills/refund/SKILL.md"]
        == definition["child_snapshots"][VERSION][0]["content"]
    )
    manifest = [
        [path, hashlib.sha256(text.encode()).hexdigest()]
        for path, text in sorted(files.items())
    ]
    expected_digest = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(manifest, separators=(",", ":")).encode()
        ).hexdigest()
    )
    assert result.digest == expected_digest
    assert result.checks["summary"]["errors"] == 0


def test_policy_only_change_is_a_new_digest_and_inputs_are_not_mutated(definition):
    before = deepcopy(definition)
    pinned = compile_context(**definition)
    assert definition == before
    definition["spec"]["links"][0]["selector"] = {"label": "production"}
    definition["lock"]["links"][0]["requested"] = {"label": "production"}
    followed = compile_context(**definition)
    assert pinned.digest != followed.digest
    assert pinned.files[-1] == followed.files[-1]


@pytest.mark.parametrize(
    "path",
    [
        "skills",
        "skills/unowned/SKILL.md",
        ".futureagi/context.json",
        "CLAUDE.md",
        "sub/AGENTS.md",
        ".claude/settings.json",
        ".github/workflows/run.yml",
        "refs/package.json",
        "SKILL.md",
    ],
)
def test_owned_control_or_instruction_paths_are_rejected(definition, path):
    definition["files"].append({"path": path, "content": "Do not execute me"})
    with pytest.raises(SkillsHubError) as error:
        compile_context(**definition)
    assert error.value.code == "checks_failed"


@pytest.mark.parametrize(
    "path", ["../escape", "/absolute", "a\\b", "a/./b", "a /b", "a\x00b"]
)
def test_unsafe_paths_are_never_normalized(definition, path):
    definition["files"].append({"path": path, "content": "text"})
    with pytest.raises(SkillsHubError):
        compile_context(**definition)


@pytest.mark.parametrize(
    "path", ["AGENTS.md", "nested/SKILL.md", ".claude/settings.json", "CLAUDE.md"]
)
def test_child_cannot_smuggle_another_instruction_scope(definition, path):
    child = definition["child_snapshots"][VERSION]
    child.append({"path": path, "content": "nested instructions"})
    definition["lock"]["links"][0]["digest"] = manifest_digest(child)
    with pytest.raises(SkillsHubError) as error:
        compile_context(**definition)
    assert error.value.code == "unsupported_instruction_scope"


def test_child_200_files_does_not_fit_with_parent_and_descriptor(definition):
    child = definition["child_snapshots"][VERSION]
    child.extend(
        {"path": f"references/{index}.txt", "content": "x"} for index in range(199)
    )
    definition["lock"]["links"][0]["digest"] = manifest_digest(child)
    with pytest.raises(SkillsHubError) as error:
        compile_context(**definition)
    assert error.value.code == "context_closure_too_large"
    assert error.value.detail["file_count"] == 202
    assert error.value.detail["contributions"][0]["file_count"] == 200


def test_mount_prefix_depth_and_descriptor_bytes_count(definition):
    child = definition["child_snapshots"][VERSION]
    child.append({"path": "/".join(["nested"] * 7 + ["ref.md"]), "content": "x"})
    definition["lock"]["links"][0]["digest"] = manifest_digest(child)
    with pytest.raises(SkillsHubError) as error:
        compile_context(**definition)
    assert error.value.code == "context_closure_too_large"


def test_whole_tree_byte_limit_includes_descriptor():
    files = [
        {
            "path": "AGENTS.md" if index == 0 else f"ref{index}.txt",
            "content": "x" * (512 * 1024),
        }
        for index in range(4)
    ]
    with pytest.raises(SkillsHubError) as error:
        compile_context(
            name="max",
            files=files,
            spec={"schema": 1, "description": "Maximum", "links": []},
            lock={"schema": 1, "links": []},
            child_snapshots={},
        )
    assert error.value.code == "context_closure_too_large"
    assert error.value.detail["total_bytes"] > 2 * 1024 * 1024


def test_empty_root_and_case_prefix_collisions(definition):
    definition["files"][0]["content"] = " \n"
    with pytest.raises(SkillsHubError):
        compile_context(**definition)
    checks = check_context_files(
        files=[
            {"path": "AGENTS.md", "content": "x"},
            {"path": "Refs", "content": "x"},
            {"path": "refs/x", "content": "x"},
        ],
        complete=False,
    )
    assert any(item["code"] == "path_invalid" for item in checks["items"])


@pytest.mark.parametrize(
    "mutation", ["digest", "mount", "version", "extra", "duplicate", "missing"]
)
def test_untrusted_lock_cannot_substitute_identity(definition, mutation):
    link = definition["lock"]["links"][0]
    if mutation == "digest":
        link["digest"] = "sha256:" + "0" * 64
    elif mutation == "mount":
        link["mount"] = "skills/renamed/"
    elif mutation == "version":
        link["version"] = 9
    elif mutation == "extra":
        link["ignored"] = True
    elif mutation == "duplicate":
        definition["lock"]["links"].append(deepcopy(link))
    else:
        definition["child_snapshots"] = {}
    with pytest.raises(SkillsHubError):
        compile_context(**definition)


def test_secret_diagnostics_do_not_echo_source(definition):
    secret = "ghp_" + "a" * 36
    definition["files"][0]["content"] = f"Credential {secret}"
    result = compile_context(**definition)
    assert any(item["code"] == "secret_suspected" for item in result.checks["items"])
    assert secret not in json.dumps(result.checks)
    assert secret in next(
        file["content"] for file in result.files if file["path"] == "AGENTS.md"
    )


def test_twenty_leaf_closure_is_complete_and_order_independent(make_skill_files):
    links, accepted, snapshots = [], [], {}
    for index in range(1, 21):
        identity, version_id = UUID(int=index), UUID(int=100 + index)
        name = f"policy-{index}"
        files = make_skill_files(
            name=name,
            extra=[{"path": "references/rule.md", "content": "Reviewed policy"}],
        )
        selector = {"version": 8}
        links.append({"skill_id": str(identity), "selector": selector})
        accepted.append(
            {
                "skill_id": str(identity),
                "version_id": str(version_id),
                "version": 8,
                "mount": f"skills/{name}/",
                "requested": selector,
                "digest": manifest_digest(files),
            }
        )
        snapshots[version_id] = files
    args = {
        "name": "catalog",
        "files": [{"path": "AGENTS.md", "content": "Choose the relevant policy."}],
        "spec": {"schema": 1, "description": "Complete catalog", "links": links},
        "lock": {"schema": 1, "links": accepted},
        "child_snapshots": snapshots,
    }
    first = compile_context(**args)
    assert len(first.files) == 42
    assert len(first.lock["links"]) == 20
    links.reverse()
    accepted.reverse()
    assert compile_context(**args).digest == first.digest
    links.append({"skill_id": str(UUID(int=21)), "selector": {"version": 8}})
    with pytest.raises(SkillsHubError) as error:
        compile_context(**args)
    assert error.value.code == "invalid_dependency_selector"
