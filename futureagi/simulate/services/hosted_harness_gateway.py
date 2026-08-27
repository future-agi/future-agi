from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import uuid
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import jwt
import requests
from django.conf import settings
from django.utils import timezone

from simulate.models import (
    HostedHarnessAttempt,
    HostedHarnessJob,
    HostedHarnessSecret,
)
from simulate.services.hosted_harness import (
    HostedHarnessError,
    record_cleanup,
    register_attempt,
    request_cancellation,
)
from tfc.settings.settings import UPLOAD_BUCKET_NAME
from tfc.utils.storage_client import ensure_bucket, get_storage_client

HOSTED_ENGINE_CATALOG = {
    "postgres": {
        "version": "16",
        "role": "harness",
        "database": "w<N>",
        "strategies": ["template_database", "datadir_copy"],
    },
    "redis": {
        "version": "7",
        "role": None,
        "database": None,
        "strategies": ["datadir_copy", "empty"],
    },
    "rabbitmq": {
        "version": "3.13",
        "role": "harness",
        "database": None,
        "strategies": ["datadir_copy"],
    },
}
HOSTED_RUNTIME_CATALOG = {
    "python": ["3.11", "3.12", "3.13"],
    "node": ["20", "22"],
    "binaries": ["git", "ffmpeg"],
}
_ENTRYPOINT_SESSION = "alk-harness"
_ENTRYPOINT_COMMAND_ID_FILE = "/run/futureagi/entrypoint-command-id"


class GitHubAppTokenProvider:
    def __init__(self, *, app_id: str, private_key: str) -> None:
        self.app_id = app_id
        self.private_key = private_key

    @classmethod
    def from_settings(cls) -> GitHubAppTokenProvider:
        app_id = getattr(settings, "GITHUB_APP_ID", "")
        private_key = getattr(settings, "GITHUB_APP_PRIVATE_KEY", "").replace(
            "\\n", "\n"
        )
        if not app_id or not private_key:
            raise HostedHarnessError(
                "github_app_not_configured",
                "GitHub App credentials are not configured",
                status_code=503,
                retryable=True,
            )
        return cls(app_id=str(app_id), private_key=private_key)

    def installation_token(self, installation_id: str) -> str:
        now = int(time.time())
        app_jwt = jwt.encode(
            {"iat": now - 30, "exp": now + 540, "iss": self.app_id},
            self.private_key,
            algorithm="RS256",
        )
        response = requests.post(
            f"https://api.github.com/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=(5, 30),
        )
        if response.status_code != 201:
            raise HostedHarnessError(
                "github_installation_token_failed",
                f"GitHub rejected installation {installation_id}",
                status_code=502,
                retryable=response.status_code >= 500,
            )
        token = response.json().get("token")
        if not token:
            raise HostedHarnessError(
                "github_installation_token_missing",
                "GitHub returned no installation token",
                status_code=502,
                retryable=True,
            )
        return str(token)

    def revoke(self, token: str) -> None:
        response = requests.delete(
            "https://api.github.com/installation/token",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=(5, 30),
        )
        if response.status_code != 204:
            raise HostedHarnessError(
                "github_installation_token_revoke_failed",
                "GitHub installation token could not be revoked",
                status_code=502,
                retryable=response.status_code >= 500,
            )

    @contextmanager
    def credential(self, installation_id: str):
        token = self.installation_token(installation_id)
        try:
            yield token
        finally:
            self.revoke(token)


