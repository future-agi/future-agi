"""Context snapshot and text protocol on the shared Skills paid-job lifecycle."""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from functools import partial
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from model_hub.models import (
    HubSkillTestAttempt,
    HubSkillTestRun,
    HubSkillTestSuite,
)
from model_hub.selectors.skills_hub_v12 import get_resource
from model_hub.services.skills_hub.checks import parse_frontmatter
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.packages import file_sha256, manifest_digest
from model_hub.services.skills_hub.snapshots import snapshot_version
from model_hub.services.skills_hub.v11 import runner, test_runs
from model_hub.services.skills_hub.v11.grading import EVALUATOR_VERSION, parse_json
from model_hub.services.skills_hub.v11.inventory import (
    bind_findings,
    build_inventory,
    canonical_json,
    fingerprint,
    validate_inventory_review,
)
from model_hub.services.skills_hub.v11.jobs import (
    create_job_locked,
    dispatch_job,
    existing_job,
)
from model_hub.services.skills_hub.v11.limits import (
    MAX_SNAPSHOT_BYTES,
    effective_limits,
    retention_days,
    validate_run_limits,
)
from model_hub.services.skills_hub.v11.model_calls import (
    ADAPTER_VERSION,
    call_allowance,
    resolve_test_connection,
)
from model_hub.services.skills_hub.v11.principals import (
    require_private_principal,
    require_scope_access,
)
from model_hub.services.skills_hub.v11.suites import validate_cases
from model_hub.services.skills_hub.v11.types import SkillPrincipal, SkillScope
from model_hub.services.skills_hub.v12.resolution_leases import (
    check_snapshot_distribution,
    require_distribution_eligibility,
)

HARNESS_VERSION = "contexts-text-v1.2"
CASE_FIELDS = {
    "id",
    "title",
    "source_group_id",
    "prompt",
    "fixtures",
    "expected_trigger",
    "checks",
    "reviewed",
}
CONTEXT_PROMPT = """Complete the user's request using the supplied evidence.
Standing application instructions follow in a separate message. They and all
skill files/fixtures remain below these invariant restrictions: only text and
read-only file tools are available. Do not execute code, browse, contact services
or invent external actions. Consult relevant optional skills using load_skill;
continue root chunks before treating them as complete. Read supporting files
only when needed. Available skills:\n"""


def validate_context_cases(
    cases: list[dict], *, require_reviewed: bool = True
) -> list[dict]:
    result = validate_cases(cases, require_reviewed=require_reviewed)
    if any(case["expected_trigger"] is not None for case in result):
        raise SkillsHubError(
            "invalid_test_config",
            "Standing context has no root activation expectation.",
            status=400,
        )
    return result


def context_bundle(snapshot: dict, *, draft_revision: int | None = None) -> dict:
    """Adapt the common immutable snapshot to the existing run-owned file shape."""
    files = [
        {
            **item,
            "sha256": file_sha256(item["content"]),
            "size_bytes": len(item["content"].encode()),
        }
        for item in snapshot["files"]
    ]
    by_path = {item["path"]: item for item in files}
    if (
        len(by_path) != len(files)
        or not by_path.get("AGENTS.md", {}).get("content", "").strip()
        or snapshot.get("digest") != manifest_digest(files)
    ):
        raise SkillsHubError(
            "invalid_context_definition",
            "Context snapshot must retain its exact nonempty root and full manifest.",
            status=422,
        )
    try:
        descriptor = json.loads(by_path[".futureagi/context.json"]["content"])
    except (ValueError, KeyError, TypeError) as exc:
        raise SkillsHubError(
            "invalid_context_definition",
            "Context descriptor is unavailable.",
            status=422,
        ) from exc
    if (
        not isinstance(descriptor, dict)
        or descriptor.get("kind") != "agent_context"
        or descriptor.get("entrypoint") != "AGENTS.md"
        or not isinstance(descriptor.get("links"), list)
    ):
        raise SkillsHubError(
            "invalid_context_definition", "Context descriptor is invalid.", status=422
        )
    return {
        **snapshot,
        "subject_kind": "agent_context",
        "harness": HARNESS_VERSION,
        "draft_revision": draft_revision,
        "execution_manifest_digest": snapshot["digest"],
        "files": files,
        "descriptor": descriptor,
    }


