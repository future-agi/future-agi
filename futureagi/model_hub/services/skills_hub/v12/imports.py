"""Retained AGENTS-only intake using the shared bounded archive reader.

No embedded skill is silently registered or discarded. This is an owned-input
inventory; publication still uses the canonical context compiler and lock.
"""

from __future__ import annotations

import copy
import hashlib
import json
import posixpath
from uuid import UUID

from django.db import transaction

from model_hub.models.skills_hub_r3 import HubSkillIntakePlan
from model_hub.selectors.skills_hub_v12 import get_resource
from model_hub.services.skills_hub.checks import estimate_tokens, run_checks
from model_hub.services.skills_hub.drafts import _validate_payload
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.limits import (
    MAX_FILES,
    MAX_VERSION_BYTES,
)
from model_hub.services.skills_hub.r3.import_plans import (
    accept_retained_item,
    checked_plan_item,
    head_identity,
    lock_intake_workspace,
    operation_replay,
    read_upload_groups,
    rejection_blocks,
    retained_plan,
    unassigned_members,
)
from model_hub.services.skills_hub.types import FileInput
from model_hub.services.skills_hub.v11.types import SkillPrincipal
from model_hub.services.skills_hub.v12.credentials import canonical_digest
from model_hub.services.skills_hub.versions import _validate_identity


def _owned_blockers(files: list[FileInput]) -> list[dict]:
    blockers = []
    for file in files:
        path = file["path"]
        parts = path.casefold().split("/")
        if parts[-1] == "skill.md" or parts[0] == "skills":
            blockers.append({"path": path, "reason": "embedded_skill_unregistered"})
        elif (
            parts[-1]
            in {
                "agents.md",
                "agents.override.md",
                "claude.md",
                "claude.local.md",
                "gemini.md",
                "copilot-instructions.md",
            }
            and path != "AGENTS.md"
        ):
            blockers.append({"path": path, "reason": "unsupported_instruction_scope"})
        elif parts[0] in {
            ".futureagi",
            ".claude",
            ".agents",
            ".cursor",
            ".gemini",
            ".vscode",
            ".github",
        } or parts[-1] in {".mcp.json", "package.json", "pyproject.toml"}:
            blockers.append({"path": path, "reason": "destination_forbidden"})
    root = next((row["content"] for row in files if row["path"] == "AGENTS.md"), "")
    if not root.strip():
        blockers.append({"path": "AGENTS.md", "reason": "root_missing"})
    try:
        _validate_payload(files, "")
    except SkillsHubError as exc:
        blockers.append({"path": "", "reason": exc.code})
    if len(files) + 1 > MAX_FILES:
        blockers.append(
            {"path": ".futureagi/context.json", "reason": "context_closure_too_large"}
        )
    return blockers


def _owned_checks(files: list[FileInput], blockers: list[dict]) -> dict:
    # Reuse file/link/secret diagnostics without manufacturing SKILL.md. Only the
    # legacy entrypoint requirement is replaced by the explicit AGENTS inventory.
    checks = run_checks(files)
    items = [item for item in checks["items"] if item["code"] != "root_missing"]
    items.extend(
        {
            "code": blocker["reason"],
            "severity": "error",
            "path": blocker["path"],
            "message": "This input cannot be included in the managed context.",
        }
        for blocker in blockers
    )
    checks["items"] = items
    checks["summary"] = {
        key: sum(item["severity"] == severity for item in items)
        for key, severity in (
            ("errors", "error"),
            ("warnings", "warning"),
            ("infos", "info"),
        )
    }
    checks["tokens"]["skill_md"] = estimate_tokens(
        next((file["content"] for file in files if file["path"] == "AGENTS.md"), "")
    )
    checks["validation_scope"] = "authored_files"
    return checks


