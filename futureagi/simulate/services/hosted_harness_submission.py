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

from typing import Any


def submit_hosted_harness_job(
    organization,
    workspace,
    payload: dict[str, Any],
    idempotency_key: str,
    public_base_url: str,
):
    from simulate.services.harness_provider import (
        _validate_known_hosted_egress,
        _validate_phone_connectivity,
        _validate_required_credential_files,
        _validate_secret_refs_hosted,
    )
    from simulate.services.harness_usage import (
        require_harness_run_usage,
        require_harness_source_call_usage,
    )
    from simulate.services.hosted_harness import (
        HostedHarnessError,
        create_hosted_job,
    )
    from simulate.temporal.client import start_hosted_harness_gateway_workflow

    # This uses only references/configuration and deployment env presence.
    # Definitive launch validation runs again after vault resolution.
    _validate_secret_refs_hosted(payload["agent"]["secret_refs"])
    _validate_phone_connectivity(payload)
    _validate_known_hosted_egress(payload, public_base_url)

    require_harness_run_usage(str(organization.id), payload)
    source_analysis = _validate_required_credential_files(organization, payload)
    require_harness_source_call_usage(
        str(organization.id),
        payload,
        source_analysis[0] if source_analysis else (),
    )

    try:
        job, created = create_hosted_job(
            organization,
            payload,
            idempotency_key=idempotency_key,
            workspace=workspace,
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
        raise HostedHarnessError(
            "scheduler_unavailable",
            "Hosted harness scheduler is unavailable",
            status_code=503,
            retryable=True,
        ) from exc
    return job, created
