"""Reviewed one-shot assistance. Generation never publishes or applies a draft."""

from __future__ import annotations

import asyncio
import hashlib
import json
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from functools import partial
from typing import TYPE_CHECKING
from uuid import UUID

import yaml
from asgiref.sync import sync_to_async
from django.db import transaction
from django.utils import timezone

from model_hub.models.skills_hub import HubSkill, HubSkillDraft
from model_hub.services.skills_hub.checks import (
    parse_frontmatter,
    run_checks,
    text_is_valid,
)
from model_hub.services.skills_hub.drafts import save_draft
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.packages import manifest_digest

if TYPE_CHECKING:
    from model_hub.models.skills_hub_v11 import HubSkillAssistProposal
    from model_hub.services.skills_hub.v11.types import SkillPrincipal, SkillScope

from model_hub.services.skills_hub.v11.limits import (
    MAX_ASSIST_INPUT_BYTES as MAX_INPUT_BYTES,
)
from model_hub.services.skills_hub.v11.limits import (
    MAX_ASSIST_OUTPUT_BYTES as MAX_OUTPUT_BYTES,
)
from model_hub.services.skills_hub.v11.limits import (
    MAX_ASSIST_OUTPUT_TOKENS as MAX_OUTPUT_TOKENS,
)
from model_hub.services.skills_hub.v11.limits import (
    MAX_OUTBOUND_FINDINGS as MAX_FINDINGS,
)

PROMPTS = {
    "draft": (
        "Propose an Agent Skills text bundle for the named task. Use only supplied\n"
        "policy, examples and constraints. Do not invent company policy, prices,\n"
        "permissions or external actions. Represent missing domain facts as clear\n"
        "TODOs and list questions. Return JSON {files:[{path,content}],missing_information:[]}.\n"
        "The root must be SKILL.md with the supplied immutable name and valid description.\n"
        "Do not create executable files, test answer keys, credentials or hidden files.\n"
        "The proposal is for review, not publication."
    ),
    "description": (
        "Propose only a replacement skill description, at most 1024 characters.\n"
        "Use the supplied skill purpose and reviewed routing examples to clarify when\n"
        "the skill should and should not be selected. Do not change the body, broaden\n"
        "it to unrelated tasks, copy expected answers or claim verified improvement.\n"
        "Return JSON {description,reason,tradeoffs:[]}."
    ),
    "summary": (
        "Write a concise version note from this exact before/after file diff.\n"
        "Describe only visible changes. Do not invent tests, outcomes, approvals or\n"
        "security guarantees. Return JSON {summary}; maximum 2000 characters."
    ),
}


def _json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
        allow_nan=False,
    )


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _invalid(
    message: str, *, code: str = "invalid_test_config", status: int = 400
) -> SkillsHubError:
    return SkillsHubError(code, message, status=status)


def _bounded_text(value: object, maximum: int, *, nonempty: bool = True) -> bool:
    return (
        isinstance(value, str)
        and text_is_valid(value)
        and len(value) <= maximum
        and (bool(value.strip()) or not nonempty)
    )


def _output_error() -> SkillsHubError:
    return _invalid(
        "The model did not return a safe, complete proposal. Review the inputs before trying again.",
        code="assist_output_invalid",
        status=422,
    )


def replace_description(
    files: list[dict], description: str, *, name: str
) -> list[dict]:
    """Replace one YAML scalar server-side, leaving unrelated frontmatter/body intact."""
    if not _bounded_text(description, 1024):
        raise _output_error()
    result = deepcopy(files)
    root = next((file for file in result if file["path"] == "SKILL.md"), None)
    if root is None:
        raise _output_error()
    original = root["content"]
    frontmatter, body, errors = parse_frontmatter(original)
    if errors or frontmatter is None:
        raise _output_error()
    lines = original.splitlines(keepends=True)
    end = next(
        index for index in range(1, len(lines)) if lines[index].rstrip("\r\n") == "---"
    )
    block = "".join(lines[1:end])
    try:
        node = yaml.compose(block, Loader=yaml.SafeLoader)
        if not isinstance(node, yaml.MappingNode):
            raise _output_error()
        values = [value for key, value in node.value if key.value == "description"]
        if len(values) != 1 or not isinstance(values[0], yaml.ScalarNode):
            raise _output_error()
        value = values[0]
        start, stop = value.start_mark.index, value.end_mark.index
        replacement = json.dumps(description, ensure_ascii=False)
        # A block scalar consumes its terminating newline. Retain that separator.
        consumed = block[start:stop]
        if consumed.endswith("\r\n"):
            replacement += "\r\n"
        elif consumed.endswith("\n"):
            replacement += "\n"
        root["content"] = (
            lines[0] + block[:start] + replacement + block[stop:] + "".join(lines[end:])
        )
        after, after_body, after_errors = parse_frontmatter(root["content"])
    except (yaml.YAMLError, ValueError, RecursionError) as exc:
        raise _output_error() from exc
    if (
        after_errors
        or after != {**frontmatter, "description": description}
        or after_body != body
    ):
        # Alias/merge-key edits must not silently change another scalar.
        raise _output_error()
    checks = run_checks(result, expected_name=name)
    if checks["summary"]["errors"]:
        raise _output_error()
    return result


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    output = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("Duplicate JSON key")
        output[key] = value
    return output


