"""Execution-backend switch for the hosted ALK harness control plane.

`HarnessJobViewSet` is provider-neutral: it validates the v1.6 request contract
(`futureagi.harness-job.v1`) and delegates to the provider selected by
``settings.HARNESS_PROVIDER``:

- ``daytona`` (default): the platform *is* the gateway. It persists the job,
  starts ``HostedHarnessGatewayWorkflow`` and drives the Daytona sandbox
  (matches the hosted-execution seams contract v1.6 — "the gateway drives the
  Daytona API from outside; no network runtime provider exists").
- ``sandbox``: the platform is a thin proxy to an out-of-process ALK sandbox
  server (dev: ALK's local-process provider; prod: a managed sandbox service).
  The v1.6 request is mapped to the sandbox server's flat contract and
  forwarded over HTTP; no viewset or UI change is required to switch.

Both providers accept the *same* validated v1.6 payload, so switching backends
never changes the platform's public request schema.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from rest_framework import status
from rest_framework.response import Response

from simulate.models import (
    HostedHarnessJob,
    HostedHarnessReceipt,
    HostedHarnessScenario,
    HostedHarnessStageOutput,
)


def get_harness_provider():
    """Return the configured harness execution provider (default: daytona)."""
    name = str(getattr(settings, "HARNESS_PROVIDER", "daytona") or "daytona").lower()
    if name == "sandbox":
        return SandboxHarnessProvider()
    return DaytonaHarnessProvider()


def _organization(request):
    return getattr(request, "organization", None) or getattr(
        request.user, "organization", None
    )


def _workspace(request):
    workspace = getattr(request, "workspace", None)
    if workspace is not None:
        return workspace

    # Session-authenticated UI requests do not always pass through the API-key
    # authentication hook that materializes ``request.workspace``. Resolve the
    # explicit workspace header here as a safe fallback, while preserving the
    # same organization and membership checks as the authentication layer.
    workspace_id = request.headers.get("X-Workspace-Id")
    if not workspace_id:
        return None

    from rest_framework.exceptions import PermissionDenied

    from accounts.models.workspace import Workspace

    organization = _organization(request)
    workspace = (
        Workspace.no_workspace_objects.select_related("organization")
        .filter(id=workspace_id, organization=organization, is_active=True)
        .first()
    )
    if workspace is None or not request.user.can_access_workspace(workspace):
        raise PermissionDenied("Access denied to this workspace")
    return workspace


def _scope_jobs(queryset, request):
    """Apply the exact tenant scope captured by the submitting request."""
    workspace = _workspace(request)
    if workspace is None:
        return queryset.filter(workspace__isnull=True)
    return queryset.filter(workspace=workspace)


def _validate_secret_refs_daytona(secret_refs: dict) -> None:
    """Reject secret_refs the Daytona resolver cannot materialize.

    platform-vault target_provider refs keep working.  Any other manager
    (including harness_environment_file) returns a typed error rather than
    silently accepting something the resolver will fail on inside the sandbox.
    """
    from simulate.services.hosted_harness import HostedHarnessError

    for alias, ref in secret_refs.items():
        manager = ref.get("manager", "")
        if manager != "platform-vault":
            raise HostedHarnessError(
                "secret_manager_unsupported",
                f"secret manager {manager!r} for alias {alias!r} is not supported "
                f"by the daytona provider; use platform-vault target_provider refs",
                status_code=422,
            )


def serialize_job(job: HostedHarnessJob) -> dict[str, Any]:
    attempt = job.attempts.order_by("-attempt_number").first()
    events: list[dict[str, Any]] = []
    if attempt:
        recent = list(attempt.events.filter(accepted=True).order_by("-sequence")[:100])
        events = [
            {
                "event_id": event.event_id,
                "sequence": event.sequence,
                "stage": event.stage,
                "type": event.event_type,
                "payload": event.payload,
                "emitted_at": event.emitted_at.isoformat(),
            }
            for event in reversed(recent)
        ]
    # Stage outputs — persisted authoritative snapshots from verified bundle.
    stage_outputs_qs = HostedHarnessStageOutput.no_workspace_objects.filter(
        job=job
    ).order_by("created_at")[:20]
    stage_outputs = [
        {
            "id": str(so.id),
            "title": so.title,
            "summary": so.summary,
            "kind": so.kind,
            "data": so.data,
        }
        for so in stage_outputs_qs
    ]
    if not stage_outputs:
        stage_outputs = list(job.stage_outputs or [])
    # Scenarios — bounded to job.scenario_count.
    scenario_regs = list(
        HostedHarnessScenario.no_workspace_objects.filter(job=job)
        .select_related("scenario")
        .order_by("created_at")[: job.scenario_count]
    )
    scenarios = [
        {
            "scenario_key": reg.scenario_key,
            "scenario_id": str(reg.scenario_id),
            "name": getattr(reg.scenario, "name", "") if reg.scenario else "",
            "instruction": (getattr(reg.scenario, "prompt", None) or "")
            if reg.scenario
            else None,
            "use_case": (getattr(reg.scenario, "use_case", None) or "")
            if reg.scenario
            else None,
            "call_execution_id": str(reg.call_execution_id)
            if reg.call_execution_id
            else None,
            "status": _scenario_status(reg),
        }
        for reg in scenario_regs
    ]
    # Receipts — bounded.
    receipt_qs = HostedHarnessReceipt.no_workspace_objects.filter(job=job).order_by(
        "created_at"
    )[: job.scenario_count]
    receipts = [r.body for r in receipt_qs]
    platform = {
        "run_test_id": str(job.run_test_id) if job.run_test_id else None,
        "test_execution_id": str(job.test_execution_id)
        if job.test_execution_id
        else None,
        "url": (
            f"/dashboard/simulate/test/{job.run_test_id}/{job.test_execution_id}/call-details"
            if job.run_test_id and job.test_execution_id
            else None
        ),
    }
    return {
        "job": {
            "job_id": str(job.id),
            "run_id": str(job.run_id),
            "source": job.payload["source"],
            "metadata": job.payload.get("metadata", {}),
            "run_test_id": str(job.run_test_id) if job.run_test_id else None,
            "test_execution_id": str(job.test_execution_id)
            if job.test_execution_id
            else None,
        },
        "status": {
            "state": job.state,
            "stage": job.current_stage,
            "updated_at": job.updated_at.isoformat(),
            "attempt": attempt.attempt_number if attempt else 0,
            "completed_scenarios": job.completed_count,
            "failed_scenarios": job.failed_count,
            "total_scenarios": job.scenario_count,
            "deadline_at": job.deadline_at.isoformat(),
            "failure": job.failure,
        },
        "events": events,
        "stage_outputs": stage_outputs,
        "scenarios": scenarios,
        "receipts": receipts,
        "platform": platform,
    }


def _scenario_status(reg: HostedHarnessScenario) -> str | None:
    """Derive a human-readable status for a scenario registration."""
    if not reg.call_execution_id:
        return "registered"
    receipt = (
        HostedHarnessReceipt.no_workspace_objects.filter(
            job_id=reg.job_id, scenario_id=reg.id
        )
        .values_list("status", flat=True)
        .first()
    )
    return receipt or "running"


class DaytonaHarnessProvider:
    """Platform-as-gateway. Persists the job and drives Daytona via Temporal."""

    name = "daytona"

    def create(self, request) -> Response:
        from simulate.services.hosted_harness import (
            HostedHarnessError,
            create_hosted_job,
        )
        from simulate.services.hosted_harness_gateway import _validate_egress_domains
        from simulate.temporal.client import start_hosted_harness_gateway_workflow

        organization = _organization(request)
        if organization is None:
            return Response(
                {"detail": "Organization not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        idempotency_key = request.headers.get("Idempotency-Key", "").strip()
        if not idempotency_key:
            return Response(
                {"detail": "Idempotency-Key header is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Validate egress and secret refs at admission.
        try:
            _validate_egress_domains(
                request.validated_data["security"]["allowed_egress_domains"]
            )
            _validate_secret_refs_daytona(
                request.validated_data["agent"]["secret_refs"]
            )
        except HostedHarnessError as exc:
            return Response(exc.as_dict(), status=exc.status_code)
        try:
            job, _ = create_hosted_job(
                organization,
                request.validated_data,
                idempotency_key=idempotency_key,
                workspace=_workspace(request),
            )
            base_url = getattr(
                settings, "HARNESS_PUBLIC_BASE_URL", ""
            ) or request.build_absolute_uri("/").rstrip("/")
            retry_cfg = job.payload["retry"]
            start_hosted_harness_gateway_workflow(
                str(job.id),
                base_url,
                retry_cfg["max_infrastructure_attempts"],
                retry_cfg["initial_backoff_seconds"],
                retry_cfg["max_backoff_seconds"],
            )
        except HostedHarnessError as exc:
            return Response(exc.as_dict(), status=exc.status_code)
        except Exception:
            return Response(
                {"detail": "Hosted harness scheduler is unavailable"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(serialize_job(job), status=status.HTTP_202_ACCEPTED)

    def list(self, request) -> Response:
        organization = _organization(request)
        jobs = _scope_jobs(
            HostedHarnessJob.no_workspace_objects.filter(organization=organization),
            request,
        ).order_by("-created_at")[:100]
        return Response([serialize_job(job) for job in jobs])

    def preflight(self, request) -> Response:
        from simulate.services.hosted_harness import HostedHarnessError
        from simulate.services.hosted_harness_gateway import (
            HOSTED_ENGINE_CATALOG,
            HOSTED_RUNTIME_CATALOG,
            _validate_egress_domains,
        )

        payload = request.validated_data
        try:
            _validate_egress_domains(payload["security"]["allowed_egress_domains"])
        except HostedHarnessError as exc:
            return Response(exc.as_dict(), status=exc.status_code)
        runtime = payload["runtime"]
        return Response(
            {
                "ready_to_submit": True,
                "effective_parallelism": runtime["parallelism"],
                "snapshot": {
                    "name": getattr(settings, "ALK_DAYTONA_SNAPSHOT", None),
                    "digest": getattr(settings, "ALK_DAYTONA_SNAPSHOT_DIGEST", None),
                    "engines": HOSTED_ENGINE_CATALOG,
                    "runtimes": HOSTED_RUNTIME_CATALOG,
                },
            }
        )

    def retrieve(self, request, pk) -> Response:
        job = self._job(request, pk)
        if job is None:
            return Response(
                {"detail": "Hosted harness job not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(serialize_job(job))

    def cancel(self, request, pk) -> Response:
        from simulate.services.hosted_harness import request_cancellation
        from simulate.temporal.client import cancel_hosted_harness_gateway_workflow

        job = self._job(request, pk)
        if job is None:
            return Response(
                {"detail": "Hosted harness job not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        request_cancellation(job, request.validated_data["reason"])
        try:
            cancel_hosted_harness_gateway_workflow(str(job.id))
        except Exception:
            return Response(
                {"detail": "Cancellation was recorded but could not be signaled"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        job.refresh_from_db()
        return Response(serialize_job(job))

    def source_upload(self, request) -> Response:
        from simulate.services.hosted_harness import HostedHarnessError
        from simulate.services.hosted_harness_gateway import store_source_archive

        organization = _organization(request)
        if organization is None:
            return Response(
                {"detail": "Organization not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        files = request.FILES.getlist("files")
        paths = request.data.getlist("paths")
        if not files or len(files) != len(paths):
            return Response(
                {"detail": "one relative path is required per file"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(files) > 5_000:
            return Response(
                {"detail": "source may contain at most 5000 files"},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        total = sum(int(getattr(uploaded, "size", 0) or 0) for uploaded in files)
        if total > 200 * 1024 * 1024:
            return Response(
                {"detail": "source may not exceed 200 MiB"},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        try:
            result = store_source_archive(
                organization,
                files,
                paths,
                str(request.data.get("name") or "uploaded-agent"),
            )
        except HostedHarnessError as exc:
            return Response(exc.as_dict(), status=exc.status_code)
        return Response(result, status=status.HTTP_201_CREATED)

    def health(self) -> dict[str, Any]:
        return {
            "configured": bool(
                getattr(settings, "DAYTONA_API_KEY", "")
                and getattr(settings, "ALK_DAYTONA_SNAPSHOT", "")
            ),
            "provider": "daytona",
        }

    def _job(self, request, pk):
        organization = _organization(request)
        return _scope_jobs(
            HostedHarnessJob.no_workspace_objects.filter(
                id=pk, organization=organization
            ),
            request,
        ).first()


class SandboxHarnessProvider:
    """Thin HTTP proxy to an out-of-process ALK sandbox server.

    Accepts the same v1.6 request the Daytona provider does and maps it to the
    sandbox server's flat contract before forwarding.
    """

    name = "sandbox"

    def _client(self):
        from simulate.services.harness_sandbox import HarnessSandboxClient

        return HarnessSandboxClient()

    @staticmethod
    def _flatten_source(data: dict[str, Any]) -> dict[str, Any]:
        source = data["source"]
        agent = data["agent"]
        flat: dict[str, Any] = {
            "scenario_count": data.get("scenario_count", 10),
            "seed": data.get("seed"),
            "connector": agent.get("connector", "auto"),
            "connector_config": agent.get("config", {}) or {},
            "secret_refs": agent.get("secret_refs", {}) or {},
            "platform_run_id": data.get("platform_run_id"),
            "metadata": data.get("metadata", {}) or {},
        }
        kind = source["kind"]
        if kind == "github":
            flat["github_repository"] = source.get("repository")
            if source.get("ref"):
                flat["github_ref"] = source["ref"]
            if source.get("commit_sha"):
                flat["github_commit_sha"] = source["commit_sha"]
            flat["github_visibility"] = source.get("visibility", "public")
            if source.get("installation_id"):
                flat["github_installation_id"] = source["installation_id"]
        elif kind == "archive":
            flat["source_id"] = str(source["archive_artifact_id"])
        else:  # remote — the sandbox server has no external-target mode
            raise _SandboxMappingError(
                f"source kind {kind!r} is not supported by the sandbox provider"
            )
        return {key: value for key, value in flat.items() if value is not None}

    def create(self, request) -> Response:
        from simulate.services.harness_sandbox import (
            HarnessSandboxRejected,
            HarnessSandboxUnavailable,
        )

        try:
            payload = self._flatten_source(request.validated_data)
            result = self._client().submit(payload)
        except _SandboxMappingError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except HarnessSandboxRejected as exc:
            return Response({"detail": str(exc)}, status=exc.status_code)
        except HarnessSandboxUnavailable as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )
        return Response(result, status=status.HTTP_202_ACCEPTED)

    def list(self, request) -> Response:
        from simulate.services.harness_sandbox import (
            HarnessSandboxRejected,
            HarnessSandboxUnavailable,
        )

        try:
            return Response(self._client().list_jobs())
        except HarnessSandboxRejected as exc:
            return Response({"detail": str(exc)}, status=exc.status_code)
        except HarnessSandboxUnavailable as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )

    def preflight(self, request) -> Response:
        from simulate.services.harness_sandbox import (
            HarnessSandboxRejected,
            HarnessSandboxUnavailable,
        )

        try:
            payload = self._flatten_source(request.validated_data)
            return Response(self._client().preflight(payload))
        except _SandboxMappingError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except HarnessSandboxRejected as exc:
            return Response({"detail": str(exc)}, status=exc.status_code)
        except HarnessSandboxUnavailable as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )

    def retrieve(self, request, pk) -> Response:
        from simulate.services.harness_sandbox import (
            HarnessSandboxRejected,
            HarnessSandboxUnavailable,
        )

        try:
            return Response(self._client().get(str(pk)))
        except HarnessSandboxRejected as exc:
            return Response({"detail": str(exc)}, status=exc.status_code)
        except HarnessSandboxUnavailable as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )

    def cancel(self, request, pk) -> Response:
        from simulate.services.harness_sandbox import (
            HarnessSandboxRejected,
            HarnessSandboxUnavailable,
        )

        try:
            return Response(self._client().cancel(str(pk)))
        except HarnessSandboxRejected as exc:
            return Response({"detail": str(exc)}, status=exc.status_code)
        except HarnessSandboxUnavailable as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )

    def source_upload(self, request) -> Response:
        from simulate.services.harness_sandbox import (
            HarnessSandboxRejected,
            HarnessSandboxUnavailable,
        )

        files = request.FILES.getlist("files")
        paths = request.data.getlist("paths")
        if not files or len(files) != len(paths):
            return Response(
                {"detail": "one relative path is required per file"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            result = self._client().upload_source(
                files, paths, str(request.data.get("name") or "uploaded-agent")
            )
        except HarnessSandboxRejected as exc:
            return Response({"detail": str(exc)}, status=exc.status_code)
        except HarnessSandboxUnavailable as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )
        return Response(result, status=status.HTTP_201_CREATED)

    def health(self) -> dict[str, Any]:
        from simulate.services.harness_sandbox import HarnessSandboxUnavailable

        client = self._client()
        try:
            client.health()
            reachable = True
        except HarnessSandboxUnavailable:
            reachable = False
        return {"configured": reachable, "provider": "sandbox", "url": client.base_url}


class _SandboxMappingError(RuntimeError):
    pass