def recheck_context_snapshot_access(*, scope: SkillScope, snapshot: dict) -> None:
    """Shared worker/proxy hook before every managed provider and file boundary.

    Job principal/deadline/generation checks still run in the existing lifecycle.
    Frozen versions are reauthorized, never re-resolved from moving labels.
    """
    check_snapshot_distribution(scope=scope, snapshot=snapshot)
    # A draft has no parent-version projection yet, but its accepted children
    # are still immutable releases subject to the same foundation boundary.
    draft_children = [
        link
        for arm in ("candidate", "production", "reference")
        if (bundle := snapshot.get(arm)) is not None and bundle["version_id"] is None
        for link in bundle["descriptor"]["links"]
    ]
    check_snapshot_distribution(scope=scope, snapshot={"competitors": draft_children})


def context_arm(snapshot: dict, variant: str) -> dict | None:
    if variant == "without_target":
        return None
    if (
        variant not in {"candidate", "production", "reference"}
        or snapshot.get(variant) is None
    ):
        raise SkillsHubError(
            "invalid_test_config", "The context baseline was not frozen.", status=400
        )
    bundle: dict = snapshot[variant]
    return bundle


def context_catalog(*, snapshot: dict, variant: str) -> dict[str, dict]:
    parent = context_arm(snapshot, variant)
    if parent is None:
        return {}
    catalog = {}
    for link in parent["descriptor"]["links"]:
        prefix = link["mount"].rstrip("/") + "/"
        files = [
            {
                "path": item["path"][len(prefix) :],
                "content": item["content"],
                "sha256": file_sha256(item["content"]),
                "size_bytes": len(item["content"].encode()),
            }
            for item in parent["files"]
            if item["path"].startswith(prefix)
        ]
        root = next(
            (item["content"] for item in files if item["path"] == "SKILL.md"), None
        )
        metadata, _, errors = parse_frontmatter(root or "")
        if (
            metadata is None
            or any(error["severity"] == "error" for error in errors)
            or manifest_digest(files) != link["digest"]
        ):
            raise SkillsHubError(
                "invalid_context_definition",
                "Retained child files do not match the accepted lock.",
                status=422,
            )
        handle = (
            "s-" + fingerprint([snapshot["plan_fingerprint"], link["skill_id"]])[7:23]
        )
        if handle in catalog:
            raise SkillsHubError(
                "invalid_context_definition",
                "Duplicate retained child identity.",
                status=422,
            )
        catalog[handle] = {
            "skill_id": link["skill_id"],
            "version_id": link["version_id"],
            "digest": link["digest"],
            "name": metadata["name"],
            "description": metadata["description"],
            "files": files,
            "parent_mount": prefix,
            "parent_skill_id": parent["skill_id"],
            "parent_version_id": parent["version_id"],
            "parent_digest": parent["digest"],
        }
    return catalog


def build_context_task_messages(
    *, snapshot: dict, case: dict, variant: str
) -> tuple[list[dict], dict]:
    catalog = context_catalog(snapshot=snapshot, variant=variant)
    messages = [
        {
            "role": "system",
            "content": CONTEXT_PROMPT
            + canonical_json(
                [
                    {
                        "id": handle,
                        "name": bundle["name"],
                        "description": bundle["description"],
                    }
                    for handle, bundle in catalog.items()
                ]
            ),
        }
    ]
    parent = context_arm(snapshot, variant)
    if parent is not None:
        root = next(
            item["content"] for item in parent["files"] if item["path"] == "AGENTS.md"
        )
        messages.append(
            {
                "role": "system",
                "content": "Standing application instructions (AGENTS.md)\n" + root,
            }
        )
    messages.append(
        {
            "role": "user",
            "content": "User request and supplied input\n"
            + canonical_json(
                {
                    "request": case["prompt"],
                    "fixtures": [
                        {"name": item["name"], "text": item["text"]}
                        for item in case["fixtures"]
                    ],
                }
            ),
        }
    )
    return messages, catalog