def validate_assist_output(
    *, kind: str, content: str, name: str, files: list[dict]
) -> dict:
    """Strictly validate the single completion; never issue a repair completion."""
    if (
        not isinstance(content, str)
        or not text_is_valid(content)
        or len(content.encode("utf-8")) > MAX_OUTPUT_BYTES
    ):
        raise _output_error()
    try:
        output = json.loads(
            content,
            object_pairs_hook=_unique_object,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (ValueError, RecursionError) as exc:
        raise _output_error() from exc
    if not isinstance(output, dict):
        raise _output_error()
    proposed = {"files": None, "description": None, "summary": None}
    questions = []
    checks = None
    if kind == "draft":
        if set(output) != {"files", "missing_information"}:
            raise _output_error()
        proposed_files, questions = output["files"], output["missing_information"]
        if not isinstance(proposed_files, list) or not 1 <= len(proposed_files) <= 3:
            raise _output_error()
        if (
            not isinstance(questions, list)
            or len(questions) > 20
            or any(not _bounded_text(question, 2000) for question in questions)
        ):
            raise _output_error()
        for file in proposed_files:
            if (
                not isinstance(file, dict)
                or set(file) != {"path", "content"}
                or not isinstance(file["path"], str)
                or not isinstance(file["content"], str)
                or not text_is_valid(file["path"])
                or not text_is_valid(file["content"])
            ):
                raise _output_error()
            if any(part.startswith(".") for part in file["path"].split("/")):
                raise _output_error()
        if (
            sum(len(file["content"].encode("utf-8")) for file in proposed_files)
            > MAX_OUTPUT_BYTES
        ):
            raise _output_error()
        checks = run_checks(proposed_files, expected_name=name)
        if checks["summary"]["errors"] or any(
            item["code"] in {"secret_suspected", "executable_content"}
            for item in checks["items"]
        ):
            raise _output_error()
        from model_hub.services.skills_hub.v11.inventory import build_inventory

        inventory = build_inventory(
            bundles=[
                {
                    "skill_id": name,
                    "digest": manifest_digest(proposed_files),
                    "files": proposed_files,
                }
            ],
            cases=[],
        )
        if any(
            "evaluation_artifact" in file["flags"]
            for bundle in inventory["inventory"]
            for file in bundle["files"]
        ):
            raise _output_error()
        proposed["files"] = proposed_files
    elif kind == "description":
        if set(output) != {"description", "reason", "tradeoffs"} or not _bounded_text(
            output["reason"], 2000
        ):
            raise _output_error()
        if (
            not isinstance(output["tradeoffs"], list)
            or len(output["tradeoffs"]) > 10
            or any(not _bounded_text(value, 2000) for value in output["tradeoffs"])
        ):
            raise _output_error()
        proposed["description"] = output["description"]
        proposed["files"] = replace_description(files, output["description"], name=name)
        proposed["reason"], proposed["tradeoffs"] = (
            output["reason"],
            output["tradeoffs"],
        )
        checks = run_checks(proposed["files"], expected_name=name)
        if any(
            item["code"] == "secret_suspected"
            for item in run_checks(
                [{"path": "description.txt", "content": output["description"]}]
            )["items"]
        ):
            raise _output_error()
    elif kind == "summary":
        if set(output) != {"summary"} or not _bounded_text(output["summary"], 2000):
            raise _output_error()
        proposed["summary"] = output["summary"]
    else:
        raise _output_error()
    return {"proposal": proposed, "missing_information": questions, "checks": checks}


def _request_input(request: dict, *, creation: bool = False) -> dict:
    fields = {
        "draft": {
            "kind",
            "revision",
            "expected_digest",
            "instruction",
            "context_text",
            "model",
            "max_estimated_cost_usd",
        },
        "description": {
            "kind",
            "revision",
            "expected_digest",
            "source_run_id",
            "case_ids",
            "model",
            "max_estimated_cost_usd",
        },
        "summary": {"kind", "from_version", "to", "model", "max_estimated_cost_usd"},
    }
    kind = request.get("kind")
    required = fields.get(kind)
    accepted = (
        {
            "expected_input_fingerprint",
            "acknowledge_provider_processing",
            "outbound_review",
        }
        if creation
        else set()
    )
    if required is None or set(request) != required | accepted:
        raise _invalid("Use the exact assistance input fields for the selected action.")
    model = request["model"]
    if not isinstance(model, dict) or set(model) != {
        "model_id",
        "temperature",
        "max_output_tokens",
    }:
        raise _invalid("Choose an explicit model configuration.")
    if (
        type(model["max_output_tokens"]) is not int
        or not 1 <= model["max_output_tokens"] <= MAX_OUTPUT_TOKENS
    ):
        raise _invalid("Assistance permits at most 8192 output tokens.")
    if (
        not _bounded_text(model["model_id"], 255)
        or isinstance(model["temperature"], bool)
        or not isinstance(model["temperature"], (int, float))
        or not 0 <= model["temperature"] <= 2
    ):
        raise _invalid("Choose a valid model and temperature.")
    if kind == "draft" and (
        not _bounded_text(request["instruction"], MAX_INPUT_BYTES)
        or not _bounded_text(request["context_text"], MAX_INPUT_BYTES, nonempty=False)
    ):
        raise _invalid("Provide the task and bounded plain-text context.")
    if kind in {"draft", "description"} and (
        type(request["revision"]) is not int
        or request["revision"] < 1
        or not isinstance(request["expected_digest"], str)
    ):
        raise _invalid("Choose a saved draft revision and digest.")
    if kind == "description" and (
        not isinstance(request["case_ids"], list)
        or not 2 <= len(request["case_ids"]) <= 20
        or len(set(map(str, request["case_ids"]))) != len(request["case_ids"])
    ):
        raise _invalid("Select reviewed positive and near-miss examples.")
    return {field: request[field] for field in required}


def _skill(*, scope: SkillScope, skill_id: UUID, lock: bool = False) -> HubSkill:
    queryset = HubSkill.no_workspace_objects.filter(
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        workspace__organization_id=scope.organization_id,
        deleted=False,
    )
    if lock:
        queryset = queryset.select_for_update(of=("self",))
    try:
        return queryset.get(pk=skill_id)
    except HubSkill.DoesNotExist as exc:
        raise _invalid(
            "Resource not found.", code="resource_not_found", status=404
        ) from exc


def _draft(
    *,
    skill: HubSkill,
    principal: SkillPrincipal,
    revision: int,
    digest: str,
    lock: bool = False,
) -> HubSkillDraft:
    queryset = skill.drafts.filter(user_id=principal.user_id)
    if lock:
        queryset = queryset.select_for_update()
    draft = queryset.first()
    if draft is None or draft.revision != revision:
        raise SkillsHubError(
            "draft_conflict",
            "Save and review the current draft before continuing.",
            status=409,
            detail={"revision": draft.revision if draft else 0},
        )
    if manifest_digest(draft.files) != digest:
        raise _invalid(
            "The candidate files changed.", code="candidate_changed", status=409
        )
    return draft


def _version_files(skill: HubSkill, number: int) -> list[dict]:
    version = skill.versions.filter(number=number).first()
    if version is None:
        raise _invalid("Version not found.", code="version_not_found", status=404)
    return [
        {"path": file.path, "content": file.blob.content}
        for file in version.files.select_related("blob").order_by("path")
    ]


def _resolve_inputs(
    *, principal: SkillPrincipal, skill: HubSkill, request: dict, lock: bool
) -> dict:
    kind = request["kind"]
    source_run_id, cases = None, []
    draft = None
    if kind in {"draft", "description"}:
        draft = _draft(
            skill=skill,
            principal=principal,
            revision=request["revision"],
            digest=request["expected_digest"],
            lock=lock,
        )
        files = deepcopy(draft.files)
    else:
        target = request["to"]
        if not isinstance(target, dict) or target.get("kind") not in {
            "draft",
            "version",
        }:
            raise _invalid("Choose a saved candidate for the summary.")
        field = "revision" if target["kind"] == "draft" else "version"
        if (
            set(target) != {"kind", field, "digest"}
            or type(target[field]) is not int
            or target[field] < 1
        ):
            raise _invalid("Choose a valid candidate identity.")
        if target["kind"] == "draft":
            draft = _draft(
                skill=skill,
                principal=principal,
                revision=target["revision"],
                digest=target["digest"],
                lock=lock,
            )
            files = deepcopy(draft.files)
        else:
            files = _version_files(skill, target["version"])
            if manifest_digest(files) != target["digest"]:
                raise _invalid(
                    "The candidate files changed.", code="candidate_changed", status=409
                )
    payload = {"name": skill.name, "files": files}
    if kind == "draft":
        payload.update(
            instruction=request["instruction"], context_text=request["context_text"]
        )
    elif kind == "description":
        from model_hub.selectors.skills_hub_v11 import get_visible_run

        run = get_visible_run(
            principal=principal, skill_id=skill.pk, run_id=request["source_run_id"]
        )
        if (
            run.payload_purged_at
            or run.snapshot is None
            or (run.payload_expires_at and run.payload_expires_at <= timezone.now())
        ):
            raise _invalid(
                "Detailed evidence expired.", code="evidence_expired", status=410
            )
        if run.candidate_digest != manifest_digest(files):
            raise _invalid(
                "The source run tested different files.",
                code="candidate_changed",
                status=409,
            )
        selected = set(map(str, request["case_ids"]))
        cases = [case for case in run.snapshot["cases"] if str(case["id"]) in selected]
        if (
            len(cases) != len(selected)
            or any(not case["reviewed"] for case in cases)
            or {case["expected_trigger"] for case in cases} != {True, False}
        ):
            raise _invalid(
                "Select reviewed examples for both use and non-use of this skill."
            )
        source_run_id = str(run.pk)
        # Expected answers/checks are deliberately not given to description assistance.
        payload["routing_examples"] = [
            {key: case[key] for key in ("id", "prompt", "fixtures", "expected_trigger")}
            for case in cases
        ]
    else:
        from model_hub.services.skills_hub.compare import compare_file_sets

        before_number = request["from_version"]
        if before_number is not None and (
            type(before_number) is not int or before_number < 1
        ):
            raise _invalid("Choose a valid before version.")
        before = (
            _version_files(skill, before_number) if before_number is not None else []
        )
        payload = {"name": skill.name, "diff": compare_file_sets(before, files)}
    return {
        "payload": payload,
        "files": files,
        "base_revision": draft.revision if draft else None,
        "base_digest": manifest_digest(files),
        "draft_note": draft.note if draft else "",
        "draft_base_version": draft.base_version if draft else None,
        "draft_source": deepcopy(getattr(draft, "source", {})),
        "source_run_id": source_run_id,
        "case_ids": [str(case["id"]) for case in cases],
        "source_group_ids": sorted({case["source_group_id"] for case in cases}),
    }


def _outbound_findings(
    payload: dict,
    fingerprint: str,
    *,
    skill_id: str | None = None,
    digest: str | None = None,
) -> list[dict]:
    """Scan every outbound scalar using the contract's bounded location union."""
    from model_hub.services.skills_hub.v11.inventory import (
        bind_findings,
        secret_findings,
    )

    findings = []

    def visit(value: object, pointer: str) -> None:
        if isinstance(value, dict):
            for key in sorted(value):
                visit(
                    value[key],
                    pointer + "/" + str(key).replace("~", "~0").replace("/", "~1"),
                )
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{pointer}/{index}")
        elif isinstance(value, str):
            location = {"kind": "assist_input", "pointer": pointer}
            parts = pointer.split("/")[1:]
            if (
                len(parts) == 3
                and parts[0] == "files"
                and parts[2] == "content"
                and skill_id
                and digest
            ):
                path = payload["files"][int(parts[1])]["path"]
                if secret_findings(path, location={}):
                    path = "[redacted path]"
                location = {
                    "kind": "bundle_file",
                    "skill_id": skill_id,
                    "digest": digest,
                    "path": path,
                }
            elif (
                len(parts) == 3
                and parts[0] == "routing_examples"
                and parts[2] == "prompt"
            ):
                location = {
                    "kind": "case_prompt",
                    "case_id": str(payload["routing_examples"][int(parts[1])]["id"]),
                }
            elif (
                len(parts) == 5
                and parts[0] == "routing_examples"
                and parts[2] == "fixtures"
                and parts[4] == "text"
            ):
                case = payload["routing_examples"][int(parts[1])]
                location = {
                    "kind": "fixture",
                    "case_id": str(case["id"]),
                    "fixture_id": str(case["fixtures"][int(parts[3])]["id"]),
                }
            for finding in secret_findings(value, location=location):
                # Location is a discriminated union, not nullable unrelated coordinates.
                finding["location"] = {**location, "line": finding["location"]["line"]}
                findings.append(finding)
            if len(findings) > MAX_FINDINGS:
                raise _invalid(
                    "Too many possible credentials. Remove them before previewing.",
                    code="test_input_unsafe",
                    status=422,
                )

    visit(payload, "")
    return bind_findings(findings, fingerprint)


def _validate_review(request: dict, plan: dict) -> dict:
    review = request.get("outbound_review")
    if (
        request.get("acknowledge_provider_processing") is not True
        or not isinstance(review, dict)
        or set(review) != {"fingerprint", "false_positive_secret_findings"}
    ):
        raise _invalid(
            "Review outbound data and acknowledge provider processing.",
            code="invalid_inventory_review",
        )
    if (
        request.get("expected_input_fingerprint") != plan["input_fingerprint"]
        or review["fingerprint"] != plan["input_fingerprint"]
    ):
        raise _invalid(
            "Assistance inputs changed. Preview them again.",
            code="test_plan_changed",
            status=409,
        )
    decisions = review["false_positive_secret_findings"]
    if not isinstance(decisions, list) or len(decisions) > MAX_FINDINGS:
        raise _invalid("Invalid outbound review.", code="invalid_inventory_review")
    expected = {item["finding_id"] for item in plan["outbound_findings"]}
    seen = set()
    for decision in decisions:
        if (
            not isinstance(decision, dict)
            or set(decision) != {"finding_id", "reason"}
            or not _bounded_text(decision["reason"], 2000)
            or not isinstance(decision["finding_id"], str)
            or decision["finding_id"] not in expected
            or decision["finding_id"] in seen
        ):
            raise _invalid(
                "Use each finding from this preview once with a review reason.",
                code="invalid_inventory_review",
            )
        seen.add(decision["finding_id"])
    if seen != expected:
        raise _invalid(
            "Remove possible credentials or review every false positive.",
            code="test_input_unsafe",
            status=422,
        )
    return {
        "fingerprint": review["fingerprint"],
        "false_positive_secret_findings": sorted(
            decisions, key=lambda item: item["finding_id"]
        ),
    }


def _resolve_plan(
    *, principal: SkillPrincipal, skill: HubSkill, request: dict, lock: bool = False
) -> tuple[dict, dict]:
    from model_hub.services.skills_hub.v11.limits import (
        effective_limits,
        retention_days,
    )
    from model_hub.services.skills_hub.v11.model_calls import (
        ADAPTER_VERSION,
        call_allowance,
        resolve_test_connection,
    )

    inputs = _resolve_inputs(
        principal=principal, skill=skill, request=request, lock=lock
    )
    prompt_version = f"skills-{request['kind']}-v1.1"
    messages = [
        {"role": "system", "content": PROMPTS[request["kind"]]},
        {"role": "user", "content": _json(inputs["payload"])},
    ]
    input_bytes = len(_json(messages).encode("utf-8"))
    if input_bytes > MAX_INPUT_BYTES:
        raise _invalid(
            "The complete assistance input exceeds 64 KiB. Reduce it; no prefix will be sent.",
            code="payload_too_large",
            status=413,
        )
    connection = resolve_test_connection(
        scope=principal.scope, model_id=request["model"]["model_id"]
    )
    tokens, estimate = call_allowance(
        connection=connection,
        messages=messages,
        tools=[],
        max_output_tokens=request["model"]["max_output_tokens"],
    )
    try:
        stop = Decimal(str(request["max_estimated_cost_usd"]))
    except InvalidOperation as exc:
        raise _invalid("Choose a positive estimated-cost stop.") from exc
    if (
        not stop.is_finite()
        or not 0 < stop <= Decimal(effective_limits()["max_estimated_cost_usd"])
        or stop.as_tuple().exponent < -8
    ):
        raise _invalid(
            "Choose a positive estimated-cost stop within deployment limits."
        )
    identity = {
        "organization_id": str(principal.scope.organization_id),
        "workspace_id": str(principal.scope.workspace_id),
        "owner_user_id": str(principal.user_id),
        "skill_id": str(skill.pk),
        "request": request,
        "resolved": inputs,
        "prompt_version": prompt_version,
        "prompt": PROMPTS[request["kind"]],
        "adapter_version": ADAPTER_VERSION,
        "connection": connection.identity(),
        "token_allowance": tokens,
        "estimated_allowance_usd": str(estimate),
        "retention_days": retention_days(),
    }
    fingerprint = _digest(identity)
    plan = {
        "input_fingerprint": fingerprint,
        "provider_disclosure": {"provider": connection.provider},
        "allowance": {
            "input_bytes": input_bytes,
            "max_input_bytes": MAX_INPUT_BYTES,
            "max_total_tokens": tokens,
            "max_output_bytes": MAX_OUTPUT_BYTES,
            "max_output_tokens": request["model"]["max_output_tokens"],
            "max_files": 3,
            "deadline_seconds": 60,
        },
        "estimate": {
            "upper_bound_usd": str(estimate),
            "pricing_source": connection.pricing_source,
        },
        "outbound_findings": _outbound_findings(
            inputs["payload"],
            fingerprint,
            skill_id=str(skill.pk),
            digest=inputs["base_digest"],
        ),
    }
    snapshot = {**identity, "messages": messages, "input_fingerprint": fingerprint}
    return plan, json.loads(_json(snapshot))


def preview_assist_proposal(
    *, principal: SkillPrincipal, skill_id: UUID, request: dict
) -> dict:
    """Resolve a spend/outbound preview locally. No job or provider call is created."""
    from model_hub.services.skills_hub.v11.principals import (
        require_private_principal,
        require_scope_access,
    )

    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=True)
    skill = _skill(scope=principal.scope, skill_id=skill_id)
    plan, _ = _resolve_plan(
        principal=principal, skill=skill, request=_request_input(request)
    )
    return plan


