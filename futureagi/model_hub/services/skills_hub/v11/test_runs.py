"""Local preview and immutable, idempotent run acceptance."""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from functools import partial
from typing import NoReturn
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from model_hub.models.skills_hub import HubSkill, HubSkillDraft
from model_hub.models.skills_hub_v11 import (
    HubSkillAssistProposal,
    HubSkillTestAttempt,
    HubSkillTestRun,
    HubSkillTestSuite,
)
from model_hub.services.skills_hub.checks import parse_frontmatter, run_checks
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.packages import file_sha256, manifest_digest
from model_hub.services.skills_hub.types import FileInput
from model_hub.services.skills_hub.v11.grading import EVALUATOR_VERSION, validate_schema
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
    HARNESS_VERSION,
    MAX_SNAPSHOT_BYTES,
    effective_limits,
    retention_days,
    validate_run_limits,
)
from model_hub.services.skills_hub.v11.model_calls import (
    ADAPTER_VERSION,
    resolve_test_connection,
)
from model_hub.services.skills_hub.v11.principals import (
    require_private_principal,
    require_scope_access,
)
from model_hub.services.skills_hub.v11.suites import validate_cases
from model_hub.services.skills_hub.v11.types import SkillPrincipal

_CASE_FIELDS = {
    "id",
    "title",
    "source_group_id",
    "prompt",
    "fixtures",
    "expected_trigger",
    "checks",
    "reviewed",
}


def _invalid(message: str) -> NoReturn:
    raise SkillsHubError("invalid_test_config", message, status=400)


def _configuration(value: object) -> dict:
    if not isinstance(value, dict):
        _invalid("Test configuration must be an object.")
    try:
        config: dict = json.loads(canonical_json(value))
    except (ValueError, TypeError, RecursionError):
        _invalid("Test configuration must contain finite JSON values.")
    fields = {
        "candidate",
        "cases",
        "baselines",
        "competitors",
        "model",
        "judge",
        "profile",
        "repetitions",
        "limits",
        "purpose",
        "parent_run_id",
    }
    if (
        set(config) != fields
        or config["profile"] not in {"quick", "repeated"}
        or config["purpose"] not in {"regression", "diagnostic"}
        or type(config["repetitions"]) is not int
    ):
        _invalid("Use the exact test configuration fields and a supported profile.")
    if not isinstance(config["baselines"], list) or any(
        value not in {"production", "without_target"} for value in config["baselines"]
    ):
        _invalid("Choose production or without-target baselines.")
    for key in ("model", "judge"):
        model = config[key]
        if key == "judge" and model is None:
            continue
        if (
            not isinstance(model, dict)
            or set(model) != {"model_id", "temperature", "max_output_tokens"}
            or not isinstance(model["model_id"], str)
            or type(model["max_output_tokens"]) is not int
            or model["max_output_tokens"] <= 0
            or type(model["temperature"]) not in {int, float}
            or not 0 <= model["temperature"] <= 2
        ):
            _invalid("Choose explicit valid model parameters.")
    if (
        not isinstance(config["limits"], dict)
        or set(config["limits"]) != {"max_total_tokens", "max_estimated_cost_usd"}
        or type(config["limits"]["max_total_tokens"]) is not int
        or not isinstance(config["limits"]["max_estimated_cost_usd"], str)
    ):
        _invalid("Choose explicit token and decimal-string spend ceilings.")
    try:
        cost = Decimal(config["limits"]["max_estimated_cost_usd"])
        if not cost.is_finite() or cost <= 0 or int(cost.as_tuple().exponent) < -8:
            _invalid(
                "Choose a positive finite spend ceiling with at most eight decimal places."
            )
    except InvalidOperation:
        _invalid("Choose a valid decimal-string spend ceiling.")
    if not isinstance(config["competitors"], list):
        _invalid("Competitors must be an explicit list.")
    for item in config["competitors"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"skill_id", "version"}
            or type(item["version"]) is not int
            or item["version"] < 1
        ):
            _invalid("Competitors must pin published versions.")
        try:
            item["skill_id"] = str(UUID(item["skill_id"]))
        except (ValueError, TypeError, AttributeError):
            _invalid("Invalid competitor identity.")
    selection = config["cases"]
    if not isinstance(selection, dict) or selection.get("kind") not in {
        "saved",
        "ad_hoc",
    }:
        _invalid("Choose saved or ad-hoc cases.")
    expected = (
        {"kind", "suite_revision", "case_ids"}
        if selection["kind"] == "saved"
        else {"kind", "cases"}
    )
    if set(selection) != expected:
        _invalid("Use the exact case-selection fields.")
    if selection["kind"] == "saved" and (
        not isinstance(selection["case_ids"], list)
        or len(set(selection["case_ids"])) != len(selection["case_ids"])
    ):
        _invalid("Select distinct saved cases.")
    candidate = config["candidate"]
    if not isinstance(candidate, dict) or candidate.get("kind") not in {
        "draft",
        "version",
    }:
        _invalid("Choose an acknowledged draft or immutable version.")
    ref = "revision" if candidate["kind"] == "draft" else "version"
    if (
        set(candidate) != {"kind", ref, "digest"}
        or type(candidate[ref]) is not int
        or candidate[ref] < 1
    ):
        _invalid("Candidate must include its exact revision/version and digest.")
    return config


