from __future__ import annotations

import copy
import re
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from simulate.models import HostedHarnessJob, HostedHarnessSecret
from simulate.serializers.harness_job import HarnessAgentSerializer, phone_number_error
from simulate.services.harness_credential_probes import (
    PROVIDER_PROBES,
    probe,
    probe_provider_target,
)
from simulate.services.harness_credentials import store_secret_values
from simulate.services.hosted_harness import HostedHarnessError

EDITABLE_CONFIG_FIELDS = (
    "phone_number",
    "livekit_url",
    "dynamic_variables",
    "target_speaks_first",
)
REPLACE_ONLY_CONFIG_FIELDS = ("phone_number", "livekit_url")
CREDENTIAL_FILE_ALIASES = ("GOOGLE_APPLICATION_CREDENTIALS_JSON",)
_TARGET_KEY_ALIAS = {
    "vapi": "VAPI_API_KEY",
    "retell": "RETELL_API_KEY",
    "retell_chat": "RETELL_API_KEY",
}
_TARGET_PROBE_MODES = {"connect_only", "provider_import"}
_CREDENTIAL_CHOICES = {
    "google_model_auth": (
        ("GEMINI_API_KEY",),
        ("GOOGLE_API_KEY",),
        ("GOOGLE_APPLICATION_CREDENTIALS_JSON", "GOOGLE_CLOUD_PROJECT"),
    ),
}
_MISSING_INPUT = re.compile(
    r"target_runtime_configuration_missing:\s*"
    r"((?:(?:environment|credential_choice):[A-Za-z0-9_]+(?:,\s*)?)+)"
)
_PENDING_REF = {"manager": "platform-vault", "key": "", "purpose": "target_provider"}


class CredentialCheckFailed(Exception):
    def __init__(self, checks: list[dict[str, Any]]) -> None:
        super().__init__("a changed key was rejected")
        self.checks = checks


class PreflightFailed(Exception):
    def __init__(self, checks: list[dict[str, Any]]) -> None:
        super().__init__("the rebuild preflight failed")
        self.checks = checks


def input_needed(failure: Any) -> dict[str, Any] | None:
    if not isinstance(failure, dict):
        return None
    match = _MISSING_INPUT.search(str(failure.get("message") or ""))
    if match is None:
        return None
    keys: list[str] = []
    choices: list[dict[str, Any]] = []
    for item in match.group(1).split(","):
        kind, _, name = item.strip().partition(":")
        if kind == "environment":
            keys.append(name)
        elif kind == "credential_choice" and name in _CREDENTIAL_CHOICES:
            choices.append(
                {
                    "id": name,
                    "options": [list(option) for option in _CREDENTIAL_CHOICES[name]],
                }
            )
    offered = {
        alias for choice in choices for option in choice["options"] for alias in option
    }
    keys = [key for key in dict.fromkeys(keys) if key not in offered]
    if not keys and not choices:
        return None
    return {"keys": keys, "choices": choices}


def failure_with_input_needed(failure: Any) -> Any:
    needed = input_needed(failure)
    return {**failure, "input_needed": needed} if needed else failure


def update_environment_configuration(
    job: HostedHarnessJob,
    *,
    environment_values: dict[str, str],
    config: dict[str, Any],
    credential_files: dict[str, dict[str, str]],
    user,
    callback_url: str,
) -> tuple[HostedHarnessJob, list[dict[str, Any]]]:
    from simulate.services.harness_provider import _validate_known_hosted_egress

    _require_editable(job)
    agent = (job.payload or {}).get("agent") or {}
    stored_config = agent.get("config") or {}
    config_update = _config_update(stored_config, config)
    merged_config = {**stored_config, **config_update}
    _validate_files(job, credential_files)

    if environment_values or "livekit_url" in config:
        candidate = dict(job.payload)
        candidate["agent"] = {
            **agent,
            "config": merged_config,
            "secret_refs": {
                **(agent.get("secret_refs") or {}),
                **{alias: {} for alias in environment_values},
            },
        }
        _validate_known_hosted_egress(candidate, callback_url)

    checks = _credential_checks(
        job, agent, merged_config, environment_values, credential_files, config
    )
    if any(check["status"] == "rejected" for check in checks):
        raise CredentialCheckFailed(checks)

    now = timezone.now()
    with transaction.atomic():
        locked = HostedHarnessJob.no_workspace_objects.select_for_update().get(
            id=job.id
        )
        _require_editable(locked)
        locked.payload = _edited_payload(
            locked,
            environment_values=environment_values,
            config_update=config_update,
            credential_files=credential_files,
            change={
                "at": now.isoformat(),
                "user_id": str(user.id) if getattr(user, "id", None) else None,
            },
        )
        locked.content_updated_at = now
        locked.save(update_fields=["payload", "content_updated_at", "updated_at"])
    return locked, checks