def _existing_proposal(
    *, principal: SkillPrincipal, idempotency_key: UUID, request_digest: str
) -> HubSkillAssistProposal | None:
    from model_hub.models.skills_hub_v11 import HubSkillAssistProposal, HubSkillJob

    job = HubSkillJob.objects.filter(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        owner_user_id=principal.user_id,
        kind="assist",
        idempotency_key=idempotency_key,
    ).first()
    if job is None:
        return None
    if job.request_digest != request_digest:
        raise _invalid(
            "This idempotency key was used for another request.",
            code="idempotency_conflict",
            status=409,
        )
    return HubSkillAssistProposal.objects.select_related("job").get(job=job)


def create_assist_proposal(
    *, principal: SkillPrincipal, skill_id: UUID, idempotency_key: UUID, request: dict
) -> HubSkillAssistProposal:
    """Accept the reviewed immutable input with shared organization admission."""
    from model_hub.models.skills_hub_v11 import (
        HubSkillAssistProposal,
        HubSkillExecutionQuota,
    )
    from model_hub.services.skills_hub.v11.jobs import create_job_locked, dispatch_job
    from model_hub.services.skills_hub.v11.principals import (
        require_private_principal,
        require_scope_access,
    )

    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=True)
    _skill(scope=principal.scope, skill_id=skill_id)
    request_digest = _digest({"skill_id": str(skill_id), "request": request})
    existing = _existing_proposal(
        principal=principal,
        idempotency_key=idempotency_key,
        request_digest=request_digest,
    )
    if existing is not None:
        return existing
    configuration = _request_input(request, creation=True)
    with transaction.atomic():
        HubSkillExecutionQuota.objects.get_or_create(
            organization_id=principal.scope.organization_id
        )
        HubSkillExecutionQuota.objects.select_for_update().get(
            organization_id=principal.scope.organization_id
        )
        existing = _existing_proposal(
            principal=principal,
            idempotency_key=idempotency_key,
            request_digest=request_digest,
        )
        if existing is not None:
            return existing
        skill = _skill(scope=principal.scope, skill_id=skill_id, lock=True)
        plan, snapshot = _resolve_plan(
            principal=principal, skill=skill, request=configuration, lock=True
        )
        review = _validate_review(request, plan)
        if Decimal(plan["estimate"]["upper_bound_usd"]) > Decimal(
            str(configuration["max_estimated_cost_usd"])
        ):
            raise _invalid(
                "The complete call allowance exceeds the chosen spend stop.",
                code="budget_exhausted",
                status=422,
            )
        snapshot["outbound_review"] = review
        snapshot["accepted_input_fingerprint"] = _digest(
            [plan["input_fingerprint"], review]
        )
        job = create_job_locked(
            principal=principal,
            kind="assist",
            skill_id=skill_id,
            idempotency_key=idempotency_key,
            request_digest=request_digest,
            deadline_at=timezone.now() + timedelta(seconds=60),
            limits={
                "max_total_tokens": snapshot["token_allowance"],
                "max_estimated_cost_usd": configuration["max_estimated_cost_usd"],
            },
        )
        resolved = snapshot["resolved"]
        proposal = HubSkillAssistProposal.objects.create(
            job=job,
            skill=skill,
            kind=configuration["kind"],
            base_revision=resolved["base_revision"],
            base_digest=resolved["base_digest"],
            input=snapshot,
            input_fingerprint=plan["input_fingerprint"],
            prompt_version=snapshot["prompt_version"],
            model_config=configuration["model"],
            source_run_id=resolved["source_run_id"],
            selected_case_ids=resolved["case_ids"],
            exposed_source_group_ids=resolved["source_group_ids"],
        )
        transaction.on_commit(
            partial(dispatch_job, job_id=job.pk, scope=principal.scope)
        )
    return proposal