def snapshot_identity(bundle: dict) -> dict:
    return {
        key: bundle[key]
        for key in (
            "skill_id",
            "version_id",
            "version",
            "draft_revision",
            "digest",
            "execution_manifest_digest",
        )
    }


def _bundle(
    skill: HubSkill, *, files: list[FileInput], version=None, draft_revision=None
) -> dict:
    checks = run_checks(files, expected_name=skill.name)
    if checks["summary"]["errors"]:
        raise SkillsHubError(
            "checks_failed",
            "Fix structural errors before testing.",
            status=422,
            detail=checks,
        )
    # Raw metadata is used only in the private snapshot. Preview/check output stays redacted.
    metadata, _, _ = parse_frontmatter(
        next(item["content"] for item in files if item["path"] == "SKILL.md")
    )
    if metadata is None:
        _invalid("The root instruction metadata is invalid.")
    digest = manifest_digest(files)
    return {
        "skill_id": str(skill.id),
        "version_id": str(version.id) if version else None,
        "version": version.number if version else None,
        "draft_revision": draft_revision,
        "digest": digest,
        "execution_manifest_digest": digest,
        "name": metadata["name"],
        "description": metadata["description"],
        "metadata": metadata,
        "files": [
            {
                **item,
                "sha256": file_sha256(item["content"]),
                "size_bytes": len(item["content"].encode()),
            }
            for item in sorted(files, key=lambda item: item["path"])
        ],
    }


def _version_bundle(skill: HubSkill, number: int) -> dict:
    version = skill.versions.filter(number=number).first()
    if version is None:
        raise SkillsHubError(
            "version_not_found", "Published version not found.", status=404
        )
    from model_hub.services.skills_hub.v12.resolution_leases import (
        require_distribution_eligibility,
    )

    require_distribution_eligibility(version)
    files: list[FileInput] = [
        {"path": item.path, "content": item.blob.content}
        for item in version.files.select_related("blob").order_by("path")
    ]
    return _bundle(skill, files=files, version=version)


