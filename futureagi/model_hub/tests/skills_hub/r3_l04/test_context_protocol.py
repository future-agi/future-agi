"""Whole-context roots, historical arms and progressive child delivery."""

import json
from typing import Any
from uuid import uuid4

import pytest

from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.packages import manifest_digest
from model_hub.services.skills_hub.v11.grading import grade_deterministic
from model_hub.services.skills_hub.v11.types import SkillPrincipal, SkillScope
from model_hub.services.skills_hub.v12.testing import (
    _configuration,
    build_context_task_messages,
    context_bundle,
    context_catalog,
    context_tool_definitions,
    create_context_test,
    process_context_tool,
    root_delivery_receipt,
    validate_context_cases,
)


@pytest.fixture
def snapshot(make_skill_files):
    child = make_skill_files(
        extra=[{"path": "references/policy.md", "content": "Synthetic refund only."}]
    )
    descriptor = {
        "schema": 1,
        "kind": "agent_context",
        "name": "context",
        "description": "Synthetic application policy",
        "entrypoint": "AGENTS.md",
        "owned_paths": ["AGENTS.md", "references/application.md"],
        "links": [
            {
                "mount": "skills/help-desk/",
                "skill_id": str(uuid4()),
                "version_id": str(uuid4()),
                "version": 1,
                "digest": manifest_digest(child),
                "requested": {"version": 1},
            }
        ],
    }
    files = [
        {"path": "AGENTS.md", "content": "Standing synthetic policy"},
        {"path": "references/application.md", "content": "π界😀" * 20000},
        {
            "path": ".futureagi/context.json",
            "content": json.dumps(descriptor, sort_keys=True, separators=(",", ":")),
        },
        *[{**item, "path": "skills/help-desk/" + item["path"]} for item in child],
    ]
    parent = context_bundle(
        {
            "skill_id": str(uuid4()),
            "version_id": str(uuid4()),
            "version": 4,
            "name": "context",
            "description": descriptor["description"],
            "files": files,
            "digest": manifest_digest(files),
        }
    )
    return {
        "candidate": parent,
        "production": None,
        "reference": parent,
        "plan_fingerprint": "sha256:" + "b" * 64,
    }


@pytest.fixture
def case():
    return {
        "id": str(uuid4()),
        "title": "Synthetic refund",
        "source_group_id": "synthetic",
        "prompt": "Handle a refund",
        "fixtures": [],
        "expected_trigger": None,
        "checks": [
            {
                "id": str(uuid4()),
                "kind": "contains",
                "critical": True,
                "config": {"text": "UNIQUE_EXPECTATION", "case_sensitive": True},
            }
        ],
        "reviewed": True,
    }