def _discover(files: dict[str, str], source: str) -> list[dict]:
    roots: list[str] = []
    for path in sorted(files, key=lambda value: (value.count("/"), value)):
        if posixpath.basename(path) == "AGENTS.md":
            root = posixpath.dirname(path)
            if not any(not parent or root.startswith(parent + "/") for parent in roots):
                roots.append(root)
    result = []
    for root in roots:
        prefix = root + "/" if root else ""
        owned: list[FileInput] = [
            {"path": path[len(prefix) :], "content": text}
            for path, text in sorted(files.items())
            if path.startswith(prefix)
        ]
        result.append(
            {
                "key": canonical_digest([source, root]),
                "root": root,
                "kind": "agent_context",
                "upload_source": source,
                "files": owned,
                "blockers": _owned_blockers(owned),
                "destination_id": None,
                "source": None,
                **head_identity(None),
            }
        )
    return result


@transaction.atomic
def preview_context_import(
    *, principal: SkillPrincipal, uploads: list[tuple[str, bytes]]
) -> HubSkillIntakePlan:
    lock_intake_workspace(principal)
    groups, rejected, skipped = read_upload_groups(uploads)
    items = []
    for group in groups:
        discovered = _discover(group["files"], group["source"])
        items.extend(discovered)
        rejected.extend(
            {**finding, "source": group["source"]}
            for finding in unassigned_members(group["files"], discovered)
        )
        if not discovered:
            rejected.append(
                {
                    "path": group["filename"] or "",
                    "reason": "root_missing",
                    "source": group["source"],
                }
            )
    for item in items:
        item["blockers"].extend(
            copy.deepcopy(finding)
            for finding in rejected
            if rejection_blocks(finding, item, items)
        )
        item["checks"] = _owned_checks(item["files"], item["blockers"])
    return retained_plan(
        principal=principal,
        kind="context_import",
        items=items,
        rejected=rejected,
        skipped=skipped,
        upload_digest=canonical_digest(
            [[path, hashlib.sha256(raw).hexdigest()] for path, raw in uploads]
        ),
    )


@transaction.atomic
def accept_context_import(
    *,
    principal: SkillPrincipal,
    preview_id: UUID,
    root_key: str,
    expected_fingerprint: str,
    name: str,
    description: str,
    tags: list[str],
    rights_confirmed: bool,
    idempotency_key: UUID,
) -> dict:
    lock_intake_workspace(principal)
    request = {
        "plan_id": str(preview_id),
        "item_key": root_key,
        "expected_fingerprint": expected_fingerprint,
        "name": name,
        "description": description,
        "tags": tags,
        "rights_confirmed": rights_confirmed,
        "expected_head_version": None,
        "expected_head_digest": None,
        "decision": "replace",
    }
    replay = operation_replay(
        principal=principal,
        operation="context.import",
        idempotency_key=idempotency_key,
        request=request,
    )
    if replay is not None:
        get_resource(
            principal=principal,
            resource_id=UUID(replay["resource_id"]),
            kind="agent_context",
        )
        return replay
    plan, item = checked_plan_item(
        principal=principal,
        plan_id=preview_id,
        expected_fingerprint=expected_fingerprint,
        item_key=root_key,
        kind="context_import",
    )
    _validate_identity(name, description, tags)
    item = {**item, "name": name, "description": description}
    # Intake accounts for the generated descriptor even with zero linked skills.
    # This is a capacity check, not a substitute compiled release or checks result.
    descriptor = {
        "schema": 1,
        "kind": "agent_context",
        "name": name,
        "description": description,
        "entrypoint": "AGENTS.md",
        "owned_paths": sorted(file["path"] for file in item["files"]),
        "links": [],
    }
    descriptor_bytes = len(
        json.dumps(
            descriptor, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    )
    if (
        sum(len(file["content"].encode("utf-8")) for file in item["files"])
        + descriptor_bytes
        > MAX_VERSION_BYTES
    ):
        raise SkillsHubError(
            "context_closure_too_large",
            "The owned files plus generated descriptor exceed 2 MiB.",
            status=422,
        )
    return accept_retained_item(
        principal=principal,
        plan=plan,
        item=item,
        request=request,
        idempotency_key=idempotency_key,
        operation="context.import",
        tags=tags,
    )