def _plan(
    *,
    principal: SkillPrincipal,
    skill_id: UUID,
    configuration: dict,
    lock: bool = False,
) -> tuple[dict, dict]:
    if not settings.SKILLS_TESTING_ENABLED:
        raise SkillsHubError(
            "skills_unavailable", "Skills model testing is not enabled.", status=503
        )
    config = _configuration(configuration)
    validate_run_limits(config, skill_id=skill_id)
    assert principal.user_id is not None
    scope = principal.scope
    requested_ids = {
        str(skill_id),
        *(item["skill_id"] for item in config["competitors"]),
    }
    queryset = HubSkill.no_workspace_objects.filter(
        id__in=requested_ids,
        kind=HubSkill.Kind.SKILL,
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
    ).order_by("id")
    if lock:
        queryset = queryset.select_for_update()
    skills = {str(skill.id): skill for skill in queryset}
    if set(skills) != requested_ids:
        raise SkillsHubError("resource_not_found", "Skill not found.", status=404)
    skill = skills[str(skill_id)]
    selection = config["cases"]
    suite_query = HubSkillTestSuite.objects.filter(
        skill=skill,
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
    )
    if lock:
        suite_query = suite_query.select_for_update()
    suite = suite_query.first()
    if selection["kind"] == "saved":
        if suite is None or suite.revision != selection["suite_revision"]:
            raise SkillsHubError(
                "test_plan_changed", "Saved examples changed.", status=409
            )
        by_id = {case["id"]: case for case in suite.cases}
        if not set(selection["case_ids"]) <= set(by_id):
            _invalid("Selected case is not in the saved suite.")
        cases = [by_id[value] for value in selection["case_ids"]]
        validate_cases(
            [{key: case[key] for key in _CASE_FIELDS} for case in cases],
            require_reviewed=True,
        )
    else:
        cases = validate_cases(selection["cases"], require_reviewed=True)
        cases = [
            {
                **case,
                "reviewed_by": {"id": str(principal.user_id), "name": ""},
                "reviewed_at": None,
                "origin": "manual",
                "exposed_to_assist": False,
            }
            for case in cases
        ]
    for case in cases:
        for check in case["checks"]:
            if check["kind"] == "semantic" and config["judge"] is None:
                _invalid("Semantic expectations require an explicitly selected judge.")
            if check["kind"] == "json_schema":
                validate_schema(check["config"]["schema"])
    candidate_ref = config["candidate"]
    if candidate_ref["kind"] == "draft":
        drafts = HubSkillDraft.objects.filter(skill=skill, user_id=principal.user_id)
        if lock:
            drafts = drafts.select_for_update()
        draft = drafts.first()
        if draft is None or draft.revision != candidate_ref["revision"]:
            raise SkillsHubError(
                "test_plan_changed",
                "Save and preview the current draft revision.",
                status=409,
            )
        candidate = _bundle(skill, files=draft.files, draft_revision=draft.revision)
    else:
        candidate = _version_bundle(skill, candidate_ref["version"])
    if candidate["digest"] != candidate_ref["digest"]:
        raise SkillsHubError(
            "test_plan_changed", "Candidate content changed.", status=409
        )
    production, event = None, None
    if "production" in config["baselines"]:
        label = skill.labels.select_related("version").filter(name="production").first()
        if label is None:
            raise SkillsHubError(
                "label_not_found",
                "Production is not assigned; choose a baseline explicitly.",
                status=404,
            )
        production = _version_bundle(skill, label.version.number)
        event = (
            skill.label_events.filter(label="production")
            .order_by("-created_at", "-id")
            .first()
        )
    competitors = [
        _version_bundle(skills[item["skill_id"]], item["version"])
        for item in config["competitors"]
    ]
    # Exposure survives proposal expiry; only actually applied proposals affect later claims.
    exposed_groups = set()
    for groups in HubSkillAssistProposal.objects.filter(
        skill=skill,
        job__organization_id=scope.organization_id,
        job__workspace_id=scope.workspace_id,
    ).values_list("exposed_source_group_ids", flat=True):
        exposed_groups.update(groups)
    # Only selected, already-authorized group identifiers enter the snapshot;
    # never disclose other authors' unselected private proposal lineage.
    exposed_groups.intersection_update(case["source_group_id"] for case in cases)
    for case in cases:
        case["exposed_to_assist"] = (
            case.get("exposed_to_assist", False)
            or case["source_group_id"] in exposed_groups
        )
    if config["parent_run_id"] is not None:
        parent = HubSkillTestRun.objects.filter(
            job_id=config["parent_run_id"],
            job__skill=skill,
            job__organization_id=scope.organization_id,
            job__workspace_id=scope.workspace_id,
        ).first()
        if parent is None or (
            parent.visibility != "workspace"
            and parent.job.owner_user_id != principal.user_id
        ):
            raise SkillsHubError(
                "resource_not_found", "Parent run not found.", status=404
            )
    task_connection = resolve_test_connection(
        scope=scope, model_id=config["model"]["model_id"]
    )
    judge_connection = (
        resolve_test_connection(scope=scope, model_id=config["judge"]["model_id"])
        if config["judge"]
        else None
    )
    connections = {"task": task_connection, "judge": judge_connection}
    limits = effective_limits()
    profile = (
        limits["profiles"]["quick"]
        if config["profile"] == "quick"
        else limits["profiles"]["repeated"]
    )
    bundles = [candidate, *([production] if production else []), *competitors]
    inventory = build_inventory(bundles=bundles, cases=cases)
    attempts = len(cases) * (1 + len(config["baselines"])) * config["repetitions"]
    semantic_attempts = (
        sum(
            any(check["kind"] == "semantic" for check in case["checks"])
            for case in cases
        )
        * (1 + len(config["baselines"]))
        * config["repetitions"]
    )
    # Worst possible per-call input is context minus output; admission does not promise all work fits the user's stop budget.
    upper = Decimal(0)
    for name, count, output in (
        (
            "task",
            attempts * profile["max_task_turns_per_attempt"],
            config["model"]["max_output_tokens"],
        ),
        (
            "judge",
            semantic_attempts,
            config["judge"]["max_output_tokens"] if config["judge"] else 0,
        ),
    ):
        connection = connections[name]
        if connection and count:
            upper += count * (
                (connection.context_limit - output) * connection.input_price
                + output * connection.output_price
            )
    upper = min(Decimal(upper), Decimal(config["limits"]["max_estimated_cost_usd"]))
    snapshot = {
        "configuration": config,
        "candidate": candidate,
        "production": production,
        "competitors": competitors,
        "cases": cases,
        "suite_id": str(suite.id) if suite and selection["kind"] == "saved" else None,
        "suite_revision": (
            suite.revision if suite and selection["kind"] == "saved" else None
        ),
        "full_suite": bool(
            suite
            and selection["kind"] == "saved"
            and set(selection["case_ids"]) == {case["id"] for case in suite.cases}
        ),
        "suite_case_count": len(suite.cases) if suite else None,
        "production_event_id": str(event.id) if event else None,
        "release_revision": getattr(skill, "release_revision", 0),
        "connections": {
            name: connection.identity() if connection else None
            for name, connection in connections.items()
        },
        "execution_configuration": {
            "model": config["model"],
            "judge": config["judge"],
            "harness_version": HARNESS_VERSION,
            "adapter_version": ADAPTER_VERSION,
            "evaluator_version": EVALUATOR_VERSION,
        },
        "limits": limits,
        "retention_days": retention_days(),
        "exposed_source_group_ids": sorted(exposed_groups),
        **inventory,
    }
    plan_fingerprint = fingerprint(
        {
            "scope": [
                str(scope.organization_id),
                str(scope.workspace_id),
                str(principal.user_id),
            ],
            "snapshot": snapshot,
        }
    )
    snapshot["plan_fingerprint"] = plan_fingerprint
    snapshot["outbound_findings"] = bind_findings(
        inventory["outbound_findings"], plan_fingerprint
    )
    # Hash ordering yields reproducible interleaving without global PRNG state or mutable labels.
    schedule = [
        {"case_id": case["id"], "variant": variant, "repeat_index": repeat}
        for case in cases
        for repeat in range(config["repetitions"])
        for variant in ["candidate", *config["baselines"]]
    ]
    snapshot["schedule_seed"] = plan_fingerprint
    snapshot["schedule"] = sorted(
        schedule, key=lambda cell: fingerprint([plan_fingerprint, cell])
    )
    # Validate the actual initial prompt/context for every arm, with no truncation.
    from model_hub.services.skills_hub.v11.model_calls import call_allowance
    from model_hub.services.skills_hub.v11.runner import (
        build_task_messages,
        tool_definitions,
    )

    for case in cases:
        for variant in ["candidate", *config["baselines"]]:
            messages, catalog = build_task_messages(
                snapshot=snapshot, case=case, variant=variant
            )
            call_allowance(
                connection=task_connection,
                messages=messages,
                tools=tool_definitions() if catalog else [],
                max_output_tokens=config["model"]["max_output_tokens"],
            )
    if len(canonical_json(snapshot).encode()) > MAX_SNAPSHOT_BYTES:
        raise SkillsHubError(
            "payload_too_large",
            "Complete snapshot exceeds 16 MiB; no files were omitted.",
            status=413,
        )
    preview = {
        "fingerprint": plan_fingerprint,
        "candidate": snapshot_identity(candidate),
        "production": snapshot_identity(production) if production else None,
        "production_event_id": snapshot["production_event_id"],
        "release_revision": snapshot["release_revision"],
        "competitors": [snapshot_identity(bundle) for bundle in competitors],
        "case_count": len(cases),
        "variant_count": 1 + len(config["baselines"]),
        "repetitions": config["repetitions"],
        "attempt_count": attempts,
        "max_task_calls": attempts * profile["max_task_turns_per_attempt"],
        "max_judge_calls": semantic_attempts,
        "estimate": {
            "upper_bound_usd": format(upper, ".8f"),
            "pricing_source": task_connection.pricing_source,
        },
        "limits": limits,
        "inventory": inventory["inventory"],
        "outbound_findings": snapshot["outbound_findings"],
        "warnings": (
            [
                {
                    "code": "identical_inputs",
                    "message": "Candidate and production have identical files.",
                }
            ]
            if production and production["digest"] == candidate["digest"]
            else []
        ),
        "provider_disclosure": {
            "task_provider": task_connection.provider,
            "judge_provider": (
                connections["judge"].provider if connections["judge"] else None
            ),
        },
    }
    return preview, snapshot