def apply_assist_proposal(
    *, principal: SkillPrincipal, proposal_id: UUID, revision: int, expected_digest: str
) -> HubSkillDraft:
    """CAS apply only reviewed files; successful retries return the original receipt."""
    from model_hub.models.skills_hub_v11 import HubSkillAssistProposal
    from model_hub.selectors.skills_hub_v11 import get_owned_proposal
    from model_hub.services.skills_hub.v11.principals import (
        require_private_principal,
        require_scope_access,
    )

    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=True)
    proposal = get_owned_proposal(principal=principal, proposal_id=proposal_id)
    with transaction.atomic():
        skill = _skill(scope=principal.scope, skill_id=proposal.skill_id, lock=True)
        proposal = HubSkillAssistProposal.objects.select_related("job").get(
            pk=proposal.pk
        )
        if proposal.kind == "summary":
            raise _invalid(
                "Copy the suggestion into the editable version note.",
                code="assist_apply_not_supported",
            )
        if (
            proposal.payload_purged_at
            or proposal.input is None
            or (
                proposal.payload_expires_at
                and proposal.payload_expires_at <= timezone.now()
            )
        ):
            raise _invalid(
                "This proposal's detailed payload expired.",
                code="evidence_expired",
                status=410,
            )
        if proposal.job.status != "completed" or not proposal.proposal:
            raise _invalid(
                "Only a completed proposal can be applied.",
                code="assist_output_invalid",
                status=422,
            )
        resolved = proposal.input["resolved"]
        if proposal.application_receipt:
            receipt = proposal.application_receipt
            # A detached result, not the now-current draft. Never overwrite later edits.
            return HubSkillDraft(
                id=receipt["draft_id"],
                skill=skill,
                user_id=principal.user_id,
                revision=receipt["revision"],
                files=proposal.proposal["files"],
                note=resolved["draft_note"],
                base_version=resolved["draft_base_version"],
                updated_at=receipt["updated_at"],
                **(
                    {"source": resolved["draft_source"]}
                    if hasattr(HubSkillDraft, "source")
                    else {}
                ),
            )
        if revision != proposal.base_revision:
            raise _invalid(
                "The proposal is for an earlier draft revision.",
                code="draft_conflict",
                status=409,
            )
        if expected_digest != proposal.base_digest:
            raise _invalid(
                "The proposal is for different candidate files.",
                code="candidate_changed",
                status=409,
            )
        draft = _draft(
            skill=skill,
            principal=principal,
            revision=revision,
            digest=expected_digest,
            lock=True,
        )
        files = proposal.proposal["files"]
        if proposal.kind == "description":
            files = replace_description(
                draft.files, proposal.proposal["description"], name=skill.name
            )
            if files != proposal.proposal["files"]:
                raise _invalid(
                    "The reviewed description patch changed.",
                    code="candidate_changed",
                    status=409,
                )
        checks = run_checks(files, expected_name=skill.name)
        if checks["summary"]["errors"]:
            raise _output_error()
        saved = save_draft(
            skill=skill,
            user=draft.user,
            files=files,
            note=draft.note,
            base_version=draft.base_version,
            revision=revision,
        )
        proposal.application_receipt = {
            "draft_id": str(saved.pk),
            "revision": saved.revision,
            "digest": manifest_digest(saved.files),
            "updated_at": saved.updated_at.isoformat(),
            "applied_by": str(principal.user_id),
            "applied_by_api_key": str(principal.api_key_id)
            if principal.api_key_id
            else None,
        }
        proposal.save(update_fields=["application_receipt"])
        return saved