def context_tool_definitions(*, with_context: bool) -> list[dict]:
    if not with_context:
        return []
    return [
        *runner.tool_definitions(),
        {
            "type": "function",
            "function": {
                "name": "read_context_file",
                "description": "Read an owned or generated file from the pinned context.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "offset": {"type": "integer", "minimum": 0},
                        "max_bytes": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 49152,
                        },
                    },
                    "required": ["path", "offset", "max_bytes"],
                    "additionalProperties": False,
                },
            },
        },
    ]


def root_delivery_receipt(*, snapshot: dict, variant: str, call_id: str) -> dict | None:
    """Call only after the shared transport has marked the model request in flight."""
    parent = context_arm(snapshot, variant)
    if parent is None:
        return None
    root = next(item for item in parent["files"] if item["path"] == "AGENTS.md")
    return {
        "call_id": call_id,
        "tool_call_id": "",
        "source_version_id": None,
        "skill_id": parent["skill_id"],
        "version_id": parent["version_id"],
        "digest": parent["digest"],
        "path": "AGENTS.md",
        "sha256": root["sha256"],
        "offset": 0,
        "next_offset": None,
        "total_chars": len(root["content"]),
        "eof": True,
        "delivered_bytes": root["size_bytes"],
        "outcome": "delivered",
        "error_code": None,
    }


def process_context_tool(
    *,
    snapshot: dict,
    variant: str,
    call: dict,
    call_id: str,
    activated: set[str],
    remaining_bytes: int,
) -> tuple[dict, dict, bool]:
    parent = context_arm(snapshot, variant)
    if parent is None:
        raise SkillsHubError(
            "tool_not_supported",
            "The no-context arm has no context file access.",
            status=400,
        )
    if call["function"]["name"] != "read_context_file":
        bundles = context_catalog(snapshot=snapshot, variant=variant)
        output, receipt, _ = runner.process_tool(
            call=call,
            call_id=call_id,
            bundles=bundles,
            activated=activated,
            target_id=parent["skill_id"],
            remaining_bytes=remaining_bytes,
        )
        bundle = next(
            (
                item
                for item in bundles.values()
                if item["skill_id"] == receipt["skill_id"]
            ),
            None,
        )
        receipt = {**receipt, "source_version_id": None}
        if bundle is not None:
            receipt = {
                **receipt,
                "source_version_id": receipt["version_id"],
                "skill_id": parent["skill_id"],
                "version_id": parent["version_id"],
                "digest": parent["digest"],
                "path": bundle["parent_mount"] + receipt["path"],
            }
        return output, receipt, False
    try:
        args = parse_json(call["function"]["arguments"])
        if not isinstance(args, dict) or set(args) != {"path", "offset", "max_bytes"}:
            raise SkillsHubError(
                "tool_arguments_invalid",
                "Use the exact context-file argument schema.",
                status=400,
            )
        allowed = set(parent["descriptor"]["owned_paths"]) | {".futureagi/context.json"}
        if not isinstance(args["path"], str) or args["path"] not in allowed:
            raise SkillsHubError(
                "resource_not_found",
                "Owned context file not found; load linked skills through their catalog.",
                status=404,
            )
        chunk = runner.read_snapshot_chunk(
            snapshot={"context": parent}, skill_handle="context", **args
        )
        size = len(chunk["text"].encode())
        if size > remaining_bytes:
            raise SkillsHubError(
                "budget_exhausted", "Delivery byte allowance reached.", status=422
            )
        receipt = {
            key: value
            for key, value in chunk.items()
            if key not in {"text", "size_bytes"}
        }
        return (
            chunk,
            {
                **receipt,
                "call_id": call_id,
                "tool_call_id": call["id"],
                "source_version_id": None,
                "delivered_bytes": size,
                "outcome": "delivered",
                "error_code": None,
            },
            False,
        )
    except (ValueError, TypeError, KeyError, RecursionError):
        error = SkillsHubError(
            "tool_arguments_invalid",
            "Use the tool's exact JSON argument schema.",
            status=400,
        )
    except SkillsHubError as caught:
        error = caught
    return (
        {"error": {"code": error.code, "message": error.message}},
        {
            "call_id": call_id,
            "tool_call_id": call["id"],
            "source_version_id": None,
            "skill_id": parent["skill_id"],
            "version_id": parent["version_id"],
            "digest": parent["digest"],
            "path": "",
            "sha256": None,
            "offset": 0,
            "next_offset": None,
            "total_chars": None,
            "eof": None,
            "delivered_bytes": 0,
            "outcome": "rejected",
            "error_code": error.code,
        },
        False,
    )