def preview_test_run(
    *, principal: SkillPrincipal, skill_id: UUID, configuration: dict
) -> dict:
    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=True)
    return _plan(principal=principal, skill_id=skill_id, configuration=configuration)[0]


def create_test_run(
    *, principal: SkillPrincipal, skill_id: UUID, idempotency_key: UUID, request: dict
) -> HubSkillTestRun:
    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=True)
    try:
        request_digest = fingerprint({"skill_id": skill_id, "request": request})
    except (ValueError, TypeError, RecursionError):
        _invalid("Run request must contain finite JSON values.")
    existing = existing_job(
        principal=principal,
        kind="test",
        idempotency_key=idempotency_key,
        request_digest=request_digest,
    )
    if existing:
        return HubSkillTestRun.objects.get(job=existing)
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
        _invalid("Confirm provider processing and the exact reviewed plan.")
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
        try:
            preview, snapshot = _plan(
                principal=principal,
                skill_id=skill_id,
                configuration=request["configuration"],
                lock=True,
            )
        except SkillsHubError as error:
            if error.code in {
                "model_not_configured",
                "model_pricing_unavailable",
                "model_tools_unsupported",
                "label_not_found",
            }:
                raise SkillsHubError(
                    "test_plan_changed",
                    "A previewed model connection or baseline is no longer available.",
                    status=409,
                ) from error
            raise
        if request["expected_fingerprint"] != preview["fingerprint"]:
            raise SkillsHubError(
                "test_plan_changed",
                "Inputs or configuration changed; preview again.",
                status=409,
            )
        review, reasons = validate_inventory_review(
            plan_fingerprint=preview["fingerprint"],
            inventory=preview["inventory"],
            findings=preview["outbound_findings"],
            review=request["inventory_review"],
            purpose=snapshot["configuration"]["purpose"],
            exposed=any(case["exposed_to_assist"] for case in snapshot["cases"]),
        )
        config = snapshot["configuration"]
        if config["parent_run_id"] and not snapshot["full_suite"]:
            reasons = sorted({*reasons, "subset_rerun"})
        snapshot["inventory_review"] = review
        execution_fingerprint = fingerprint(
            {"plan_fingerprint": preview["fingerprint"], "inventory_review": review}
        )
        snapshot["execution_fingerprint"] = execution_fingerprint
        if len(canonical_json(snapshot).encode()) > MAX_SNAPSHOT_BYTES:
            raise SkillsHubError(
                "payload_too_large", "Reviewed snapshot exceeds 16 MiB.", status=413
            )
        job = create_job_locked(
            principal=principal,
            kind="test",
            skill_id=skill_id,
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
            execution_fingerprint=execution_fingerprint,
            candidate_digest=snapshot["candidate"]["digest"],
            candidate=preview["candidate"],
            production=preview["production"],
            competitors=preview["competitors"],
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


def share_test_run(
    *, principal: SkillPrincipal, run_id: UUID, acknowledge_snapshot_sharing: bool
) -> HubSkillTestRun:
    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=True)
    if acknowledge_snapshot_sharing is not True:
        _invalid(
            "Explicitly acknowledge workspace disclosure of the entire snapshot and evidence."
        )
    with transaction.atomic():
        run = (
            HubSkillTestRun.objects.select_for_update()
            .filter(
                job_id=run_id,
                job__organization_id=principal.scope.organization_id,
                job__workspace_id=principal.scope.workspace_id,
                job__owner_user_id=principal.user_id,
                job__skill__deleted=False,
            )
            .first()
        )
        if run is None:
            raise SkillsHubError("resource_not_found", "Run not found.", status=404)
        if run.visibility == "private":
            run.visibility, run.shared_at, run.shared_by_id = (
                "workspace",
                timezone.now(),
                principal.user_id,
            )
            run.save(update_fields=["visibility", "shared_at", "shared_by"])
        return run