def tool(name, args):
    return {
        "id": str(uuid4()),
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def test_root_and_catalog_are_exact_but_expectations_are_not_task_messages(
    snapshot, case
):
    messages, catalog = build_context_task_messages(
        snapshot=snapshot, case=case, variant="reference"
    )
    assert "Standing synthetic policy" in str(messages)
    assert "UNIQUE_EXPECTATION" not in str(messages)
    assert "Synthetic refund only" not in str(messages)
    assert len(catalog) == 1
    messages, catalog = build_context_task_messages(
        snapshot=snapshot, case=case, variant="without_target"
    )
    assert "Standing synthetic policy" not in str(messages)
    assert "help-desk" not in str(messages)
    assert catalog == {}
    assert context_tool_definitions(with_context=False) == []


def test_linked_files_keep_parent_paths_and_source_identity(snapshot):
    handle = next(iter(context_catalog(snapshot=snapshot, variant="candidate")))
    active: set[str] = set()
    root, receipt, requested = process_context_tool(
        snapshot=snapshot,
        variant="candidate",
        call=tool("load_skill", {"skill_id": handle}),
        call_id=str(uuid4()),
        activated=active,
        remaining_bytes=192 * 1024,
    )
    assert root["path"] == "SKILL.md"
    assert receipt["path"] == "skills/help-desk/SKILL.md"
    assert (
        receipt["source_version_id"]
        == snapshot["candidate"]["descriptor"]["links"][0]["version_id"]
    )
    assert receipt["skill_id"] == snapshot["candidate"]["skill_id"]
    assert requested is False
    assert handle in active


def test_owned_read_does_not_bypass_child_activation(snapshot):
    response, receipt, _ = process_context_tool(
        snapshot=snapshot,
        variant="candidate",
        call=tool(
            "read_context_file",
            {"path": "skills/help-desk/SKILL.md", "offset": 0, "max_bytes": 24000},
        ),
        call_id=str(uuid4()),
        activated=set(),
        remaining_bytes=192 * 1024,
    )
    assert response["error"]["code"] == "resource_not_found"
    assert receipt["outcome"] == "rejected"


def test_utf8_owned_file_reconstructs_with_character_offsets(snapshot):
    text, offset = "", 0
    while True:
        response, receipt, _ = process_context_tool(
            snapshot=snapshot,
            variant="candidate",
            call=tool(
                "read_context_file",
                {
                    "path": "references/application.md",
                    "offset": offset,
                    "max_bytes": 24000,
                },
            ),
            call_id=str(uuid4()),
            activated=set(),
            remaining_bytes=192 * 1024,
        )
        assert len(response["text"].encode()) <= 24000
        text += response["text"]
        if response["eof"]:
            break
        assert response["next_offset"] > offset
        offset = response["next_offset"]
    assert text == "π界😀" * 20000


def test_root_delivery_is_not_activation_or_answer_uplift(snapshot, case):
    receipt = root_delivery_receipt(
        snapshot=snapshot, variant="candidate", call_id=str(uuid4())
    )
    assert receipt is not None
    assert receipt["path"] == "AGENTS.md"
    assert receipt["source_version_id"] is None
    assert receipt["eof"] is True
    assert (
        root_delivery_receipt(
            snapshot=snapshot, variant="without_target", call_id=str(uuid4())
        )
        is None
    )
    assert validate_context_cases([case]) == [case]
    candidate = grade_deterministic(
        case=case, answer="UNIQUE_EXPECTATION", receipts=[receipt], variant="candidate"
    )
    without = grade_deterministic(
        case=case, answer="UNIQUE_EXPECTATION", receipts=[], variant="without_target"
    )
    assert candidate == without
    case["expected_trigger"] = True
    with pytest.raises(SkillsHubError):
        validate_context_cases([case])


@pytest.mark.django_db
def test_context_worker_capability_cannot_be_enabled_by_testing_flag(
    organization, workspace, user, monkeypatch, settings
):
    from model_hub.models import HubSkill, HubSkillJob
    from model_hub.services.skills_hub.v11 import runner

    settings.SKILLS_TESTING_ENABLED = True
    # Simulate an older worker: a deployment flag cannot add its protocol branch.
    monkeypatch.setattr(runner, "SUPPORTED_HARNESSES", frozenset())

    context = HubSkill.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        name="synthetic-context",
        kind="agent_context",
    )
    principal = SkillPrincipal(
        SkillScope(organization.id, workspace.id), user.id, None, "human"
    )
    with pytest.raises(SkillsHubError) as error:
        create_context_test(
            principal=principal,
            context_id=context.id,
            request={},
            idempotency_key=uuid4(),
        )
    assert error.value.code == "runner_capability_missing"
    assert not HubSkillJob.objects.filter(skill=context).exists()


def test_historical_baseline_is_not_rewritten_to_production():
    config: dict[str, Any] = {
        "harness": "contexts-text-v1.2",
        "candidate": {"kind": "version", "version": 6, "digest": "sha256:" + "a" * 64},
        "cases": {"kind": "ad_hoc", "cases": []},
        "baselines": [{"kind": "version", "version": 4}],
        "competitors": [],
        "model": {"model_id": "synthetic", "temperature": 0, "max_output_tokens": 1024},
        "judge": None,
        "profile": "quick",
        "repetitions": 1,
        "limits": {"max_total_tokens": 10000, "max_estimated_cost_usd": "1.00"},
        "purpose": "regression",
        "parent_run_id": None,
    }
    normalized = _configuration(config)
    assert normalized["baselines"] == ["reference"]
    assert normalized["context_baselines"] == [{"kind": "version", "version": 4}]
    assert config["baselines"] == [{"kind": "version", "version": 4}]
    config["baselines"].append({"kind": "version", "version": 5})
    with pytest.raises(SkillsHubError):
        _configuration(config)


def test_root_digest_and_child_digest_are_enforced(snapshot):
    changed = snapshot["candidate"]
    changed["files"][0]["content"] = "not reviewed"
    with pytest.raises(SkillsHubError):
        context_bundle(changed)
    changed["digest"] = manifest_digest(changed["files"])
    root = next(
        row for row in changed["files"] if row["path"] == "skills/help-desk/SKILL.md"
    )
    root["content"] += "changed child"
    with pytest.raises(SkillsHubError):
        context_catalog(snapshot=snapshot, variant="candidate")