def _configuration(value: dict) -> dict:
    if (
        not isinstance(value, dict)
        or value.get("harness") != HARNESS_VERSION
        or value.get("competitors") != []
    ):
        raise SkillsHubError(
            "invalid_test_config",
            "Context tests require their explicit harness and an empty competitor list.",
            status=400,
        )
    baselines, variants = value.get("baselines"), []
    if not isinstance(baselines, list):
        raise SkillsHubError(
            "invalid_test_config", "Choose explicit context baselines.", status=400
        )
    for baseline in baselines:
        if baseline == {"kind": "production"}:
            variants.append("production")
        elif baseline == {"kind": "without_context"}:
            variants.append("without_target")
        elif (
            isinstance(baseline, dict)
            and set(baseline) == {"kind", "version"}
            and baseline["kind"] == "version"
            and type(baseline["version"]) is int
            and baseline["version"] >= 1
        ):
            variants.append("reference")
        else:
            raise SkillsHubError(
                "invalid_test_config", "Invalid immutable context baseline.", status=400
            )
    if not 1 <= len(variants) <= 2 or len(set(variants)) != len(variants):
        raise SkillsHubError(
            "invalid_test_config",
            "Choose one or two distinct baseline kinds.",
            status=400,
        )
    adapted = {key: item for key, item in value.items() if key != "harness"}
    adapted["baselines"] = [
        "production" if item == "reference" else item for item in variants
    ]
    normalized = test_runs._configuration(adapted)
    return {
        **normalized,
        "baselines": variants,
        "context_baselines": baselines,
        "harness": HARNESS_VERSION,
    }