def _prepare_generation(*, scope: SkillScope, proposal_id: UUID) -> tuple | None:
    from model_hub.models.skills_hub_v11 import (
        HubSkillAssistProposal,
        HubSkillJob,
        HubSkillModelCall,
    )
    from model_hub.services.skills_hub.v11.jobs import (
        check_job_running,
        lock_quota,
        reserve_call,
    )
    from model_hub.services.skills_hub.v11.model_calls import (
        call_allowance,
        resolve_test_connection,
    )
    from model_hub.services.skills_hub.v11.types import TERMINAL_JOB_STATUSES

    with transaction.atomic():
        lock_quota(scope.organization_id)
        job = (
            HubSkillJob.objects.select_for_update()
            .filter(
                pk=proposal_id,
                organization_id=scope.organization_id,
                workspace_id=scope.workspace_id,
                kind="assist",
            )
            .first()
        )
        if job is None:
            raise _invalid("Resource not found.", code="resource_not_found", status=404)
        if (
            job.status in TERMINAL_JOB_STATUSES
            or HubSkillModelCall.objects.filter(job=job).exists()
        ):
            return None
        check_job_running(job)
        proposal = HubSkillAssistProposal.objects.get(job=job, skill_id=job.skill_id)
        proposal.job = job
        if proposal.input is None or proposal.payload_purged_at:
            raise _invalid(
                "This proposal expired.", code="evidence_expired", status=410
            )
        if job.started_at is None:
            job.started_at, job.claim_generation = (
                timezone.now(),
                job.claim_generation + 1,
            )
        job.status, job.heartbeat_at = "running", timezone.now()
        job.save(
            update_fields=["started_at", "claim_generation", "status", "heartbeat_at"]
        )
        connection = resolve_test_connection(
            scope=scope, model_id=proposal.model_config["model_id"]
        )
        if connection.identity() != proposal.input["connection"]:
            raise _invalid(
                "Model configuration changed after the preview.",
                code="test_plan_changed",
                status=409,
            )
        tokens, estimate = call_allowance(
            connection=connection,
            messages=proposal.input["messages"],
            tools=[],
            max_output_tokens=proposal.model_config["max_output_tokens"],
        )
        call = reserve_call(
            job_id=job.pk,
            attempt_id=None,
            kind="assist",
            request_digest=proposal.input["accepted_input_fingerprint"],
            token_allowance=tokens,
            estimated_allowance=estimate,
        )
        return proposal, call, connection


