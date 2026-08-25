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

from simulate.models import HostedHarnessJob


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
    return {
        "job": {
            "job_id": str(job.id),
            "run_id": str(job.run_id),
            "source": job.payload["source"],
            "metadata": job.payload.get("metadata", {}),
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
    }


class DaytonaHarnessProvider:
    """Platform-as-gateway. Persists the job and drives Daytona via Temporal."""

    name = "daytona"

    def create(self, request) -> Response:
        from simulate.services.hosted_harness import (
            HostedHarnessError,
            create_hosted_job,
        )
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
        try:
            job, _ = create_hosted_job(
                organization,
                request.validated_data,
                idempotency_key=idempotency_key,
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
        jobs = HostedHarnessJob.no_workspace_objects.filter(
            organization=organization
        ).order_by("-created_at")[:100]
        return Response([serialize_job(job) for job in jobs])

    def preflight(self, request) -> Response:
        from simulate.services.hosted_harness_gateway import (
            HOSTED_ENGINE_CATALOG,
            HOSTED_RUNTIME_CATALOG,
        )

        payload = request.validated_data
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
        return HostedHarnessJob.no_workspace_objects.filter(
            id=pk, organization=organization
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