def prepare_context_test_plan(
    *,
    principal: SkillPrincipal,
    context_id: UUID,
    configuration: dict,
    lock: bool = False,
) -> tuple[dict, dict]:
    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=True)
    if not settings.SKILLS_TESTING_ENABLED:
        raise SkillsHubError(
            "skills_unavailable", "Skills testing is disabled.", status=503
        )
    config = _configuration(configuration)
    # The generic limit validator knows two baseline enums; only cardinality
    # changes here, never the old leaf semantics or its five-competitor limit.
    limits_config = {
        **config,
        "baselines": ["production", "without_target"][: len(config["baselines"])],
    }
    validate_run_limits(limits_config, skill_id=context_id)
    skill = get_resource(
        principal=principal, resource_id=context_id, kind="agent_context", lock=lock
    )
    ref = config["candidate"]

    def version_bundle(number: int) -> dict:
        version = skill.versions.filter(number=number).first()
        if version is None:
            raise SkillsHubError(
                "version_not_found", "Context version not found.", status=404
            )
        require_distribution_eligibility(version)
        return context_bundle(snapshot_version(version))

    if ref["kind"] == "draft":
        from model_hub.services.skills_hub.v12.authoring import get_compiled_draft

        compiled = get_compiled_draft(
            principal=principal, context_id=context_id, revision=ref["revision"]
        )
        candidate = context_bundle(
            {
                "skill_id": str(skill.id),
                "version_id": None,
                "version": None,
                "digest": compiled.digest,
                "name": skill.name,
                "description": compiled.spec["description"],
                "entrypoint": "AGENTS.md",
                "files": compiled.files,
            },
            draft_revision=ref["revision"],
        )
    else:
        candidate = version_bundle(ref["version"])
    if candidate["digest"] != ref["digest"]:
        raise SkillsHubError(
            "test_plan_changed", "Context candidate changed.", status=409
        )
    arms, event = {"candidate": candidate, "production": None, "reference": None}, None
    for baseline, variant in zip(
        config["context_baselines"], config["baselines"], strict=True
    ):
        if variant == "production":
            label = (
                skill.labels.select_related("version").filter(name="production").first()
            )
            if label is None:
                raise SkillsHubError(
                    "label_not_found",
                    "Production is not assigned; choose a baseline explicitly.",
                    status=404,
                )
            arms[variant] = version_bundle(label.version.number)
            event = (
                skill.label_events.filter(label="production")
                .order_by("-created_at", "-id")
                .first()
            )
        elif variant == "reference":
            arms[variant] = version_bundle(baseline["version"])
    if (
        arms["production"]
        and arms["reference"]
        and arms["production"]["version_id"] == arms["reference"]["version_id"]
    ):
        raise SkillsHubError(
            "invalid_test_config", "Baseline identities must be distinct.", status=400
        )
    suite_rows = HubSkillTestSuite.objects.filter(skill=skill)
    suite = (suite_rows.select_for_update() if lock else suite_rows).first()
    selection = config["cases"]
    if selection["kind"] == "saved":
        if suite is None or suite.revision != selection["suite_revision"]:
            raise SkillsHubError(
                "test_plan_changed", "Saved examples changed.", status=409
            )
        selected = {row["id"]: row for row in suite.cases}
        if not set(selection["case_ids"]) <= set(selected):
            raise SkillsHubError(
                "invalid_test_config", "Selected case is not in the suite.", status=400
            )
        cases = [selected[key] for key in selection["case_ids"]]
        validate_context_cases(
            [{key: row[key] for key in CASE_FIELDS} for row in cases]
        )
    else:
        cases = validate_context_cases(selection["cases"])
    if (
        not cases
        or any(
            check["kind"] == "semantic" for case in cases for check in case["checks"]
        )
        and config["judge"] is None
    ):
        raise SkillsHubError(
            "invalid_test_config",
            "Choose cases and an explicit judge for semantic assertions.",
            status=400,
        )
    if config["parent_run_id"] is not None:
        parent = HubSkillTestRun.objects.filter(
            job_id=config["parent_run_id"],
            job__skill=skill,
            job__organization_id=principal.scope.organization_id,
            job__workspace_id=principal.scope.workspace_id,
        ).first()
        if parent is None or (
            parent.visibility != "workspace"
            and parent.job.owner_user_id != principal.user_id
        ):
            raise SkillsHubError(
                "resource_not_found", "Parent run not found.", status=404
            )
    connections = {
        "task": resolve_test_connection(
            scope=principal.scope, model_id=config["model"]["model_id"]
        ),
        "judge": (
            resolve_test_connection(
                scope=principal.scope, model_id=config["judge"]["model_id"]
            )
            if config["judge"]
            else None
        ),
    }
    limits = effective_limits()
    inventory = build_inventory(
        bundles=[bundle for bundle in arms.values() if bundle], cases=cases
    )
    snapshot = {
        **arms,
        "subject_kind": "agent_context",
        "harness": HARNESS_VERSION,
        "configuration": config,
        "competitors": [],
        "cases": cases,
        "suite_id": str(suite.id) if suite and selection["kind"] == "saved" else None,
        "suite_revision": (
            suite.revision if suite and selection["kind"] == "saved" else None
        ),
        "full_suite": bool(
            suite
            and selection["kind"] == "saved"
            and set(selection["case_ids"]) == {row["id"] for row in suite.cases}
        ),
        "suite_case_count": len(suite.cases) if suite else None,
        "production_event_id": str(event.id) if event else None,
        "release_revision": skill.release_revision,
        "limits": limits,
        "retention_days": retention_days(),
        "connections": {
            name: item.identity() if item else None
            for name, item in connections.items()
        },
        "execution_configuration": {
            "model": config["model"],
            "judge": config["judge"],
            "harness_version": HARNESS_VERSION,
            "adapter_version": ADAPTER_VERSION,
            "evaluator_version": EVALUATOR_VERSION,
        },
        **inventory,
    }
    plan = fingerprint(
        {
            "scope": [
                principal.scope.organization_id,
                principal.scope.workspace_id,
                principal.user_id,
            ],
            "snapshot": snapshot,
        }
    )
    snapshot["plan_fingerprint"] = plan
    snapshot["outbound_findings"] = bind_findings(inventory["outbound_findings"], plan)
    snapshot["schedule"] = sorted(
        [
            {"case_id": case["id"], "variant": variant, "repeat_index": repeat}
            for case in cases
            for variant in ["candidate", *config["baselines"]]
            for repeat in range(config["repetitions"])
        ],
        key=lambda cell: fingerprint([plan, cell]),
    )
    task = connections["task"]
    assert task is not None
    for case in cases:
        for variant in ["candidate", *config["baselines"]]:
            messages, _ = build_context_task_messages(
                snapshot=snapshot, case=case, variant=variant
            )
            call_allowance(
                connection=task,
                messages=messages,
                tools=context_tool_definitions(
                    with_context=variant != "without_target"
                ),
                max_output_tokens=config["model"]["max_output_tokens"],
            )
    if len(canonical_json(snapshot).encode()) > MAX_SNAPSHOT_BYTES:
        raise SkillsHubError(
            "payload_too_large", "Complete context snapshot exceeds 16 MiB.", status=413
        )
    attempts = len(snapshot["schedule"])
    profile_limits = (
        limits["profiles"]["quick"]
        if config["profile"] == "quick"
        else limits["profiles"]["repeated"]
    )
    max_task = attempts * profile_limits["max_task_turns_per_attempt"]
    max_judge = (
        sum(
            any(check["kind"] == "semantic" for check in case["checks"])
            for case in cases
        )
        * (1 + len(config["baselines"]))
        * config["repetitions"]
    )
    upper = max_task * (
        task.context_limit * task.input_price
        + config["model"]["max_output_tokens"] * task.output_price
    )
    judge = connections["judge"]
    if judge:
        upper += max_judge * (
            judge.context_limit * judge.input_price
            + config["judge"]["max_output_tokens"] * judge.output_price
        )
    preview = {
        "fingerprint": plan,
        "candidate": test_runs.snapshot_identity(candidate),
        "production": (
            test_runs.snapshot_identity(arms["production"])
            if arms["production"]
            else None
        ),
        "reference": (
            test_runs.snapshot_identity(arms["reference"])
            if arms["reference"]
            else None
        ),
        "production_event_id": snapshot["production_event_id"],
        "release_revision": skill.release_revision,
        "competitors": [],
        "case_count": len(cases),
        "variant_count": 1 + len(config["baselines"]),
        "repetitions": config["repetitions"],
        "attempt_count": attempts,
        "max_task_calls": max_task,
        "max_judge_calls": max_judge,
        "estimate": {
            "upper_bound_usd": format(
                min(upper, Decimal(config["limits"]["max_estimated_cost_usd"])), ".8f"
            ),
            "pricing_source": task.pricing_source,
        },
        "limits": limits,
        "inventory": inventory["inventory"],
        "outbound_findings": snapshot["outbound_findings"],
        "warnings": [],
        "provider_disclosure": {
            "task_provider": task.provider,
            "judge_provider": judge.provider if judge else None,
        },
    }
    return preview, snapshot