class HostedSourceAcquirer:
    _REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    _REF = re.compile(r"^[A-Za-z0-9._/-]+$")

    def acquire(self, job: HostedHarnessJob) -> tuple[bytes, str]:
        source = job.payload["source"]
        if source["kind"] == "remote":
            return _empty_source_archive(), ""
        if source["kind"] == "archive":
            return _load_source_archive(job), ""
        if source["kind"] != "github":
            raise HostedHarnessError(
                "archive_source_not_configured",
                "archive source acquisition requires a configured archive registry",
                status_code=422,
            )
        repository = str(source.get("repository") or "")
        ref = str(source.get("ref") or "HEAD")
        if not self._REPOSITORY.fullmatch(repository):
            raise HostedHarnessError(
                "github_repository_invalid", "invalid GitHub repository"
            )
        if ".." in ref or not self._REF.fullmatch(ref):
            raise HostedHarnessError("github_ref_invalid", "invalid GitHub ref")
        credential = nullcontext("")
        if source.get("visibility") == "private":
            installation_id = str(source.get("installation_id") or "")
            credential = GitHubAppTokenProvider.from_settings().credential(
                installation_id
            )
        with (
            credential as token,
            tempfile.TemporaryDirectory(prefix=f"harness-source-{job.id}-") as temp,
        ):
            root = Path(temp)
            checkout = root / "source"
            environment = {
                **os.environ,
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_COUNT": "1" if token else "0",
            }
            if token:
                environment.update(
                    {
                        "GIT_CONFIG_KEY_0": "http.extraHeader",
                        "GIT_CONFIG_VALUE_0": f"Authorization: Bearer {token}",
                    }
                )
            commands = [
                ["git", "init", str(checkout)],
                [
                    "git",
                    "-C",
                    str(checkout),
                    "remote",
                    "add",
                    "origin",
                    f"https://github.com/{repository}.git",
                ],
                [
                    "git",
                    "-C",
                    str(checkout),
                    "fetch",
                    "--depth",
                    "1",
                    "origin",
                    str(source.get("commit_sha") or ref),
                ],
                ["git", "-C", str(checkout), "checkout", "--detach", "FETCH_HEAD"],
            ]
            for command in commands:
                completed = subprocess.run(
                    command,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=300,
                    check=False,
                )
                if completed.returncode:
                    detail = completed.stderr or completed.stdout or "git failed"
                    if token:
                        detail = detail.replace(token, "[REDACTED]")
                    raise HostedHarnessError(
                        "github_clone_failed",
                        detail.strip()[:500],
                        status_code=502,
                        retryable=True,
                    )
            completed = subprocess.run(
                ["git", "-C", str(checkout), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
            commit_sha = completed.stdout.strip().lower()
            expected = source.get("commit_sha")
            if expected and commit_sha != str(expected).lower():
                raise HostedHarnessError(
                    "github_commit_mismatch",
                    "resolved checkout does not match commit_sha",
                    status_code=409,
                )
            shutil.rmtree(checkout / ".git", ignore_errors=True)
            archive = io.BytesIO()
            with tarfile.open(fileobj=archive, mode="w:gz") as tar:
                tar.add(checkout, arcname="source", recursive=True)
            max_bytes = getattr(
                settings, "ALK_HOSTED_SOURCE_MAX_BYTES", 256 * 1024 * 1024
            )
            if archive.tell() > max_bytes:
                raise HostedHarnessError(
                    "source_archive_too_large",
                    "compressed source exceeds the hosted source limit",
                    status_code=413,
                )
            return archive.getvalue(), commit_sha


class PlatformSecretResolver:
    def resolve(self, job: HostedHarnessJob) -> dict[str, str]:
        resolved: dict[str, str] = {}
        for alias, reference in job.payload["agent"]["secret_refs"].items():
            if reference["manager"] != "platform-vault":
                raise HostedHarnessError(
                    "secret_manager_unsupported",
                    f"unsupported secret manager for {alias}",
                    status_code=422,
                )
            if reference["purpose"] != "target_provider":
                raise HostedHarnessError(
                    "secret_purpose_invalid",
                    f"secret {alias} is not a target_provider secret",
                    status_code=422,
                )
            query = HostedHarnessSecret.no_workspace_objects.filter(
                organization=job.organization,
                name=reference["key"],
            )
            version = reference.get("version")
            if version is not None:
                query = query.filter(version=version)
            secret = query.order_by("-created_at").first()
            if secret is None:
                raise HostedHarnessError(
                    "secret_not_found",
                    f"platform secret not found for alias {alias}",
                    status_code=422,
                )
            resolved[alias] = secret.get_value()
        return resolved


class DaytonaHostedGateway:
    def __init__(self) -> None:
        from daytona import Daytona, DaytonaConfig

        api_key = getattr(settings, "DAYTONA_API_KEY", "")
        snapshot = getattr(settings, "ALK_DAYTONA_SNAPSHOT", "")
        if not api_key or not snapshot:
            raise HostedHarnessError(
                "daytona_not_configured",
                "DAYTONA_API_KEY and ALK_DAYTONA_SNAPSHOT are required",
                status_code=503,
                retryable=True,
            )
        self.snapshot = snapshot
        self.snapshot_digest = getattr(settings, "ALK_DAYTONA_SNAPSHOT_DIGEST", "")
        self.client = Daytona(
            DaytonaConfig(
                api_key=api_key,
                api_url=getattr(settings, "DAYTONA_API_URL", None),
                target=getattr(settings, "DAYTONA_TARGET", None),
                organization_id=getattr(settings, "DAYTONA_ORGANIZATION_ID", None),
            )
        )

    def author(self, job: HostedHarnessJob) -> bytes:
        """Run ALK authoring (understand -> environment -> scenarios) inside a Daytona sandbox.

        Scenario generation is LLM-heavy and must run off the control-plane worker. This provisions
        a throwaway sandbox with only the authoring model credentials (never the target call
        secrets), runs the same ``authoring_entrypoint`` the local SDK uses, and returns the packed
        frozen authoring archive that ``bundle_author_v2`` later seals inside the execution sandbox.
        """
        from daytona import CreateSandboxFromSnapshotParams

        source_archive, commit_sha = HostedSourceAcquirer().acquire(job)
        payload = dict(job.payload)
        source = dict(payload["source"])
        if source["kind"] == "github":
            source["commit_sha"] = commit_sha
            payload["source"] = source
        if payload != job.payload:
            job.payload = payload
            job.save(update_fields=["payload", "updated_at"])

        # Authoring reaches only the model provider and the source host - never the target
        # (LiveKit/Deepgram) media secrets, which belong to the execution sandbox alone.
        secrets_map = PlatformSecretResolver().resolve(job)
        sa_json = str(secrets_map.get("GOOGLE_APPLICATION_CREDENTIALS_JSON") or "")
        project_id = ""
        if sa_json:
            try:
                project_id = str(json.loads(sa_json).get("project_id") or "")
            except (ValueError, TypeError):
                project_id = ""

        # Authoring reaches only the source host and the authoring model provider (Vertex/Claude).
        # Daytona caps the domain allow list at 20 entries, so this stays focused and excludes the
        # call-time media domains (LiveKit/Deepgram) that only the execution sandbox needs.
        default_authoring_egress = [
            "github.com",
            "codeload.github.com",
            "api.github.com",
            "objects.githubusercontent.com",
            "raw.githubusercontent.com",
            "oauth2.googleapis.com",
            "www.googleapis.com",
            "sts.googleapis.com",
            "accounts.google.com",
            "aiplatform.googleapis.com",
            "us-east5-aiplatform.googleapis.com",
            "us-central1-aiplatform.googleapis.com",
        ]
        allowed_domains = list(
            dict.fromkeys(
                getattr(
                    settings,
                    "ALK_HOSTED_AUTHORING_EGRESS_DOMAINS",
                    default_authoring_egress,
                )
            )
        )[:20]
        authoring_env = {
            "CLAUDE_CODE_USE_VERTEX": "1",
            "GOOGLE_GENAI_USE_VERTEXAI": "True",
            "CLOUD_ML_REGION": getattr(
                settings, "ALK_HOSTED_AUTHORING_CLAUDE_REGION", "us-east5"
            ),
            "GOOGLE_CLOUD_LOCATION": getattr(
                settings, "ALK_HOSTED_AUTHORING_GEMINI_LOCATION", "us-central1"
            ),
            "GOOGLE_APPLICATION_CREDENTIALS": "/run/futureagi/vertex-sa.json",
        }
        if project_id:
            authoring_env["GOOGLE_CLOUD_PROJECT"] = project_id
            authoring_env["ANTHROPIC_VERTEX_PROJECT_ID"] = project_id

        ttl_minutes = int(getattr(settings, "ALK_HOSTED_AUTHORING_TTL_MINUTES", 40))
        sandbox = None
        try:
            sandbox = self.client.create(
                CreateSandboxFromSnapshotParams(
                    snapshot=self.snapshot,
                    language="python",
                    os_user=getattr(
                        settings, "ALK_HOSTED_SANDBOX_OS_USER", "svc-control"
                    ),
                    labels={
                        "futureagi.job": str(job.id),
                        "futureagi.authoring": "1",
                    },
                    network_block_all=not allowed_domains,
                    domain_allow_list=",".join(sorted(allowed_domains)) or None,
                    ephemeral=True,
                    ttl_minutes=ttl_minutes,
                    auto_delete_interval=ttl_minutes,
                ),
                timeout=300,
            )
            sandbox.fs.upload_file(source_archive, "/work/source.tar.gz")
            sandbox.fs.upload_file(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
                "/work/job.json",
            )
            if sa_json:
                sandbox.fs.upload_file(
                    sa_json.encode(), "/run/futureagi/vertex-sa.json"
                )
            prepared = sandbox.process.exec(
                "tar -xzf /work/source.tar.gz -C /work && rm /work/source.tar.gz && "
                "chown -R svc-control:svc-control /work/source",
                timeout=120,
            )
            if prepared.exit_code:
                raise HostedHarnessError(
                    "authoring_source_prepare_failed",
                    str(prepared.result or "")[-500:],
                    status_code=502,
                    retryable=True,
                )
            # The authoring model session drops intermittently; a fresh output dir plus a
            # retry clears it. Early drops fail fast, so retries stay cheap.
            attempts = max(1, int(getattr(settings, "ALK_HOSTED_AUTHORING_ATTEMPTS", 3)))
            run_timeout = int(getattr(settings, "ALK_HOSTED_AUTHORING_TIMEOUT", 1500))
            detail = "authoring produced no scenarios"
            for _ in range(attempts):
                run = sandbox.process.exec(
                    "rm -rf /work/authoring && "
                    "python -m fi.alk.harness.authoring_entrypoint /work/job.json "
                    "--source /work/source --output /work/authoring",
                    env=authoring_env,
                    timeout=run_timeout,
                )
                check = sandbox.process.exec(
                    "ls /work/authoring/scenarios 2>/dev/null | wc -l", timeout=30
                )
                try:
                    produced = int(str(check.result or "0").strip() or "0")
                except ValueError:
                    produced = 0
                if run.exit_code == 0 and produced >= 1:
                    break
                detail = str(run.result or detail)[-1000:]
            else:
                raise HostedHarnessError(
                    "authoring_failed", detail, status_code=422, retryable=False
                )
            packed = sandbox.process.exec(
                "cd /work/authoring && tar -czf /tmp/authoring.tar.gz "
                "$(ls -d world.sqlite schema.sql store.json collections.json "
                "contract.json simulator_prompt.md scenarios handlers 2>/dev/null)",
                timeout=180,
            )
            if packed.exit_code:
                raise HostedHarnessError(
                    "authoring_pack_failed",
                    str(packed.result or "")[-500:],
                    status_code=502,
                    retryable=True,
                )
            return sandbox.fs.download_file("/tmp/authoring.tar.gz")
        finally:
            if sandbox is not None:
                try:
                    sandbox.delete()
                except Exception:  # noqa: BLE001 - best-effort cleanup of throwaway sandbox
                    pass

    def launch(
        self, job: HostedHarnessJob, *, endpoint_base_url: str
    ) -> HostedHarnessAttempt:
        from daytona import CreateSandboxFromSnapshotParams, SessionExecuteRequest

        source_archive, commit_sha = HostedSourceAcquirer().acquire(job)
        authoring_archive = _authoring_archive_for(job)
        payload = dict(job.payload)
        if authoring_archive is not None:
            # Resolve cached authoring created before connector resolution shipped as well as
            # newly-authored jobs. This must happen before network policy and job.json are built.
            payload = resolve_authored_connector(payload, authoring_archive)
        source = dict(payload["source"])
        if source["kind"] == "github":
            source["commit_sha"] = commit_sha
        payload["source"] = source
        if payload != job.payload:
            job.payload = payload
            job.save(update_fields=["payload", "updated_at"])
        secrets_map = PlatformSecretResolver().resolve(job)
        dispatch_payload = prepare_dispatch_payload(payload, secrets_map)
        capability = register_attempt(
            job.id,
            endpoint_base_url=endpoint_base_url,
            snapshot_name=self.snapshot,
            snapshot_digest=self.snapshot_digest or None,
        )
        attempt = capability.attempt
        ttl_seconds = payload["runtime"]["max_duration_seconds"] + 120
        ttl_minutes = max(1, (ttl_seconds + 59) // 60)
        platform_host = urlparse(endpoint_base_url).hostname
        allowed_domains = set(getattr(settings, "ALK_HOSTED_BASE_EGRESS_DOMAINS", []))
        allowed_domains.update(payload["security"]["allowed_egress_domains"])
        # WebRTC negotiates media over IP candidates after the HTTPS/WebSocket signalling
        # connection. A domain-only allowlist permits LiveKit signalling but can block the peer
        # connection. Daytona rejects requests containing both allow-list forms, so an explicit
        # operator CIDR policy replaces (rather than augments) the domain policy for LiveKit.
        # Production CIDR policies therefore need to cover both media and every required HTTPS
        # destination; the local certification lane deliberately uses 0.0.0.0/0.
        webrtc_cidrs: list[str] = []
        if (
            payload["runtime"]["network_policy"] == "live"
            and payload["agent"]["connector"] == "livekit"
        ):
            webrtc_cidrs = list(getattr(settings, "ALK_HOSTED_WEBRTC_EGRESS_CIDRS", []))
        if platform_host:
            allowed_domains.add(platform_host)
        sandbox = None
        try:
            sandbox = self.client.create(
                CreateSandboxFromSnapshotParams(
                    snapshot=self.snapshot,
                    language="python",
                    os_user=getattr(
                        settings, "ALK_HOSTED_SANDBOX_OS_USER", "svc-control"
                    ),
                    labels={
                        "futureagi.job": str(job.id),
                        "futureagi.attempt": str(attempt.id),
                    },
                    network_block_all=not allowed_domains,
                    network_allow_list=",".join(webrtc_cidrs) or None,
                    domain_allow_list=(
                        None
                        if webrtc_cidrs
                        else ",".join(sorted(allowed_domains)) or None
                    ),
                    ephemeral=True,
                    ttl_minutes=ttl_minutes,
                    auto_delete_interval=ttl_minutes,
                ),
                timeout=300,
            )
            attempt.provider_ref = sandbox.id
            attempt.state = HostedHarnessAttempt.State.PROVISIONING
            attempt.save(update_fields=["provider_ref", "state", "updated_at"])
            sandbox.fs.upload_file(source_archive, "/work/source.tar.gz")
            sandbox.fs.upload_file(
                json.dumps(
                    dispatch_payload, sort_keys=True, separators=(",", ":")
                ).encode(),
                "/work/job.json",
            )
            sandbox.fs.upload_file(
                json.dumps(secrets_map, sort_keys=True, separators=(",", ":")).encode(),
                "/run/futureagi/secrets.json",
            )
            sandbox.fs.upload_file(
                json.dumps(
                    capability.document, sort_keys=True, separators=(",", ":")
                ).encode(),
                "/run/futureagi/capabilities.json",
            )
            if authoring_archive is not None:
                sandbox.fs.upload_file(authoring_archive, "/work/authoring.tar.gz")
            prepared = sandbox.process.exec(
                "if [ -f /work/authoring.tar.gz ]; then mkdir -p /work/authoring && "
                "tar -xzf /work/authoring.tar.gz -C /work/authoring && "
                "rm /work/authoring.tar.gz; fi && "
                "tar -xzf /work/source.tar.gz -C /work && "
                "rm /work/source.tar.gz && "
                "chown -R svc-control:svc-control /work/source && "
                "chmod -R a-w /work/source && "
                "chmod 0600 /work/job.json /run/futureagi/secrets.json "
                "/run/futureagi/capabilities.json",
                timeout=120,
            )
            if prepared.exit_code:
                raise HostedHarnessError(
                    "sandbox_upload_prepare_failed",
                    prepared.result[-500:],
                    status_code=502,
                    retryable=True,
                )
            authored = sandbox.process.exec(
                "python -m fi.alk.harness.bundle_author_v2 "
                "--job /work/job.json --source /work/source "
                "--authoring /work/authoring --output /work/bundle",
                timeout=900,
            )
            if authored.exit_code:
                detail = str(authored.result or "bundle authoring failed")[-1000:]
                raise HostedHarnessError(
                    "bundle_authoring_failed",
                    detail,
                    status_code=422,
                    retryable=False,
                )
            sandbox.process.create_session(_ENTRYPOINT_SESSION)
            command = sandbox.process.execute_session_command(
                _ENTRYPOINT_SESSION,
                SessionExecuteRequest(
                    command=(
                        "python -m fi.alk.harness.hosted_entrypoint /work/job.json "
                        "--source /work/source --output /work/artifacts"
                    ),
                    run_async=True,
                    suppress_input_echo=True,
                ),
            )
            sandbox.fs.upload_file(
                str(command.cmd_id).encode(), _ENTRYPOINT_COMMAND_ID_FILE
            )
            attempt.state = HostedHarnessAttempt.State.RUNNING
            attempt.heartbeat_at = timezone.now()
            attempt.save(update_fields=["state", "heartbeat_at", "updated_at"])
            return attempt
        except Exception:
            attempt.terminal_stage = "failed"
            attempt.terminal_failure = {
                "domain": "infrastructure",
                "stage": "queued",
                "code": "sandbox_launch_failed",
                "message": "sandbox failed before the guest entrypoint started",
            }
            attempt.state = HostedHarnessAttempt.State.FAILED
            attempt.save(
                update_fields=[
                    "terminal_stage",
                    "terminal_failure",
                    "state",
                    "updated_at",
                ]
            )
            if sandbox is None:
                record_cleanup(
                    attempt.id,
                    provider_ref="",
                    verified_absent=True,
                    details={"provider": "daytona", "sandbox_created": False},
                )
            else:
                self._delete_and_record(attempt)
            raise

    def inspect(self, attempt: HostedHarnessAttempt) -> dict[str, Any]:
        sandbox = self.client.get(str(attempt.provider_ref))
        command_id = (
            sandbox.fs.download_file(_ENTRYPOINT_COMMAND_ID_FILE).decode().strip()
        )
        command = sandbox.process.get_session_command(_ENTRYPOINT_SESSION, command_id)
        observation: dict[str, Any] = {
            "exit_code": command.exit_code,
            "status": getattr(command, "status", None),
            "logs": "",
        }
        if command.exit_code is not None:
            # Capture the guest's combined stdout/stderr before the sandbox is
            # torn down, so crashes are diagnosable without keeping sandboxes.
            try:
                logs = sandbox.process.get_session_command_logs(
                    _ENTRYPOINT_SESSION, command_id
                )
                text = getattr(logs, "output", None) or "\n".join(
                    part
                    for part in (
                        getattr(logs, "stdout", ""),
                        getattr(logs, "stderr", ""),
                    )
                    if part
                )
                observation["logs"] = (text or "")[-8000:]
            except Exception:  # noqa: BLE001
                observation["logs"] = ""
        return observation

    def cancel(self, job: HostedHarnessJob, *, reason: str) -> HostedHarnessJob:
        from daytona import DaytonaNotFoundError

        job = request_cancellation(job, reason)
        attempt = HostedHarnessAttempt.no_workspace_objects.filter(
            job=job, attempt_number=job.current_attempt_number
        ).first()
        if attempt is None or not attempt.provider_ref:
            return job
        try:
            sandbox = self.client.get(str(attempt.provider_ref))
        except DaytonaNotFoundError:
            attempt.terminal_stage = "canceled"
            attempt.terminal_reason = reason
            attempt.save(
                update_fields=["terminal_stage", "terminal_reason", "updated_at"]
            )
            return record_cleanup(
                attempt.id,
                provider_ref=str(attempt.provider_ref),
                verified_absent=True,
                details={"provider": "daytona", "already_absent": True},
            )
        sandbox.fs.upload_file(
            json.dumps({"reason": reason}, separators=(",", ":")).encode(),
            "/run/futureagi/cancel.json",
        )
        sandbox.process.exec(
            "pkill -TERM -f 'fi.alk.harness.hosted_entrypoint' || true",
            timeout=30,
        )
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            observation = self.inspect(attempt)
            if observation["exit_code"] is not None:
                break
            time.sleep(2)
        self._delete_and_record(attempt)
        return HostedHarnessJob.no_workspace_objects.get(id=job.id)

    def _should_retry(self, attempt: HostedHarnessAttempt, domain: str) -> bool:
        # An infrastructure/connectivity failure is worth a fresh attempt while
        # the domain is retryable and the whole-job attempt budget remains
        # (spine §0.6 + §1 `retry`). Exit 0 (verdict delivered) and exit 3
        # (superseded) never reach here.
        retry = attempt.job.payload.get("retry", {})
        return domain in retry.get(
            "retryable_domains", []
        ) and attempt.attempt_number < retry.get("max_infrastructure_attempts", 1)

    def reconcile_completed(
        self, attempt: HostedHarnessAttempt
    ) -> HostedHarnessJob | None:
        from daytona import DaytonaNotFoundError

        try:
            observation = self.inspect(attempt)
        except DaytonaNotFoundError:
            retry_pending = False
            if not attempt.terminal_event_received:
                attempt.terminal_stage = "failed"
                attempt.terminal_failure = {
                    "domain": "infrastructure",
                    "stage": "running",
                    "code": "sandbox_disappeared",
                    "message": "provider sandbox disappeared before terminal delivery",
                }
                attempt.state = HostedHarnessAttempt.State.FAILED
                attempt.save(
                    update_fields=[
                        "terminal_stage",
                        "terminal_failure",
                        "state",
                        "updated_at",
                    ]
                )
                retry_pending = self._should_retry(attempt, "infrastructure")
            return record_cleanup(
                attempt.id,
                provider_ref=str(attempt.provider_ref),
                verified_absent=True,
                retry_pending=retry_pending,
                details={"provider": "daytona", "already_absent": True},
            )
        exit_code = observation["exit_code"]
        if exit_code is None:
            attempt.heartbeat_at = timezone.now()
            attempt.save(update_fields=["heartbeat_at", "updated_at"])
            return None
        retry_pending = False
        if exit_code == 3:
            attempt.state = HostedHarnessAttempt.State.SUPERSEDED
            attempt.save(update_fields=["state", "updated_at"])
        elif exit_code == 4:
            # Spine v1.14 exit 4: terminal state reached, but the terminal
            # event could not be delivered (events channel died or the platform
            # rejected the final drain). Still an infrastructure failure — and
            # retryable once P1 lands — but labelled distinctly so operators
            # don't read an undeliverable-evidence exit as a guest crash.
            attempt.terminal_stage = "failed"
            attempt.terminal_failure = {
                "domain": "infrastructure",
                "stage": "finalizing",
                "code": "evidence_undeliverable",
                "message": (
                    "guest reached terminal state but could not deliver "
                    "the terminal event"
                ),
                "details": {"guest_log_tail": observation.get("logs", "")},
            }
            attempt.state = HostedHarnessAttempt.State.FAILED
            attempt.save(
                update_fields=[
                    "terminal_stage",
                    "terminal_failure",
                    "state",
                    "updated_at",
                ]
            )
            retry_pending = self._should_retry(attempt, "infrastructure")
        elif exit_code != 0 and not attempt.terminal_event_received:
            attempt.terminal_stage = "failed"
            attempt.terminal_failure = {
                "domain": "infrastructure",
                "stage": "running",
                "code": "guest_crashed",
                "message": f"guest entrypoint exited {exit_code}",
                "details": {"guest_log_tail": observation.get("logs", "")},
            }
            attempt.state = HostedHarnessAttempt.State.FAILED
            attempt.save(
                update_fields=[
                    "terminal_stage",
                    "terminal_failure",
                    "state",
                    "updated_at",
                ]
            )
            retry_pending = self._should_retry(attempt, "infrastructure")
        elif exit_code == 0 and (
            not attempt.terminal_event_received or not attempt.manifest_acked
        ):
            attempt.terminal_stage = "failed"
            attempt.terminal_failure = {
                "domain": "platform_sync",
                "stage": "uploading_artifacts",
                "code": "terminal_delivery_incomplete",
                "message": "guest exited without an acknowledged terminal stream",
                "details": {"guest_log_tail": observation.get("logs", "")},
            }
            attempt.state = HostedHarnessAttempt.State.FAILED
            attempt.save(
                update_fields=[
                    "terminal_stage",
                    "terminal_failure",
                    "state",
                    "updated_at",
                ]
            )
        return self._delete_and_record(attempt, retry_pending=retry_pending)

    def _delete_and_record(
        self, attempt: HostedHarnessAttempt, *, retry_pending: bool = False
    ) -> HostedHarnessJob:
        from daytona import DaytonaNotFoundError

        try:
            sandbox = self.client.get(str(attempt.provider_ref))
        except DaytonaNotFoundError:
            absent = True
        else:
            self.client.delete(sandbox, timeout=120, wait=True)
            try:
                self.client.get(str(attempt.provider_ref))
            except DaytonaNotFoundError:
                absent = True
            else:
                absent = False
        return record_cleanup(
            attempt.id,
            provider_ref=str(attempt.provider_ref),
            verified_absent=absent,
            retry_pending=retry_pending,
            details={"provider": "daytona", "deleted_at": timezone.now().isoformat()},
        )


def _empty_source_archive() -> bytes:
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        info = tarfile.TarInfo("source")
        info.type = tarfile.DIRTYPE
        info.mode = 0o555
        tar.addfile(info)
    return archive.getvalue()


def _source_object_key(organization_id, source_id: str) -> str:
    return f"harness-sources/{organization_id}/{source_id}.tar.gz"


def _safe_source_member(raw_path: str) -> str:
    """Return a repo-relative POSIX path, rejecting traversal/absolute paths."""
    candidate = str(raw_path).strip().lstrip("/")
    if not candidate or ".." in candidate.split("/") or candidate.startswith("/"):
        raise HostedHarnessError(
            "source_path_invalid",
            f"invalid source path: {raw_path!r}",
            status_code=400,
        )
    return candidate


def _authoring_archive_for(job: HostedHarnessJob) -> bytes | None:
    """Resolve frozen world/scenario authoring inputs for the in-sandbox v2 producer.

    The directory may be an older session or a hand-authored v2 bundle; the producer adopts only
    its schema/scenarios and deterministically derives runtime topology from the newly acquired
    source. It never trusts or forwards an old manifest.
    """
    metadata = (job.payload or {}).get("metadata") or {}
    object_key = str(metadata.get("authoring_object_key") or "").strip()
    if object_key:
        response = None
        try:
            response = get_storage_client().get_object(UPLOAD_BUCKET_NAME, object_key)
            return response.read()
        except Exception as exc:
            raise HostedHarnessError(
                "authoring_artifacts_not_found",
                "the frozen ALK authoring artifacts could not be loaded",
                status_code=422,
            ) from exc
        finally:
            if response is not None:
                response.close()
                response.release_conn()

    base = getattr(settings, "ALK_HOSTED_BUNDLE_DIR", "")
    if not base:
        return None
    payload = job.payload or {}
    source = payload.get("source") or {}
    # Uploaded folders have no repository slug. ``authoring_key`` is the stable identity of the
    # frozen contract/world/scenario output produced before dispatch; a source UUID is accepted as
    # a final fallback for callers that store those outputs alongside the uploaded archive.
    candidates = (
        metadata.get("authoring_key"),
        source.get("repository"),
        metadata.get("name"),
        source.get("archive_artifact_id"),
    )
    bundle_dir = None
    root = Path(base).resolve()
    for raw in candidates:
        key = str(raw or "").strip()
        if not key or not re.fullmatch(r"[A-Za-z0-9._/-]+", key):
            continue
        candidate = (root / key.replace("/", "__")).resolve()
        if candidate.is_relative_to(root) and (candidate / "scenarios").is_dir():
            bundle_dir = candidate
            break
    if bundle_dir is None:
        return None
    # Authoring directories are long-lived developer/platform workspaces and may also contain
    # historical recordings, database data directories, logs and prior environment builds.  None
    # of those are V2 producer inputs.  Upload only the explicit authoring contract instead of
    # recursively shipping the entire workspace to every sandbox.
    files: list[Path] = []
    for name in (
        "schema.sql",
        "store.json",
        "world.sqlite",
        "collections.json",
        "contract.json",
        "simulator_prompt.md",
    ):
        path = bundle_dir / name
        if path.is_file() and not path.is_symlink():
            files.append(path)
    for directory_name in ("scenarios", "handlers"):
        directory = bundle_dir / directory_name
        if directory.is_dir() and not directory.is_symlink():
            files.extend(
                path
                for path in sorted(directory.rglob("*"))
                if path.is_file() and not path.is_symlink()
            )
    max_files = 10_000
    max_bytes = 128 * 1024 * 1024
    total_bytes = sum(path.stat().st_size for path in files)
    if len(files) > max_files or total_bytes > max_bytes:
        raise HostedHarnessError(
            "authoring_artifacts_too_large",
            (
                "frozen authoring inputs exceed the hosted limit: "
                f"files={len(files)}/{max_files}, bytes={total_bytes}/{max_bytes}"
            ),
            status_code=422,
            retryable=False,
        )
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        for path in sorted(files):
            tar.add(path, arcname=path.relative_to(bundle_dir).as_posix())
    return archive.getvalue()


def pack_authoring_archive(authoring_root: Path) -> bytes:
    """Pack only the frozen ALK outputs consumed by ``bundle_author_v2``."""
    files: list[Path] = []
    for name in (
        "schema.sql",
        "store.json",
        "world.sqlite",
        "collections.json",
        "contract.json",
        "simulator_prompt.md",
    ):
        path = authoring_root / name
        if path.is_file() and not path.is_symlink():
            files.append(path)
    for directory_name in ("scenarios", "handlers"):
        directory = authoring_root / directory_name
        if directory.is_dir() and not directory.is_symlink():
            files.extend(
                path
                for path in sorted(directory.rglob("*"))
                if path.is_file() and not path.is_symlink()
            )
    if not (authoring_root / "contract.json").is_file():
        raise HostedHarnessError(
            "agent_contract_missing",
            "ALK authoring completed without an agent contract",
            status_code=422,
        )
    if not (authoring_root / "scenarios").is_dir():
        raise HostedHarnessError(
            "scenario_artifacts_missing",
            "ALK authoring completed without scenario artifacts",
            status_code=422,
        )
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        for path in sorted(files):
            tar.add(path, arcname=path.relative_to(authoring_root).as_posix())
    return archive.getvalue()


def resolve_authored_connector(payload: dict[str, Any], body: bytes) -> dict[str, Any]:
    """Resolve ``auto`` only when frozen authoring evidence is unambiguous.

    Explicit user choices remain authoritative. For the repository-hosted voice lane, require
    both a voice contract and the complete LiveKit credential triplet before selecting LiveKit.
    Bundle compilation and guest dispatch then consume the same concrete connector.
    """
    resolved = dict(payload)
    agent = dict(resolved.get("agent") or {})
    if str(agent.get("connector") or "auto").lower() != "auto":
        return resolved

    try:
        with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
            member = archive.extractfile("contract.json")
            contract = json.load(member) if member is not None else {}
    except (KeyError, tarfile.TarError, json.JSONDecodeError, OSError, TypeError):
        return resolved

    aliases = {str(alias).upper() for alias in (agent.get("secret_refs") or {})}
    required = {"LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"}
    if str(contract.get("modality") or "").lower() == "voice" and required <= aliases:
        agent["connector"] = "livekit"
        resolved["agent"] = agent
    return resolved


def prepare_dispatch_payload(
    payload: dict[str, Any], secrets_map: dict[str, str]
) -> dict[str, Any]:
    """Add non-secret connector configuration derived from run-scoped secrets.

    Hosted users supply ``LIVEKIT_URL`` beside the LiveKit key pair. The target agent needs the
    uppercase environment alias, while the ALK caller contract reads lowercase
    ``agent.config.livekit_url``. Mirror only the public endpoint into the ephemeral job document;
    API keys and secret values remain exclusively in ``secrets.json`` and the stored job payload is
    never mutated.
    """
    dispatched = dict(payload)
    agent = dict(dispatched.get("agent") or {})
    config = dict(agent.get("config") or {})
    if (
        str(agent.get("connector") or "").lower() == "livekit"
        and not config.get("livekit_url")
        and secrets_map.get("LIVEKIT_URL")
    ):
        config["livekit_url"] = secrets_map["LIVEKIT_URL"]
        agent["config"] = config
        dispatched["agent"] = agent
    return dispatched


def store_authoring_archive(job: HostedHarnessJob, body: bytes) -> str:
    """Persist fresh authoring output and attach its opaque key to the hosted job."""
    object_key = f"harness-authoring/{job.organization_id}/{job.id}.tar.gz"
    client = get_storage_client()
    ensure_bucket(client, UPLOAD_BUCKET_NAME)
    client.put_object(
        bucket_name=UPLOAD_BUCKET_NAME,
        object_name=object_key,
        data=io.BytesIO(body),
        length=len(body),
        content_type="application/gzip",
    )
    payload = resolve_authored_connector(dict(job.payload or {}), body)
    metadata = dict(payload.get("metadata") or {})
    metadata["authoring_object_key"] = object_key
    metadata["authoring_mode"] = "fresh"
    payload["metadata"] = metadata
    job.payload = payload
    job.current_stage = "validating_scenarios"
    job.state = HostedHarnessJob.State.ADMITTED
    job.save(update_fields=["payload", "current_stage", "state", "updated_at"])
    return object_key


def store_source_archive(organization, files, paths, name: str) -> dict[str, Any]:
    """Pack an uploaded folder into a `source/`-rooted tar.gz in object storage.

    Returns the descriptor the caller echoes to the client. The resulting
    `source_id` is used later as `source.archive_artifact_id` and resolved by
    `HostedSourceAcquirer` for `source.kind == "archive"`.
    """
    source_id = str(uuid.uuid4())
    archive = io.BytesIO()
    total = 0
    seen: set[str] = set()
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        for uploaded, raw_path in zip(files, paths, strict=True):
            relative = _safe_source_member(raw_path)
            if relative in seen:
                raise HostedHarnessError(
                    "source_path_duplicate",
                    f"duplicate source path: {relative}",
                    status_code=400,
                )
            seen.add(relative)
            data = uploaded.read()
            total += len(data)
            info = tarfile.TarInfo(f"source/{relative}")
            info.size = len(data)
            info.mode = 0o755 if data[:2] == b"#!" else 0o644
            tar.addfile(info, io.BytesIO(data))
    body = archive.getvalue()
    max_bytes = getattr(settings, "ALK_HOSTED_SOURCE_MAX_BYTES", 256 * 1024 * 1024)
    if len(body) > max_bytes:
        raise HostedHarnessError(
            "source_archive_too_large",
            "compressed source exceeds the hosted source limit",
            status_code=413,
        )
    client = get_storage_client()
    ensure_bucket(client, UPLOAD_BUCKET_NAME)
    client.put_object(
        bucket_name=UPLOAD_BUCKET_NAME,
        object_name=_source_object_key(organization.id, source_id),
        data=io.BytesIO(body),
        length=len(body),
        content_type="application/gzip",
    )
    return {
        "source_id": source_id,
        "name": (name or "uploaded-agent")[:255],
        "file_count": len(files),
        "total_bytes": total,
    }


def _load_source_archive(job: HostedHarnessJob) -> bytes:
    source_id = str(job.payload["source"].get("archive_artifact_id") or "")
    if not source_id:
        raise HostedHarnessError(
            "archive_artifact_missing",
            "archive source requires archive_artifact_id",
            status_code=422,
        )
    key = _source_object_key(job.organization_id, source_id)
    response = None
    try:
        response = get_storage_client().get_object(UPLOAD_BUCKET_NAME, key)
        return response.read()
    except HostedHarnessError:
        raise
    except Exception as exc:
        raise HostedHarnessError(
            "archive_source_not_found",
            "uploaded source archive was not found",
            status_code=422,
        ) from exc
    finally:
        if response is not None:
            response.close()
            response.release_conn()
