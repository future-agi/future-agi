from __future__ import annotations

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


class CredentialCheckFailed(Exception):
    def __init__(self, checks: list[dict[str, Any]]) -> None:
        super().__init__("a changed key was rejected")
        self.checks = checks


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
        payload = dict(locked.payload or {})
        saved_agent = dict(payload.get("agent") or {})
        refs = dict(saved_agent.get("secret_refs") or {})
        refs.update(store_secret_values(locked.organization, environment_values))
        refs.update(credential_files)
        saved_agent["secret_refs"] = refs
        saved_agent["config"] = {
            **(saved_agent.get("config") or {}),
            **config_update,
        }
        payload["agent"] = saved_agent
        metadata = dict(payload.get("metadata") or {})
        metadata["config_changes"] = [
            *(metadata.get("config_changes") or []),
            {
                "at": now.isoformat(),
                "user_id": str(user.id) if getattr(user, "id", None) else None,
                "secrets": sorted(environment_values),
                "config": sorted(config_update),
                "credential_files": sorted(credential_files),
            },
        ]
        payload["metadata"] = metadata
        locked.payload = payload
        locked.content_updated_at = now
        locked.save(update_fields=["payload", "content_updated_at", "updated_at"])
    return locked, checks


def _require_editable(job: HostedHarnessJob) -> None:
    from simulate.services.harness_provider import get_harness_provider

    if get_harness_provider().name != "hosted":
        raise HostedHarnessError(
            "configuration_edit_unsupported",
            "this environment's keys cannot be changed on this deployment",
            status_code=409,
        )
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