def preview_context_test(
    *, principal: SkillPrincipal, context_id: UUID, configuration: dict
) -> dict:
    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=True)
    return prepare_context_test_plan(
        principal=principal, context_id=context_id, configuration=configuration
    )[0]


def create_context_test(
    *, principal: SkillPrincipal, context_id: UUID, request: dict, idempotency_key: UUID
) -> HubSkillTestRun:
    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=True)
    get_resource(principal=principal, resource_id=context_id, kind="agent_context")
    request_digest = fingerprint({"skill_id": context_id, "request": request})
    existing = existing_job(
        principal=principal,
        kind="test",
        idempotency_key=idempotency_key,
        request_digest=request_digest,
    )
    if existing:
        return HubSkillTestRun.objects.get(job=existing)
    # This handshake is supplied only by the shared runner once its protocol
    # dispatch is integrated. A setting alone cannot enable the old leaf loop.
    if HARNESS_VERSION not in getattr(runner, "SUPPORTED_HARNESSES", ()):
        raise SkillsHubError(
            "runner_capability_missing",
            "The shared worker has not qualified whole-context execution.",
            status=422,
        )
    if (
        set(request)
        != {
            "configuration",
            "expected_fingerprint",
            "acknowledge_provider_processing",
            "inventory_review",
        }
        or request["acknowledge_provider_processing"] is not True
    ):
        raise SkillsHubError(
            "invalid_test_config",
            "Confirm the exact reviewed plan and provider processing.",
            status=400,
        )
    with transaction.atomic():
        from model_hub.services.skills_hub.r3.spend_policy import lock_spend_authority

        lock_spend_authority(scope=principal.scope)
        existing = existing_job(
            principal=principal,
            kind="test",
            idempotency_key=idempotency_key,
            request_digest=request_digest,
        )
        if existing:
            return HubSkillTestRun.objects.get(job=existing)
        preview, snapshot = prepare_context_test_plan(
            principal=principal,
            context_id=context_id,
            configuration=request["configuration"],
            lock=True,
        )
        if preview["fingerprint"] != request["expected_fingerprint"]:
            raise SkillsHubError(
                "test_plan_changed", "Context plan changed; preview again.", status=409
            )
        config = snapshot["configuration"]
        review, reasons = validate_inventory_review(
            plan_fingerprint=preview["fingerprint"],
            inventory=preview["inventory"],
            findings=preview["outbound_findings"],
            review=request["inventory_review"],
            purpose=config["purpose"],
            exposed=any(
                case.get("exposed_to_assist", False) for case in snapshot["cases"]
            ),
        )
        if config["parent_run_id"] and not snapshot["full_suite"]:
            reasons = sorted({*reasons, "subset_rerun"})
        snapshot["inventory_review"] = review
        execution = fingerprint(
            {"plan_fingerprint": preview["fingerprint"], "inventory_review": review}
        )
        snapshot["execution_fingerprint"] = execution
        if len(canonical_json(snapshot).encode()) > MAX_SNAPSHOT_BYTES:
            raise SkillsHubError(
                "payload_too_large",
                "Reviewed context snapshot exceeds 16 MiB.",
                status=413,
            )
        job = create_job_locked(
            principal=principal,
            kind="test",
            skill_id=context_id,
            idempotency_key=idempotency_key,
            request_digest=request_digest,
            deadline_at=timezone.now()
            + timedelta(
                seconds=snapshot["limits"]["profiles"][config["profile"]][
                    "deadline_seconds"
                ]
            ),
            limits=config["limits"],
        )
        run = HubSkillTestRun.objects.create(
            job=job,
            profile=config["profile"],
            parent_run_id=config["parent_run_id"],
            suite_id=snapshot["suite_id"],
            suite_revision=snapshot["suite_revision"],
            selected_case_ids=[case["id"] for case in snapshot["cases"]],
            full_suite=snapshot["full_suite"],
            repetitions=config["repetitions"],
            harness_version=HARNESS_VERSION,
            adapter_version=ADAPTER_VERSION,
            evaluator_version=EVALUATOR_VERSION,
            execution_configuration=snapshot["execution_configuration"],
            plan_fingerprint=preview["fingerprint"],
            execution_fingerprint=execution,
            candidate_digest=snapshot["candidate"]["digest"],
            candidate=preview["candidate"],
            production=preview["production"],
            reference=preview["reference"],
            competitors=[],
            baseline_version_id=(
                snapshot["production"]["version_id"] if snapshot["production"] else None
            ),
            baseline_event_id=snapshot["production_event_id"],
            snapshot=snapshot,
            validity="diagnostic" if reasons else "reviewed",
            validity_reasons=reasons,
        )
        HubSkillTestAttempt.objects.bulk_create(
            [
                HubSkillTestAttempt(run=run, schedule_index=index, **cell)
                for index, cell in enumerate(snapshot["schedule"])
            ]
        )
        transaction.on_commit(
            partial(dispatch_job, job_id=job.id, scope=principal.scope)
        )
        return run