def _store_generated(
    *, scope: SkillScope, proposal_id: UUID, generation: int, output: dict
) -> bool:
    from model_hub.models.skills_hub_v11 import HubSkillAssistProposal, HubSkillJob

    with transaction.atomic():
        job = HubSkillJob.objects.select_for_update().get(
            pk=proposal_id,
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
        )
        if (
            job.claim_generation != generation
            or job.status != "running"
            or job.cancel_requested_at
            or timezone.now() >= job.deadline_at
        ):
            return False
        proposal = HubSkillAssistProposal.objects.get(job=job)
        proposal.proposal = output["proposal"]
        proposal.missing_information = output["missing_information"]
        proposal.checks = output["checks"]
        # Freeze retention at acceptance, even if deployment settings change mid-job.
        proposal.payload_expires_at = timezone.now() + timedelta(
            days=proposal.input["retention_days"]
        )
        proposal.save(
            update_fields=[
                "proposal",
                "missing_information",
                "checks",
                "payload_expires_at",
            ]
        )
        return True


async def generate_assist_proposal(*, scope: SkillScope, proposal_id: UUID) -> dict:
    """One bounded request through the shared adapter and durable reservation ledger."""
    from model_hub.services.skills_hub.v11.jobs import (
        finish_job,
        mark_in_flight,
        settle_call,
    )
    from model_hub.services.skills_hub.v11.model_calls import complete_skill_turn

    prepared = await sync_to_async(_prepare_generation)(
        scope=scope, proposal_id=proposal_id
    )
    if prepared is None:
        return {"id": str(proposal_id), "already_claimed": True}
    proposal, call, connection = prepared
    result, error = None, None
    try:
        await sync_to_async(mark_in_flight)(
            call_id=call.pk,
            generation=call.claim_generation,
            connection=connection.identity(),
        )
        remaining = int(
            (proposal.job.deadline_at - timezone.now()).total_seconds() * 1000
        )
        if remaining <= 0:
            raise _invalid(
                "Assistance deadline reached.", code="provider_timeout", status=422
            )
        result = await complete_skill_turn(
            connection=connection,
            messages=proposal.input["messages"],
            tools=[],
            parameters={
                "temperature": proposal.model_config["temperature"],
                "max_output_tokens": proposal.model_config["max_output_tokens"],
            },
            timeout_ms=min(45000, remaining),
        )
        if result.finish_reason != "stop" or result.assistant_message.get("tool_calls"):
            raise _output_error()
        output = validate_assist_output(
            kind=proposal.kind,
            content=result.assistant_message.get("content"),
            name=proposal.input["resolved"]["payload"]["name"],
            files=proposal.input["resolved"]["files"],
        )
        await sync_to_async(_store_generated)(
            scope=scope,
            proposal_id=proposal_id,
            generation=call.claim_generation,
            output=output,
        )
    except SkillsHubError as exc:
        # Domain messages are sanitized; raw provider exceptions never enter evidence.
        error = {
            "code": exc.code,
            "message": "Assistance could not complete. Review the inputs and connection before retrying.",
            "detail": {},
        }
    except asyncio.CancelledError:
        await asyncio.shield(
            sync_to_async(settle_call)(
                call_id=call.pk,
                generation=call.claim_generation,
                result=result,
                error={
                    "code": "execution_unknown",
                    "message": "Local execution stopped; provider outcome may be unknown.",
                    "detail": {},
                },
            )
        )
        raise
    await sync_to_async(settle_call)(
        call_id=call.pk, generation=call.claim_generation, result=result, error=error
    )
    return await sync_to_async(finish_job)(
        scope=scope, job_id=proposal_id, failure_code=error["code"] if error else None
    )