def rebuild_failed_environment(
    job: HostedHarnessJob,
    *,
    request,
    environment_values: dict[str, str],
    config: dict[str, Any],
    credential_files: dict[str, dict[str, str]],
    callback_url: str,
) -> tuple[HostedHarnessJob, list[dict[str, Any]]]:
    from simulate.services.harness_provider import (
        _validate_known_hosted_egress,
        _validate_phone_connectivity,
        hosted_preflight_checks,
    )
    from simulate.temporal.client import start_hosted_harness_gateway_workflow

    _require_rebuildable(job)
    agent = (job.payload or {}).get("agent") or {}
    stored_config = agent.get("config") or {}
    stored_refs = agent.get("secret_refs") or {}
    config_update = _config_update(stored_config, config)
    _validate_files(job, credential_files)

    candidate = copy.deepcopy(job.payload)
    candidate["agent"] = {
        **agent,
        "config": {**stored_config, **config_update},
        "secret_refs": {
            **stored_refs,
            **{alias: dict(_PENDING_REF) for alias in environment_values},
            **credential_files,
        },
    }
    _validate_phone_connectivity(candidate)
    _validate_known_hosted_egress(candidate, callback_url)
    unchanged = (
        set(stored_refs) - set(environment_values) - set(CREDENTIAL_FILE_ALIASES)
    )
    candidate["credential_values"] = {
        **_stored_values(job, agent, unchanged),
        **environment_values,
    }
    checks, _credentials, _source_error = hosted_preflight_checks(request, candidate)
    if any(check["status"] == "failed" for check in checks):
        raise PreflightFailed(checks)

    now = timezone.now()
    user = request.user
    with transaction.atomic():
        locked = HostedHarnessJob.no_workspace_objects.select_for_update().get(
            id=job.id
        )
        _require_rebuildable(locked)
        payload = _edited_payload(
            locked,
            environment_values=environment_values,
            config_update=config_update,
            credential_files=credential_files,
            change={
                "at": now.isoformat(),
                "user_id": str(user.id) if getattr(user, "id", None) else None,
                "rebuild": True,
            },
        )
        payload["metadata"]["attempt_cycle_start"] = locked.current_attempt_number + 1
        locked.payload = payload
        locked.state = HostedHarnessJob.State.QUEUED
        locked.current_stage = "queued"
        locked.failure = None
        locked.terminal_at = None
        locked.deadline_at = now + timedelta(
            seconds=payload["runtime"]["max_duration_seconds"]
        )
        locked.content_updated_at = now
        locked.save(
            update_fields=[
                "payload",
                "state",
                "current_stage",
                "failure",
                "terminal_at",
                "deadline_at",
                "content_updated_at",
                "updated_at",
            ]
        )

    retry = locked.payload["retry"]
    try:
        start_hosted_harness_gateway_workflow(
            str(locked.id),
            callback_url,
            retry["max_infrastructure_attempts"],
            retry["initial_backoff_seconds"],
            retry["max_backoff_seconds"],
        )
    except Exception as exc:
        HostedHarnessJob.no_workspace_objects.filter(id=locked.id).update(
            state=HostedHarnessJob.State.FAILED,
            current_stage="failed",
            terminal_at=timezone.now(),
            failure={
                "domain": "infrastructure",
                "stage": "queued",
                "code": "scheduler_unavailable",
                "message": "The rebuild could not be scheduled. Try again.",
            },
        )
        raise HostedHarnessError(
            "scheduler_unavailable",
            "the rebuild could not be scheduled; try again",
            status_code=503,
        ) from exc
    locked.refresh_from_db()
    return locked, checks


def _edited_payload(
    job: HostedHarnessJob,
    *,
    environment_values: dict[str, str],
    config_update: dict[str, Any],
    credential_files: dict[str, dict[str, str]],
    change: dict[str, Any],
) -> dict[str, Any]:
    payload = dict(job.payload or {})
    saved_agent = dict(payload.get("agent") or {})
    refs = dict(saved_agent.get("secret_refs") or {})
    refs.update(store_secret_values(job.organization, environment_values))
    refs.update(credential_files)
    saved_agent["secret_refs"] = refs
    saved_agent["config"] = {**(saved_agent.get("config") or {}), **config_update}
    payload["agent"] = saved_agent
    metadata = dict(payload.get("metadata") or {})
    metadata["config_changes"] = [
        *(metadata.get("config_changes") or []),
        {
            **change,
            "secrets": sorted(environment_values),
            "config": sorted(config_update),
            "credential_files": sorted(credential_files),
        },
    ]
    payload["metadata"] = metadata
    return payload


def _require_hosted() -> None:
    from simulate.services.harness_provider import get_harness_provider

    if get_harness_provider().name != "hosted":
        raise HostedHarnessError(
            "configuration_edit_unsupported",
            "this environment's keys cannot be changed on this deployment",
            status_code=409,
        )


def _require_rebuildable(job: HostedHarnessJob) -> None:
    _require_hosted()
    if (
        job.state != HostedHarnessJob.State.FAILED
        or job.environment_id is not None
        or job.test_execution_id is not None
    ):
        raise HostedHarnessError(
            "environment_not_failed",
            "only an environment whose build failed can be rebuilt",
            status_code=409,
        )


