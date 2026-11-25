"""Shared request-independent HostedHarnessJob submission path.

Both the public harness-job API and production certification submit jobs
through ``submit_hosted_harness_job`` so admission checks, usage enforcement,
idempotent persistence, and gateway scheduling stay identical regardless of
the caller.

Contract: validations raise ``HostedHarnessError``; usage refusals raise the
metering exception untouched (the HTTP layer translates them); an unexpected
failure while persisting or scheduling becomes a typed 503
``scheduler_unavailable`` error. Returns ``(job, created)``.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def submit_hosted_harness_job(
    organization,
    workspace,
    payload: dict[str, Any],
    idempotency_key: str,
    public_base_url: str,
    *,
    user=None,
    secret_values: dict[str, str] | None = None,
):
    from django.core.exceptions import ValidationError
    from django.db import transaction

    from simulate.serializers.harness_job import names_agent_version
    from simulate.services.canonical_json import canonical_digest
    from simulate.services.harness_agent_versions import advisory_xact_lock
    from simulate.services.harness_provider import (
        _connected_build_runtime,
        _connected_build_source,
        _environment_agent_maker,
        _environment_build_runtime,
        _secret_ref_minter,
        _validate_github_installation,
        _validate_known_hosted_egress,
        _validate_phone_connectivity,
        _validate_required_credential_files,
        _validate_secret_refs_hosted,
    )
    from simulate.services.harness_usage import (
        require_harness_run_usage,
        require_harness_source_call_usage,
    )
    from simulate.services.harness_versioning.versions import versioning_enabled
    from simulate.services.hosted_harness import (
        HostedHarnessError,
        create_hosted_job,
        find_replayed_job,
        idempotency_lock_name,
    )
    from simulate.temporal.client import start_hosted_harness_gateway_workflow

    secret_values = secret_values or {}
    client_digest = canonical_digest(payload)
    # This uses only references/configuration and deployment env presence.
    # Definitive launch validation runs again after vault resolution.
    version_bound = False
    try:
        connected_runtime = _connected_build_runtime(
            organization.id, getattr(workspace, "id", None), payload
        )
        if connected_runtime is not None:
            payload["source"] = _connected_build_source(connected_runtime, payload)
        else:
            connected_runtime = _environment_build_runtime(
                organization.id, getattr(workspace, "id", None), payload
            )
            if connected_runtime is not None:
                from simulate.services.harness_agent_versions import (
                    apply_agent_version,
                )

                apply_agent_version(payload, connected_runtime)
                version_bound = True
    except (ValueError, ValidationError):
        connected_runtime = None
        version_bound = False
    connected_build = connected_runtime is not None
    if (
        not connected_build
        and (payload.get("source") or {}).get("kind") == "provider"
        and names_agent_version(payload["agent"], payload.get("metadata"))
    ):
        raise HostedHarnessError(
            "version_not_buildable",
            "This agent version can't be built from here",
            status_code=409,
        )
    available_names: list[str] = []
    if connected_build:
        from simulate.services.harness_agent_versions import version_credential_names

        available_names = [*version_credential_names(connected_runtime), *secret_values]

    _validate_secret_refs_hosted(payload["agent"]["secret_refs"])
    _validate_phone_connectivity(payload)
    _validate_known_hosted_egress(payload, public_base_url)
    replayed = find_replayed_job(
        organization,
        payload,
        idempotency_key=idempotency_key,
        workspace=workspace,
        request_digest=client_digest,
    )
    if replayed is None and versioning_enabled() and not connected_build:
        from simulate.services.harness_agent_changes import (
            validate_github_source,
        )

        validate_github_source(payload, lookup=False)
    if replayed is None:
        _validate_github_installation(
            organization.id, getattr(workspace, "id", None), payload
        )

    require_harness_run_usage(str(organization.id), payload)
    source_analysis = _validate_required_credential_files(
        organization,
        payload,
        getattr(workspace, "id", None),
        available_names=available_names,
    )
    require_harness_source_call_usage(
        str(organization.id),
        payload,
        source_analysis[0] if source_analysis else (),
    )

    try:
        with transaction.atomic():
            advisory_xact_lock(idempotency_lock_name(organization, idempotency_key))
            replayed = find_replayed_job(
                organization,
                payload,
                idempotency_key=idempotency_key,
                workspace=workspace,
                request_digest=client_digest,
            )
            on_create = None
            if replayed is None and versioning_enabled():
                from simulate.services.harness_agent_changes import (
                    validate_github_source,
                )

                repository_id = (
                    None
                    if connected_build
                    else validate_github_source(
                        payload,
                        organization_id=organization.id,
                        workspace_id=getattr(workspace, "id", None),
                    )
                )
                on_create = _environment_agent_maker(
                    organization,
                    payload,
                    secret_values,
                    source_analysis,
                    user,
                    repository_id,
                    workspace=workspace,
                    version_bound=version_bound,
                )
            elif replayed is None and secret_values:
                on_create = _secret_ref_minter(secret_values)
            job, created = create_hosted_job(
                organization,
                payload,
                idempotency_key=idempotency_key,
                workspace=workspace,
                on_create=on_create,
                request_digest=client_digest,
            )
        retry_cfg = job.payload["retry"]
        start_hosted_harness_gateway_workflow(
            str(job.id),
            public_base_url,
            retry_cfg["max_infrastructure_attempts"],
            retry_cfg["initial_backoff_seconds"],
            retry_cfg["max_backoff_seconds"],
        )
    except HostedHarnessError:
        raise
    except Exception as exc:
        logger.warning(
            "harness_create_failed org=%s error=%s",
            organization.id,
            type(exc).__name__,
        )
        raise HostedHarnessError(
            "scheduler_unavailable",
            "Hosted harness scheduler is unavailable",
            status_code=503,
            retryable=True,
        ) from exc
    return job, created
