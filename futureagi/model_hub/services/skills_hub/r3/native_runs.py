"""J2 application verification: matched native runs of one qualified application.

The control plane freezes exact immutable context arms, reviewed cases and the
qualified application configuration, reserves the shared workspace allowance,
binds owner-private native evidence to a Simulation job and dispatches the guest
payload to the configured native runtime. Guests return observations only; the
control plane grades selection, delivery, business-tool and answer outcomes from
those observations and the frozen arm bytes. Expectations never reach a guest.

No runtime is configured by default. Production must configure a qualified
runtime (E2B is not wired here); the E2E overlay configures a container runtime
whose evidence is explicitly a synthetic fixture.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from model_hub.models import HubSkillNativeEvidence
from model_hub.models.skills_hub import HubSkillVersion
from model_hub.models.skills_hub_v12 import (
    HubSkillContextBinding,
    HubSkillContextCredential,
)
from model_hub.selectors.skills_hub_v12 import get_resource
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.packages import file_sha256, manifest_digest
from model_hub.services.skills_hub.r3 import spend_policy
from model_hub.services.skills_hub.r3.native_evidence import (
    bind_native_evidence_locked,
    get_native_evidence,
)
from model_hub.services.skills_hub.r3.native_reducer import (
    NativeCheck,
    NativeTrial,
    Verdict,
    planned_matrix,
    reduce_native_results,
)
from model_hub.services.skills_hub.snapshots import snapshot_version
from model_hub.services.skills_hub.v11.inventory import canonical_json, fingerprint
from model_hub.services.skills_hub.v11.principals import (
    require_private_principal,
    require_scope_access,
)
from model_hub.services.skills_hub.v11.suites import get_suite, validate_cases
from model_hub.services.skills_hub.v11.types import SkillPrincipal
from model_hub.services.skills_hub.v12.credentials import (
    _hash_secret,
    canonical_digest,
)
from simulate.models.hosted_harness import (
    HostedHarnessAttempt,
    HostedHarnessJob,
    HostedHarnessReceipt,
    HostedHarnessScenario,
)
from simulate.services.hosted_harness import hash_secret
from simulate.services.skills_context_adapter import (
    PROTOCOL,
    NativeExecutionSpec,
    build_execution_spec,
    validate_native_configuration,
    validate_native_result_receipt,
    verify_guest_health,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "skills-native-v1"
EXPECTATION_FIXTURE = "fi.application.expectations"
CASE_FIELDS = (
    "id",
    "title",
    "source_group_id",
    "prompt",
    "fixtures",
    "expected_trigger",
    "checks",
    "reviewed",
)
ACTIONS = {"issue_refund", "open_chargeback", "track_shipment", "none"}
CEILING_PER_CELL_USD = Decimal("0.05")
RETENTION = timedelta(days=30)
ATTEMPT_TTL = timedelta(minutes=45)
MAX_OBSERVATION_BYTES = 512 * 1024


def _error(code: str, message: str, status: int = 409) -> SkillsHubError:
    return SkillsHubError(code, message, status=status)


def runtime_url() -> str:
    return str(getattr(settings, "SKILLS_NATIVE_RUNTIME_URL", "") or "").rstrip("/")


def runtime_callback_url() -> str:
    return str(getattr(settings, "SKILLS_NATIVE_CALLBACK_URL", "") or "").rstrip("/")


def runtime_status() -> dict:
    return {
        "available": bool(runtime_url() and runtime_callback_url()),
        "runtime": str(getattr(settings, "SKILLS_NATIVE_RUNTIME_NAME", "") or "")
        or None,
        "evidence_class": str(
            getattr(settings, "SKILLS_NATIVE_EVIDENCE_CLASS", "") or ""
        )
        or None,
    }


# --- Application targets ---------------------------------------------------


def _binding_rows(principal: SkillPrincipal, context_id: UUID):
    return HubSkillContextBinding.objects.filter(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        context_id=context_id,
        kind="runtime",
        retired_at__isnull=True,
    ).exclude(profile="native-verification")


def _qualified(binding: HubSkillContextBinding) -> dict | None:
    if not binding.configuration or not binding.configuration_digest:
        return None
    try:
        config = validate_native_configuration(binding.configuration)
    except SkillsHubError:
        return None
    if canonical_digest(config) != binding.configuration_digest:
        return None
    return config


def _target_output(binding: HubSkillContextBinding) -> dict:
    config = _qualified(binding)
    return {
        "binding_id": str(binding.pk),
        "name": binding.name,
        "revision": binding.revision,
        "selector": binding.selector,
        "qualified": config is not None,
        "configuration_digest": binding.configuration_digest or None,
        "configuration": config,
    }


def runtime_configuration(*, principal: SkillPrincipal) -> dict:
    """The pinned configuration the runtime measures from its live image and mounts.

    The reviewer pins it explicitly; launch re-measures and refuses a mismatch.
    """
    require_private_principal(principal=principal)
    if not runtime_status()["available"]:
        raise _error(
            "skills_unavailable",
            "No qualified native application runtime is configured in this deployment.",
            503,
        )
    try:
        response = requests.get(
            f"{runtime_url()}/config",
            headers={
                "Authorization": "Bearer "
                + str(getattr(settings, "SKILLS_NATIVE_RUNTIME_TOKEN", "") or "")
            },
            timeout=(5, 30),
            allow_redirects=False,
        )
        response.raise_for_status()
        configuration = validate_native_configuration(response.json())
    except (requests.RequestException, ValueError) as exc:
        raise _error(
            "skills_unavailable", "The native runtime did not describe itself.", 503
        ) from exc
    return {"configuration": configuration}


def create_application_target(
    *, principal: SkillPrincipal, context_id: UUID, version: int
) -> dict:
    """Register the application that consumes this context as a runtime target."""
    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=True)
    status = runtime_status()
    if not status["available"]:
        raise _error(
            "skills_unavailable",
            "No qualified native application runtime is configured in this deployment.",
            503,
        )
    with transaction.atomic():
        context = get_resource(
            principal=principal,
            resource_id=context_id,
            kind="agent_context",
            lock=True,
        )
        _version(context, version)
        name = f"{status['runtime'] or 'native'} application"[:120]
        existing = _binding_rows(principal, context_id).filter(name=name).first()
        if existing is not None:
            return _target_output(existing)
        binding = HubSkillContextBinding.objects.create(
            organization_id=principal.scope.organization_id,
            workspace_id=principal.scope.workspace_id,
            context=context,
            name=name,
            kind="runtime",
            selector={"version": version},
            update_mode="manual",
            profile="native-application",
            profile_version="1",
            repository=None,
            expected_report_interval_seconds=3600,
        )
        return _target_output(binding)


def list_application_targets(*, principal: SkillPrincipal, context_id: UUID) -> dict:
    require_private_principal(principal=principal)
    get_resource(principal=principal, resource_id=context_id, kind="agent_context")
    return {
        "runtime": runtime_status(),
        # A pinned application is the verification target; other runtime bindings follow it.
        "targets": [
            _target_output(row)
            for row in sorted(
                _binding_rows(principal, context_id).order_by("name", "id"),
                key=lambda row: not row.configuration_digest,
            )
        ],
    }


def qualify_application_target(
    *,
    principal: SkillPrincipal,
    binding_id: UUID,
    revision: int,
    configuration: dict,
) -> dict:
    """Record the exact pinned application configuration a CLI derived on the host.

    Validation is domain shape only; runtime qualification happens when the
    guest proves the same identities and isolation in its authenticated health.
    """
    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=True)
    config = validate_native_configuration(configuration)
    with transaction.atomic():
        binding = (
            HubSkillContextBinding.objects.select_for_update()
            .filter(
                pk=binding_id,
                organization_id=principal.scope.organization_id,
                workspace_id=principal.scope.workspace_id,
                kind="runtime",
                retired_at__isnull=True,
                context__deleted=False,
            )
            .exclude(profile="native-verification")
            .first()
        )
        if binding is None:
            raise _error("resource_not_found", "Application target not found.", 404)
        get_resource(
            principal=principal,
            resource_id=binding.context_id,
            kind="agent_context",
            lock=True,
        )
        if type(revision) is not int or revision != binding.revision:
            raise _error("binding_conflict", "Target configuration changed.")
        digest = canonical_digest(config)
        if binding.configuration_digest != digest:
            binding.configuration = config
            binding.configuration_digest = digest
            binding.revision += 1
            binding.save(
                update_fields=[
                    "configuration",
                    "configuration_digest",
                    "revision",
                    "updated_at",
                ]
            )
        return _target_output(binding)


# --- Planning ----------------------------------------------------------------


def _version(context, number: object) -> HubSkillVersion:
    if type(number) is not int or number < 1:
        raise _error("version_not_found", "Choose a published version.", 404)
    version = (
        HubSkillVersion.objects.select_related("skill")
        .filter(skill=context, number=number)
        .first()
    )
    if version is None:
        raise _error("version_not_found", "The selected version is unavailable.", 404)
    return version


def _snapshot(version: HubSkillVersion) -> dict:
    value = snapshot_version(version)
    files = [
        {"path": item["path"], "content": item["content"]} for item in value["files"]
    ]
    if manifest_digest(files) != version.digest:
        raise _error(
            "candidate_changed", "Version bytes do not match their digest.", 409
        )
    return {
        "skill_id": value["skill_id"],
        "version_id": value["version_id"],
        "version": value["version"],
        "digest": value["digest"],
        "files": files,
    }


def _catalog(snapshot: dict) -> dict[str, dict]:
    files = {item["path"]: item["content"] for item in snapshot["files"]}
    try:
        descriptor = json.loads(files[".futureagi/context.json"])
        links = descriptor["links"]
    except (KeyError, ValueError, TypeError):
        return {}
    catalog = {}
    for link in links if isinstance(links, list) else []:
        if not isinstance(link, dict):
            continue
        mount = link.get("mount")
        skill_id = link.get("skill_id")
        root = files.get(f"{mount}SKILL.md") if isinstance(mount, str) else None
        if isinstance(skill_id, str) and root is not None:
            name = re.search(r"^name:\s*(.+)$", root, re.MULTILINE)
            catalog[skill_id] = {
                "skill_id": skill_id,
                "name": name.group(1).strip() if name else skill_id,
                "mount": mount,
                "root_sha256": file_sha256(root),
                "root": root,
            }
    return catalog


def parse_expectations(case: dict) -> dict | None:
    fixtures = [
        item for item in case["fixtures"] if item["name"] == EXPECTATION_FIXTURE
    ]
    if len(fixtures) != 1:
        return None
    try:
        value = json.loads(fixtures[0]["text"])
    except ValueError:
        return None
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "skill_id", "action"}
        or value["schema"] != 1
        or value["action"] not in ACTIONS
    ):
        return None
    try:
        value["skill_id"] = str(UUID(str(value["skill_id"])))
    except (ValueError, TypeError):
        return None
    return value


def _state_key(configuration: dict) -> str:
    schema: str = configuration["state_schema"]
    return schema.split("/", 1)[0]


def _state_fixture(case: dict, key: str) -> bool:
    for item in case["fixtures"]:
        try:
            value = json.loads(item["text"])
        except ValueError:
            continue
        if isinstance(value, dict) and isinstance(value.get(key), dict):
            return True
    return False


def _case_checks(case: dict, variants: list[str]) -> list[dict]:
    checks = [
        {"check_id": family, "family": family, "variants": list(variants)}
        for family in ("selection", "delivery", "tools")
    ]
    checks += [
        {"check_id": f"answer:{check['id']}", "family": "answer", "variants": variants}
        for check in case["checks"]
        if check["kind"] in {"contains", "not_contains"}
    ]
    return checks


def _plan(*, principal: SkillPrincipal, context_id: UUID, request: dict) -> dict:
    require_private_principal(principal=principal)
    if not isinstance(request, dict) or set(request) - {
        "binding_id",
        "binding_revision",
        "candidate_version",
        "baselines",
        "case_ids",
        "repetitions",
        "historical_case_ids",
    }:
        raise _error("invalid_test_config", "Use the exact native plan fields.", 400)
    context = get_resource(
        principal=principal, resource_id=context_id, kind="agent_context"
    )
    binding = (
        _binding_rows(principal, context_id)
        .filter(pk=request.get("binding_id"))
        .first()
    )
    if binding is None:
        raise _error("resource_not_found", "Application target not found.", 404)
    if binding.revision != request.get("binding_revision"):
        raise _error("binding_conflict", "Target configuration changed.")
    configuration = _qualified(binding)
    if configuration is None:
        raise _error(
            "runtime_incompatible",
            "Qualify the application target with its exact pinned configuration first.",
            422,
        )
    baselines = request.get("baselines")
    if not isinstance(baselines, dict) or not 1 <= len(baselines) <= 2:
        raise _error("invalid_test_config", "Choose one or two baseline arms.", 400)
    candidate = _version(context, request.get("candidate_version"))
    arms = {"candidate": candidate}
    for variant, number in baselines.items():
        if variant not in {"reference", "production"}:
            raise _error("invalid_test_config", "Unsupported baseline arm.", 400)
        arms[variant] = _version(context, number)
    if len({version.digest for version in arms.values()}) != len(arms):
        raise _error(
            "invalid_test_config", "Matched arms must have different bytes.", 400
        )
    suite = get_suite(scope=principal.scope, skill_id=context_id)
    by_id = {case["id"]: case for case in suite["cases"]}
    case_ids = request.get("case_ids")
    if not isinstance(case_ids, list) or not case_ids:
        raise _error("invalid_test_config", "Choose reviewed cases.", 400)
    cases = []
    for case_id in case_ids:
        case = by_id.get(str(case_id))
        if case is None:
            raise _error("resource_not_found", "Case not found.", 404)
        # Stored cases carry review audit fields; validation takes the documented shape.
        cases.append({key: case[key] for key in CASE_FIELDS})
    cases = validate_cases(cases, require_reviewed=True)
    historical = [str(value) for value in request.get("historical_case_ids") or []]
    if not set(historical) <= {case["id"] for case in cases}:
        raise _error(
            "invalid_test_config", "Historical cases must be selected cases.", 400
        )
    if historical and "reference" not in arms:
        raise _error(
            "invalid_test_config",
            "Historical reproduction needs the recorded reference arm.",
            400,
        )
    snapshots = {variant: _snapshot(version) for variant, version in arms.items()}
    catalogs = {variant: _catalog(value) for variant, value in snapshots.items()}
    state_key = _state_key(configuration)
    missing = []
    expectations = {}
    for case in cases:
        expected = parse_expectations(case)
        if expected is None:
            missing.append(
                {
                    "case_id": case["id"],
                    "input": "reviewed application expectation",
                    "detail": "Review which skill and business action this case requires.",
                }
            )
            continue
        expectations[case["id"]] = expected
        if not _state_fixture(case, state_key):
            missing.append(
                {
                    "case_id": case["id"],
                    "input": f"business state fixture ({state_key})",
                    "detail": "The captured case has no state record for the "
                    "application's pinned state schema. Capture the source tool "
                    "result that carried it; it cannot be reconstructed here.",
                }
            )
        for variant, catalog in catalogs.items():
            if expected["skill_id"] not in catalog:
                missing.append(
                    {
                        "case_id": case["id"],
                        "input": f"catalog member in {variant}",
                        "detail": "The required skill is not in this arm's catalog.",
                    }
                )
    repetitions = request.get("repetitions")
    matrix = planned_matrix(
        case_ids=[case["id"] for case in cases],
        baselines=[variant for variant in arms if variant != "candidate"],
        repetitions=repetitions,
    )
    variants = list(arms)
    required = {case["id"]: _case_checks(case, variants) for case in cases}
    guest_cases = [
        {
            **case,
            "fixtures": [
                item for item in case["fixtures"] if item["name"] != EXPECTATION_FIXTURE
            ],
        }
        for case in cases
    ]
    identity = {
        "schema": 1,
        "protocol": PROTOCOL,
        "context_id": str(context_id),
        "binding": {"id": str(binding.pk), "revision": binding.revision},
        "configuration_digest": binding.configuration_digest,
        "arms": {
            variant: {"version": version.number, "digest": version.digest}
            for variant, version in arms.items()
        },
        "suite_revision": suite["revision"],
        "cases": [canonical_digest(case) for case in cases],
        "historical": sorted(historical),
        "repetitions": repetitions,
    }
    return {
        "context": context,
        "binding": binding,
        "configuration": configuration,
        "arms": arms,
        "snapshots": snapshots,
        "catalogs": catalogs,
        "cases": cases,
        "guest_cases": guest_cases,
        "matrix": matrix,
        "required": required,
        "expectations": expectations,
        "historical": historical,
        "missing": missing,
        "fingerprint": fingerprint(identity),
    }


def _plan_output(plan: dict) -> dict:
    catalog = plan["catalogs"]["candidate"]
    reproducible = not plan["missing"]
    return {
        "execution_fingerprint": plan["fingerprint"],
        "runtime": runtime_status(),
        "target": _target_output(plan["binding"]),
        "arms": [
            {
                "variant": variant,
                "version": version.number,
                "version_id": str(version.pk),
                "digest": version.digest,
                "catalog_members": len(plan["catalogs"][variant]),
            }
            for variant, version in plan["arms"].items()
        ],
        "cases": [
            {
                "id": case["id"],
                "title": case["title"],
                "historical": case["id"] in plan["historical"],
                "expected_skill": (
                    catalog.get(plan["expectations"][case["id"]]["skill_id"], {}).get(
                        "name"
                    )
                    if case["id"] in plan["expectations"]
                    else None
                ),
                "expected_action": plan["expectations"]
                .get(case["id"], {})
                .get("action"),
            }
            for case in plan["cases"]
        ],
        "cells": len(plan["matrix"]),
        "ceiling_usd": str(CEILING_PER_CELL_USD * len(plan["matrix"])),
        "reproducible": reproducible,
        "reproduction": "ready" if reproducible else "not_reproduced",
        "missing_inputs": plan["missing"],
    }


def preview_native_run(
    *, principal: SkillPrincipal, context_id: UUID, request: dict
) -> dict:
    return _plan_output(
        _plan(principal=principal, context_id=context_id, request=request)
    )


# --- Launch ------------------------------------------------------------------


def _arm_access(
    *, principal: SkillPrincipal, context, variant: str, version, expires_at
) -> tuple[HubSkillContextBinding, HubSkillContextCredential, str]:
    """A disposable exact-version binding and consume credential for one arm."""
    binding = HubSkillContextBinding.objects.create(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        context=context,
        name=f"Native verification {variant} v{version.number}",
        kind="runtime",
        selector={"version": version.number},
        update_mode="manual",
        profile="native-verification",
        profile_version="1",
        repository=None,
        expected_report_interval_seconds=3600,
    )
    credential_id = uuid4()
    token = f"{credential_id}.{secrets.token_urlsafe(32)}"
    credential = HubSkillContextCredential.objects.create(
        id=credential_id,
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        purpose="consume",
        grants=[
            {
                "kind": "agent_context",
                "resource_id": str(context.pk),
                "labels": [],
                "version_ids": [str(version.pk)],
            }
        ],
        binding_ids=[],
        secret_hash=_hash_secret(token),
        expires_at=expires_at,
        created_by_id=principal.user_id,
    )
    return binding, credential, token


def create_native_run(
    *,
    principal: SkillPrincipal,
    context_id: UUID,
    request: dict,
    idempotency_key: UUID,
) -> dict:
    require_scope_access(principal=principal, write=True)
    if not isinstance(request, dict):
        raise _error("invalid_test_config", "Use the reviewed native plan.", 400)
    request = dict(request)
    accepted = request.pop("execution_fingerprint", None)
    acknowledged = request.pop("acknowledge_spend", None)
    if acknowledged is not True:
        raise _error(
            "invalid_test_config", "Explicitly acknowledge the paid ceiling.", 400
        )
    if not runtime_status()["available"]:
        raise _error(
            "skills_unavailable",
            "No qualified native application runtime is configured in this deployment.",
            503,
        )
    existing = (
        HostedHarnessJob.no_workspace_objects.filter(
            organization_id=principal.scope.organization_id,
            workspace_id=principal.scope.workspace_id,
            schema_version=SCHEMA_VERSION,
            idempotency_key=str(idempotency_key),
        )
        .select_related("hub_skill_native_evidence")
        .first()
    )
    if existing is not None:
        if existing.payload.get("skills_native_control", {}).get("request") != {
            **request,
            "execution_fingerprint": accepted,
        }:
            raise _error(
                "idempotency_conflict", "This operation key has another request."
            )
        return {"evidence_id": str(existing.hub_skill_native_evidence.pk)}
    with transaction.atomic():
        get_resource(
            principal=principal,
            resource_id=context_id,
            kind="agent_context",
            lock=True,
        )
        plan = _plan(principal=principal, context_id=context_id, request=request)
        if accepted != plan["fingerprint"]:
            raise _error(
                "candidate_changed", "The reviewed plan changed. Preview it again."
            )
        if plan["missing"]:
            raise _error(
                "not_reproducible",
                "Required historical inputs are missing; nothing was launched.",
                422,
            )
        spec = build_execution_spec(
            configuration=plan["configuration"],
            snapshots=plan["snapshots"],
            cases=plan["guest_cases"],
            matrix=plan["matrix"],
            execution_fingerprint=plan["fingerprint"],
        )
        now = timezone.now()
        token, fence = secrets.token_urlsafe(32), secrets.token_urlsafe(16)
        access = {}
        secrets_by_arm = {}
        for variant, version in plan["arms"].items():
            binding, credential, secret = _arm_access(
                principal=principal,
                context=plan["context"],
                variant=variant,
                version=version,
                expires_at=now + ATTEMPT_TTL,
            )
            access[variant] = {
                "binding_id": str(binding.pk),
                "credential_id": str(credential.pk),
            }
            secrets_by_arm[variant] = {
                "binding_id": str(binding.pk),
                "credential": secret,
            }
        control = {
            "request": {**request, "execution_fingerprint": accepted},
            "required_checks": plan["required"],
            "expectations": plan["expectations"],
            "historical": plan["historical"],
            "cases": plan["cases"],
            "application_binding_id": str(plan["binding"].pk),
            "arms": {
                variant: {
                    "version": version.number,
                    "version_id": str(version.pk),
                    "digest": version.digest,
                    **access[variant],
                }
                for variant, version in plan["arms"].items()
            },
            "catalogs": {
                variant: {
                    skill_id: {
                        "name": item["name"],
                        "mount": item["mount"],
                        "root_sha256": item["root_sha256"],
                    }
                    for skill_id, item in catalog.items()
                }
                for variant, catalog in plan["catalogs"].items()
            },
        }
        job = HostedHarnessJob.no_workspace_objects.create(
            organization_id=principal.scope.organization_id,
            workspace_id=principal.scope.workspace_id,
            run_id=uuid4(),
            name=f"Skills native verification {plan['context'].name}"[:255],
            idempotency_key=str(idempotency_key),
            request_digest=plan["fingerprint"],
            schema_version=SCHEMA_VERSION,
            payload={"skills_context": spec.guest_payload()},
            state=HostedHarnessJob.State.QUEUED,
            current_stage="queued",
            seed=0,
            scenario_count=len(plan["matrix"]),
            artifact_level="none",
            max_artifact_bytes=0,
            deadline_at=now + ATTEMPT_TTL,
        )
        reservation = spend_policy.reserve_admission(
            principal=principal,
            source="simulation_native",
            source_id=job.pk,
            operation_id=uuid4(),
            ceiling_usd=CEILING_PER_CELL_USD * len(plan["matrix"]),
        )
        evidence = bind_native_evidence_locked(
            principal=principal,
            resource_id=context_id,
            kind="agent_context",
            job=job,
            spec=spec,
            payload_expires_at=now + RETENTION,
            reservation_id=reservation.pk,
        )
        HostedHarnessScenario.no_workspace_objects.bulk_create(
            [
                HostedHarnessScenario(
                    job=job,
                    scenario_key=_cell_key(cell),
                    number=index + 1,
                    name=_cell_key(cell),
                )
                for index, cell in enumerate(plan["matrix"])
            ]
        )
        attempt = HostedHarnessAttempt.no_workspace_objects.create(
            job=job,
            attempt_number=1,
            token_hash=hash_secret(token),
            fence_hash=hash_secret(fence),
            expires_at=now + ATTEMPT_TTL,
            provider_ref=None,
        )
        job.current_attempt_number = 1
        job.state = HostedHarnessJob.State.PROVISIONING
        job.payload = {
            "skills_context": spec.guest_payload(),
            "skills_native_control": {**control, "reservation_id": str(reservation.pk)},
        }
        job.save(update_fields=["current_attempt_number", "state", "payload"])
        launch = {
            "schema": 1,
            "protocol": PROTOCOL,
            "job_id": str(job.pk),
            "attempt_id": str(attempt.pk),
            "attempt_number": 1,
            "token": token,
            "fence": fence,
            "callback_url": runtime_callback_url(),
            "registry_url": runtime_callback_url(),
            "environment": {"FI_SKILLS_TRACING": "off"},
            "application_binding_id": str(plan["binding"].pk),
            "arms": secrets_by_arm,
            "payload": spec.guest_payload(),
        }
        transaction.on_commit(lambda: _dispatch(job.pk, attempt.pk, launch))
    return {"evidence_id": str(evidence.pk)}


def _dispatch(job_id: UUID, attempt_id: UUID, launch: dict) -> None:
    try:
        response = requests.post(
            f"{runtime_url()}/launch",
            json=launch,
            headers={
                "Authorization": "Bearer "
                + str(getattr(settings, "SKILLS_NATIVE_RUNTIME_TOKEN", "") or "")
            },
            timeout=(5, 15),
            allow_redirects=False,
        )
        accepted = response.status_code == 202
    except requests.RequestException:
        accepted = False
    if not accepted:
        logger.warning("skills_native_dispatch_failed job=%s", job_id)
        _finish(job_id, attempt_id, failure="runtime_dispatch_failed")


def _cell_key(cell: dict) -> str:
    return f"{cell['case_id']}/{cell['variant']}/{cell['repeat_index']}"


# --- Authenticated guest ingestion -------------------------------------------


def _spec(job: HostedHarnessJob) -> NativeExecutionSpec:
    payload = job.payload["skills_context"]
    return NativeExecutionSpec(
        payload["configuration"],
        payload["snapshots"],
        payload["tasks"],
        payload["matrix"],
        payload["execution_fingerprint"],
    )


def _locked_attempt(attempt_id: UUID) -> HostedHarnessAttempt:
    attempt = (
        HostedHarnessAttempt.no_workspace_objects.select_for_update()
        .select_related("job")
        .get(pk=attempt_id)
    )
    if attempt.job.schema_version != SCHEMA_VERSION:
        raise _error("resource_not_found", "Native attempt not found.", 404)
    if attempt.state in {
        HostedHarnessAttempt.State.COMPLETED,
        HostedHarnessAttempt.State.FAILED,
        HostedHarnessAttempt.State.CANCELED,
        HostedHarnessAttempt.State.SUPERSEDED,
    }:
        raise _error("attempt_closed", "This native attempt is closed.", 409)
    return attempt


def record_guest_health(*, attempt: HostedHarnessAttempt, health: dict) -> dict:
    with transaction.atomic():
        attempt = _locked_attempt(attempt.pk)
        verify_guest_health(spec=_spec(attempt.job), health=health)
        attempt.state = HostedHarnessAttempt.State.RUNNING
        attempt.heartbeat_at = timezone.now()
        attempt.sandbox_runtime = {
            "health": health,
            "verified_at": timezone.now().isoformat(),
        }
        attempt.save(update_fields=["state", "heartbeat_at", "sandbox_runtime"])
        job = HostedHarnessJob.no_workspace_objects.select_for_update().get(
            pk=attempt.job_id
        )
        job.state = HostedHarnessJob.State.RUNNING
        job.current_stage = "running"
        job.save(update_fields=["state", "current_stage"])
    return {"accepted": True}


def _digest(value) -> str:
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def grade_observation(*, control: dict, case: dict, variant: str, body: dict) -> dict:
    """Evaluator-side grading from guest observations and frozen arm bytes."""
    application = body["application"]
    business = body["business"]
    expected = control["expectations"][case["id"]]
    catalog = control["catalogs"][variant]
    completed = application.get("status") == "completed"
    events = application.get("events") or []
    selected = application.get("selected") or []
    selections = [
        event["body"].get("skill_id")
        for event in events
        if event.get("kind") == "child_selection"
    ]
    member = catalog.get(expected["skill_id"])
    root = control["_roots"].get(variant, {}).get(member["mount"]) if member else None
    delivered = [
        event["body"]
        for event in events
        if event.get("kind") == "file_delivery"
        and event["body"].get("skill_id") == expected["skill_id"]
        and event["body"].get("path") == "SKILL.md"
    ]
    operations = business.get("operations") or []
    applied = [row for row in operations if row.get("status") == "applied"]
    attempted = sorted({row.get("action") for row in operations})
    answer = application.get("output") if completed else None

    def verdict(value: bool) -> Verdict:
        return "pass" if value else "fail"

    checks = []
    if completed:
        checks.append(
            NativeCheck(
                "selection",
                "selection",
                verdict(
                    selected == [expected["skill_id"]]
                    and set(selections) == {expected["skill_id"]}
                ),
            )
        )
        checks.append(
            NativeCheck(
                "delivery",
                "delivery",
                verdict(
                    root is not None
                    and bool(delivered)
                    and all(
                        row.get("content_digest") == _digest(root) for row in delivered
                    )
                ),
            )
        )
        if expected["action"] == "none":
            tools_ok = not operations
        else:
            tools_ok = (
                attempted == [expected["action"]]
                and len(applied) == 1
                and applied[0].get("action") == expected["action"]
            )
        checks.append(NativeCheck("tools", "tools", verdict(tools_ok)))
        for check in case["checks"]:
            if check["kind"] not in {"contains", "not_contains"}:
                continue
            config = check["config"]
            text = config["text"]
            haystack = answer or ""
            if not config["case_sensitive"]:
                text, haystack = text.casefold(), haystack.casefold()
            found = text in haystack
            checks.append(
                NativeCheck(
                    f"answer:{check['id']}",
                    "answer",
                    verdict(found if check["kind"] == "contains" else not found),
                )
            )
    summary = {
        "status": application.get("status"),
        "reason": application.get("reason"),
        "selected": [
            catalog.get(value, {}).get("name", "outside catalog") for value in selected
        ],
        "actions": [
            {"action": row.get("action"), "status": row.get("status")}
            for row in operations
        ],
        "answer": (answer or "")[:2000],
        "context_digest": application.get("context_digest"),
        "version_id": application.get("version_id"),
        "session_id": application.get("session_id"),
    }
    return {
        "status": "completed" if completed else "incomplete",
        "checks": checks,
        "summary": summary,
    }


def ingest_observation(*, attempt: HostedHarnessAttempt, body: dict) -> dict:
    if not isinstance(body, dict) or set(body) != {
        "schema",
        "protocol",
        "execution_fingerprint",
        "case_id",
        "variant",
        "repeat_index",
        "application",
        "business",
        "observation_digest",
    }:
        raise _error("invalid_receipt", "Use the exact native observation.", 400)
    if len(canonical_json(body).encode()) > MAX_OBSERVATION_BYTES:
        raise _error("payload_too_large", "Native observation is too large.", 413)
    if (
        not isinstance(body["application"], dict)
        or not isinstance(body["business"], dict)
        or body["observation_digest"] != _digest(body["application"])
    ):
        raise _error("invalid_receipt", "Observation digest mismatch.", 422)
    with transaction.atomic():
        attempt = _locked_attempt(attempt.pk)
        job = HostedHarnessJob.no_workspace_objects.select_for_update().get(
            pk=attempt.job_id
        )
        spec = _spec(job)
        control = dict(job.payload["skills_native_control"])
        control["_roots"] = {
            variant: {
                item["path"][: -len("SKILL.md")]: item["content"]
                for item in (snapshot or {}).get("files", [])
                if item["path"].endswith("/SKILL.md")
            }
            for variant, snapshot in spec.snapshots.items()
        }
        cases = {case["id"]: case for case in _control_cases(job)}
        case = cases.get(body["case_id"])
        if case is None or body["variant"] not in spec.snapshots:
            raise _error("invalid_receipt", "Unknown native cell.", 409)
        arm = control["arms"][body["variant"]]
        application = body["application"]
        if application.get("status") == "completed" and (
            application.get("context_digest") != arm["digest"]
            or application.get("version_id") != arm["version_id"]
        ):
            raise _error(
                "evidence_mismatch",
                "The application delivered different context bytes.",
                409,
            )
        graded = grade_observation(
            control=control, case=case, variant=body["variant"], body=body
        )
        receipt = {
            "schema": 1,
            "protocol": PROTOCOL,
            "attempt_id": str(attempt.pk),
            "claim_generation": attempt.attempt_number,
            "execution_fingerprint": body["execution_fingerprint"],
            "case_id": body["case_id"],
            "variant": body["variant"],
            "repeat_index": body["repeat_index"],
            "status": graded["status"],
            "checks": [
                {"check_id": c.check_id, "family": c.family, "verdict": c.verdict}
                for c in graded["checks"]
            ],
        }
        validate_native_result_receipt(
            spec=spec,
            attempt_id=attempt.pk,
            generation=attempt.attempt_number,
            required_checks=control["required_checks"],
            receipt=receipt,
        )
        scenario = HostedHarnessScenario.no_workspace_objects.get(
            job=job, scenario_key=_cell_key(body)
        )
        stored = {
            "receipt": receipt,
            "summary": graded["summary"],
            "observation_digest": body["observation_digest"],
            "events": [
                {"kind": event.get("kind"), "body": event.get("body")}
                for event in (application.get("events") or [])[:200]
            ],
            "business": body["business"],
        }
        row, created = HostedHarnessReceipt.no_workspace_objects.get_or_create(
            job=job,
            scenario=scenario,
            execution=None,
            defaults={
                "attempt": attempt,
                "attempt_number": attempt.attempt_number,
                "digest": _digest(stored),
                "status": graded["status"],
                "body": stored,
            },
        )
        if not created and row.digest != _digest(stored):
            raise _error(
                "idempotency_conflict", "This cell already has a different result."
            )
        received = HostedHarnessReceipt.no_workspace_objects.filter(job=job).count()
        job.completed_count = received
        job.save(update_fields=["completed_count"])
        attempt.heartbeat_at = timezone.now()
        attempt.save(update_fields=["heartbeat_at"])
    if received == len(spec.matrix):
        _finish(job.pk, attempt.pk, failure=None)
    return {"accepted": True, "received": received, "planned": len(spec.matrix)}


def report_guest_failure(*, attempt: HostedHarnessAttempt, reason: object) -> dict:
    text = str(reason)
    code = text if re.fullmatch(r"[a-z0-9_]{1,64}", text) else "guest_failed"
    _finish(attempt.job_id, attempt.pk, failure=code)
    return {"accepted": True}


def _control_cases(job: HostedHarnessJob) -> list[dict]:
    # Frozen reviewed cases (checks/expectations) stay evaluator-side only.
    cases: list[dict] = job.payload["skills_native_control"]["cases"]
    return cases


def _finish(job_id: UUID, attempt_id: UUID, *, failure: str | None) -> None:
    with transaction.atomic():
        job = HostedHarnessJob.no_workspace_objects.select_for_update().get(pk=job_id)
        if job.state in {
            HostedHarnessJob.State.COMPLETED,
            HostedHarnessJob.State.FAILED,
            HostedHarnessJob.State.CANCELED,
        }:
            return
        attempt = HostedHarnessAttempt.no_workspace_objects.select_for_update().get(
            pk=attempt_id
        )
        now = timezone.now()
        job.state = (
            HostedHarnessJob.State.FAILED
            if failure
            else HostedHarnessJob.State.COMPLETED
        )
        job.current_stage = "failed" if failure else "completed"
        job.terminal_at = now
        job.failure = {"code": failure} if failure else None
        job.save(update_fields=["state", "current_stage", "terminal_at", "failure"])
        attempt.state = (
            HostedHarnessAttempt.State.FAILED
            if failure
            else HostedHarnessAttempt.State.COMPLETED
        )
        attempt.terminal_reason = failure
        attempt.save(update_fields=["state", "terminal_reason"])
        control = job.payload["skills_native_control"]
        arms = control["arms"].values()
        HubSkillContextCredential.objects.filter(
            pk__in=[arm["credential_id"] for arm in arms], revoked_at__isnull=True
        ).update(revoked_at=now)
        HubSkillContextBinding.objects.filter(
            pk__in=[arm["binding_id"] for arm in arms], retired_at__isnull=True
        ).update(retired_at=now)
        spend_policy.close_admission(
            scope=_scope(job), admission_id=UUID(control["reservation_id"])
        )


def _scope(job: HostedHarnessJob):
    from model_hub.services.skills_hub.v11.types import SkillScope

    if job.workspace_id is None:
        # Native Skills jobs are only ever created inside a workspace scope.
        raise _error("resource_not_found", "The native run is unavailable.", 404)
    return SkillScope(
        organization_id=job.organization_id, workspace_id=job.workspace_id
    )


# --- Reader ------------------------------------------------------------------


def _reduce(job: HostedHarnessJob) -> tuple[dict, dict[str, HostedHarnessReceipt]]:
    control = job.payload["skills_native_control"]
    spec = _spec(job)
    receipts = {
        row.body["receipt"]["case_id"]
        + "/"
        + row.body["receipt"]["variant"]
        + "/"
        + str(row.body["receipt"]["repeat_index"]): row
        for row in HostedHarnessReceipt.no_workspace_objects.filter(job=job)
        if isinstance(row.body, dict) and "receipt" in row.body
    }
    trials = [
        NativeTrial(
            row.body["receipt"]["case_id"],
            row.body["receipt"]["variant"],
            row.body["receipt"]["repeat_index"],
            row.body["receipt"]["status"],
            tuple(NativeCheck(**check) for check in row.body["receipt"]["checks"]),
        )
        for row in receipts.values()
    ]
    reproduction = dict.fromkeys(control["historical"], "tools")
    reduced = reduce_native_results(
        matrix=spec.matrix,
        trials=trials,
        required_checks=control["required_checks"],
        reproduction_checks=reproduction or None,
    )
    return reduced, receipts


def _decision(job: HostedHarnessJob, reduced: dict) -> dict:
    if job.state != HostedHarnessJob.State.COMPLETED:
        return {
            "state": "pending" if not job.terminal_at else "hold",
            "reasons": (
                ["Native work is not complete."]
                if not job.terminal_at
                else ["Native work failed; no outcome is inferred."]
            ),
        }
    reasons = []
    cells = [cell for cell in reduced["cells"] if cell["variant"] == "candidate"]
    for family, label in (
        ("tools", "business tool outcome"),
        ("selection", "skill selection"),
        ("delivery", "file delivery"),
        ("answer", "answer"),
    ):
        failed = [cell for cell in cells if cell[family] == "fail"]
        if failed:
            reasons.append(
                f"Candidate {label} failed in {len(failed)} of {len(cells)} cells."
            )
    text_only = [
        cell for cell in cells if cell["answer"] == "pass" and cell["tools"] == "fail"
    ]
    if text_only:
        reasons.append(
            "The answer text passes while the application's business tool fails; a text pass is not a repair."
        )
    if reduced["reproduction"] == "not_reproduced":
        reasons.append("The recorded historical failure was not reproduced.")
    if reduced["candidate_incomplete"]:
        reasons.append("Some candidate cells are incomplete or unverifiable.")
    ready = (
        not reasons
        and reduced["state"] == "works_on_examples"
        and reduced["reproduction"] in {"reproduced", "not_requested"}
    )
    return {"state": "release_ready" if ready else "hold", "reasons": reasons}


def _evidence_output(evidence: HubSkillNativeEvidence, *, detail: bool) -> dict:
    job = evidence.simulation_job
    control = job.payload.get("skills_native_control") or {}
    output = {
        "id": str(evidence.pk),
        "job_id": str(job.pk),
        "status": job.state,
        "failure": (job.failure or {}).get("code"),
        "created_at": evidence.created_at,
        "visibility": evidence.visibility,
        "candidate_digest": evidence.candidate_digest,
        "execution_fingerprint": evidence.execution_fingerprint,
        "configuration_digest": evidence.configuration["digest"],
        "annotation_revision": evidence.annotation_revision,
        "application_binding_id": control.get("application_binding_id"),
        "arms": [
            {
                "variant": variant,
                **{k: arm[k] for k in ("version", "version_id", "digest")},
            }
            for variant, arm in (control.get("arms") or {}).items()
        ],
        "planned": job.scenario_count,
        "received": job.completed_count,
    }
    if not detail or not job.payload.get("skills_context"):
        return output
    reduced, receipts = _reduce(job)
    cases = {case["id"]: case for case in control["cases"]}
    attempt = job.attempts.order_by("-attempt_number").first()
    output.update(
        {
            "result": {
                key: reduced[key]
                for key in (
                    "state",
                    "counts",
                    "candidate_failures",
                    "candidate_incomplete",
                    "reproduction",
                    "repair_verified",
                    "regressed_cases",
                )
            },
            "decision": _decision(job, reduced),
            "runtime": (
                (attempt.sandbox_runtime or {}).get("health") if attempt else None
            ),
            "cases": [
                {
                    "id": case_id,
                    "title": cases[case_id]["title"],
                    "historical": case_id in control["historical"],
                }
                for case_id in dict.fromkeys(
                    cell["case_id"] for cell in reduced["cells"]
                )
            ],
            "cells": [
                {
                    **{
                        key: cell[key]
                        for key in (
                            "case_id",
                            "variant",
                            "repeat_index",
                            "status",
                            "answer",
                            "delivery",
                            "selection",
                            "tools",
                            "failure",
                            "incomplete",
                        )
                    },
                    "attempt_id": (
                        str(receipts[_cell_key(cell)].pk)
                        if _cell_key(cell) in receipts
                        else None
                    ),
                    "observed": (
                        receipts[_cell_key(cell)].body["summary"]
                        if _cell_key(cell) in receipts
                        else None
                    ),
                }
                for cell in reduced["cells"]
            ],
        }
    )
    return output


def list_native_runs(*, principal: SkillPrincipal, context_id: UUID) -> dict:
    from model_hub.services.skills_hub.r3.native_evidence import visible_native_evidence

    rows = (
        visible_native_evidence(
            principal=principal, resource_id=context_id, kind="agent_context"
        )
        .select_related("simulation_job")
        .order_by("-created_at")[:25]
    )
    return {"results": [_evidence_output(row, detail=False) for row in rows]}


def get_native_run(
    *, principal: SkillPrincipal, context_id: UUID, evidence_id: UUID
) -> dict:
    evidence = get_native_evidence(
        principal=principal,
        evidence_id=evidence_id,
        resource_id=context_id,
        kind="agent_context",
        payload=True,
    )
    return _evidence_output(evidence, detail=True)


def native_attempt_observation(
    *,
    principal: SkillPrincipal | None,
    evidence: HubSkillNativeEvidence,
    attempt_id: UUID,
) -> dict | None:
    """Qualified reader for annotation validation: one stored cell result."""
    row = HostedHarnessReceipt.no_workspace_objects.filter(
        job=evidence.simulation_job, pk=attempt_id
    ).first()
    return None if row is None else row.body