def _require_editable(job: HostedHarnessJob) -> None:
    _require_hosted()
    if job.state != HostedHarnessJob.State.COMPLETED:
        raise HostedHarnessError(
            "environment_not_completed",
            "only a built environment's keys and connection settings can be changed",
            status_code=409,
        )


def _config_update(stored: dict[str, Any], requested: dict[str, Any]) -> dict[str, Any]:
    update = {}
    for field, value in requested.items():
        key = field
        if field == "livekit_url" and "livekit_url" not in stored:
            key = "LIVEKIT_URL" if "LIVEKIT_URL" in stored else field
        if (
            field in REPLACE_ONLY_CONFIG_FIELDS
            and not str(stored.get(key) or "").strip()
        ):
            raise HostedHarnessError(
                "configuration_needs_rebuild",
                f"this environment has no {field}; adding one needs a rebuild",
                status_code=400,
            )
        if field == "phone_number" and (error := phone_number_error(value)):
            raise HostedHarnessError("configuration_invalid", error, status_code=400)
        if field == "livekit_url":
            parsed = urlparse(str(value).strip())
            if parsed.scheme not in {"ws", "wss", "http", "https"} or not parsed.netloc:
                raise HostedHarnessError(
                    "configuration_invalid",
                    "livekit_url must be a ws, wss, http or https URL",
                    status_code=400,
                )
        update[key] = value
    try:
        HarnessAgentSerializer().validate_config({**stored, **update})
    except serializers.ValidationError as exc:
        detail = exc.detail if isinstance(exc.detail, list) else [exc.detail]
        raise HostedHarnessError(
            "configuration_invalid",
            "; ".join(str(item) for item in detail),
            status_code=400,
        ) from exc
    return update


def _validate_files(job: HostedHarnessJob, credential_files: dict[str, dict]) -> None:
    from simulate.services.harness_credentials import is_credential_file_ref

    for alias, ref in credential_files.items():
        found = HostedHarnessSecret.no_workspace_objects.filter(
            organization=job.organization, name=ref.get("key")
        ).exists()
        if not is_credential_file_ref(ref) or not found:
            raise HostedHarnessError(
                "credential_file_not_found",
                f"{alias} does not point at an uploaded credential file",
                status_code=400,
            )


def _credential_checks(
    job: HostedHarnessJob,
    agent: dict[str, Any],
    merged_config: dict[str, Any],
    environment_values: dict[str, str],
    credential_files: dict[str, dict],
    requested_config: dict[str, Any],
) -> list[dict[str, Any]]:
    touched = set(environment_values)
    if "livekit_url" in requested_config:
        touched.add("LIVEKIT_URL")
    specs = {
        name: spec
        for name, spec in PROVIDER_PROBES.items()
        if touched & set(spec.aliases)
    }
    connector = str(agent.get("connector") or "").strip().lower()
    mode = str(agent.get("mode") or "").strip().lower()
    target_alias = _TARGET_KEY_ALIAS.get(connector)
    check_target = target_alias in touched and mode in _TARGET_PROBE_MODES
    needed = {alias for spec in specs.values() for alias in spec.aliases}
    if check_target:
        needed.add(target_alias)
    values = {
        **_stored_values(job, agent, needed - set(environment_values)),
        **environment_values,
    }
    livekit_url = merged_config.get("livekit_url") or merged_config.get("LIVEKIT_URL")
    if livekit_url and not values.get("LIVEKIT_URL"):
        values["LIVEKIT_URL"] = str(livekit_url)

    results = [
        probe(name, values) for name, spec in specs.items() if spec.applies(values)
    ]
    if check_target:
        target_key = "assistant_id" if connector == "vapi" else "agent_id"
        target = probe_provider_target(
            connector, str(merged_config.get(target_key) or ""), values
        )
        if target is not None:
            results.append(target)

    checks = [
        {
            "aliases": list(result.aliases),
            "label": result.label,
            "status": "accepted" if result.ok else "rejected",
            "message": result.message,
        }
        for result in results
    ]
    covered = {alias for result in results for alias in result.aliases}
    for alias in sorted((touched - covered - {"LIVEKIT_URL"}) | set(credential_files)):
        checks.append(
            {
                "aliases": [alias],
                "label": alias,
                "status": "not_checked",
                "message": f"{alias} was saved without a live check; please verify this value",
            }
        )
    return checks


def _stored_values(
    job: HostedHarnessJob, agent: dict[str, Any], aliases: set[str]
) -> dict[str, str]:
    from simulate.services.hosted_harness_gateway import PlatformSecretResolver

    refs = agent.get("secret_refs") or {}
    wanted = {alias: refs[alias] for alias in aliases if alias in refs}
    if not wanted:
        return {}
    probe_job = HostedHarnessJob(
        organization=job.organization, payload={"agent": {"secret_refs": wanted}}
    )
    try:
        return PlatformSecretResolver().resolve(probe_job)
    except HostedHarnessError:
        return {}
