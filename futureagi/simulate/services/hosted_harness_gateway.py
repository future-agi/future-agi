from __future__ import annotations

import io
import json
import logging
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

logger = logging.getLogger("simulate.hosted_harness_gateway")

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


_MAX_EGRESS_DOMAINS = 20
# RFC 1918 / loopback / link-local prefixes that must never appear in egress.
_PRIVATE_HOST_PATTERNS = re.compile(
    r"^(10\.|172\.(1[6-9]|2\d|3[01])\.|192\.168\.|127\.|0\.|169\.254\.|localhost$)"
)
_GOOGLE_REGION = re.compile(r"^[a-z][a-z0-9-]{0,31}$")


def _validate_egress_domains(domains: list[str]) -> None:
    """Reject egress lists exceeding cap or containing private/invalid hosts."""
    if len(domains) > _MAX_EGRESS_DOMAINS:
        raise HostedHarnessError(
            "egress_domain_limit_exceeded",
            f"at most {_MAX_EGRESS_DOMAINS} egress domains allowed, got {len(domains)}",
            status_code=400,
        )
    for domain in domains:
        if _PRIVATE_HOST_PATTERNS.match(domain):
            raise HostedHarnessError(
                "egress_domain_private",
                f"private/reserved host {domain!r} is not allowed in egress",
                status_code=400,
            )


def _validate_resolved_egress_domains(domains: set[str]) -> None:
    """Enforce Daytona's cap after platform, provider, and customer hosts are combined."""
    if len(domains) > _MAX_EGRESS_DOMAINS:
        raise HostedHarnessError(
            "egress_domain_limit_exceeded",
            "resolved sandbox egress requires "
            f"{len(domains)} domains; Daytona supports at most {_MAX_EGRESS_DOMAINS}",
            status_code=400,
        )


def _provider_egress_domains(secrets_map: dict[str, str]) -> set[str]:
    """Return the minimum provider hosts implied by run-scoped credentials.

    These are platform-managed dependencies of the selected credential type, not customer
    requested egress. In particular, Vertex service-account credentials cannot work unless the
    OAuth token endpoint and regional Vertex endpoint are reachable.
    """
    aliases = {name.upper() for name in secrets_map}
    domains: set[str] = set()
    if aliases & {
        "GOOGLE_APPLICATION_CREDENTIALS_JSON",
        "GOOGLE_APPLICATION_CREDENTIALS",
    }:
        domains.update(
            {
                "oauth2.googleapis.com",
                "us-east5-aiplatform.googleapis.com",
                "aiplatform.googleapis.com",
            }
        )
        for name in ("GOOGLE_CLOUD_LOCATION", "CLOUD_ML_REGION"):
            region = str(secrets_map.get(name) or "").strip().lower()
            if _GOOGLE_REGION.fullmatch(region):
                domains.add(f"{region}-aiplatform.googleapis.com")
    if aliases & {"GEMINI_API_KEY", "GOOGLE_API_KEY"}:
        domains.add("generativelanguage.googleapis.com")
    return domains


def _persist_bundle_stage_outputs(
    job: HostedHarnessJob,
    manifest: dict,
) -> None:
    """Persist authoritative contract/environment/scenario snapshots from verified bundle."""
    from simulate.models import HostedHarnessStageOutput

    existing = set(
        HostedHarnessStageOutput.no_workspace_objects.filter(job=job).values_list(
            "kind", flat=True
        )
    )
    outputs: list[HostedHarnessStageOutput] = []
    # Contract stage output — from contract.json referenced in manifest.files.
    if "contract" not in existing:
        contract_data = _load_bundle_file(manifest, "contract.json", job)
        if contract_data is not None:
            outputs.append(
                HostedHarnessStageOutput(
                    job=job,
                    title="Contract",
                    summary=contract_data.get("one_liner", "")
                    if isinstance(contract_data, dict)
                    else "",
                    kind="contract",
                    data=contract_data,
                )
            )
    # Environment stage output — bundle runtime/capabilities/seed.
    if "environment" not in existing:
        processes = manifest.get("processes") or []
        capabilities = manifest.get("capabilities") or {}
        outputs.append(
            HostedHarnessStageOutput(
                job=job,
                title="Environment",
                summary=manifest.get("name", ""),
                kind="environment",
                data={
                    "services": [
                        process.get("name")
                        for process in processes
                        if isinstance(process, dict) and process.get("name")
                    ],
                    "project": manifest.get("name", ""),
                    "managed": True,
                    "overrides": {
                        capability.get("configuration_name", slug): capability.get(
                            "protocol", ""
                        )
                        for slug, capability in capabilities.items()
                        if isinstance(capability, dict)
                    },
                    "runtime": manifest.get("runtime"),
                    "processes": processes,
                    "capabilities": capabilities,
                    "seed": manifest.get("seed"),
                    "readiness": manifest.get("readiness"),
                },
            )
        )
    # Scenarios stage output — from scenarios/ directories.
    if "scenarios" not in existing:
        scenario_docs = _load_bundle_scenarios(manifest, job)
        if scenario_docs:
            scenario_data = [
                {
                    **doc,
                    "name": doc.get("name") or doc.get("scenario_key"),
                    "use_case": doc.get("use_case") or doc.get("tests") or "",
                }
                for doc in scenario_docs
            ]
            outputs.append(
                HostedHarnessStageOutput(
                    job=job,
                    title="Scenarios",
                    summary=f"{len(scenario_data)} pre-authored scenarios",
                    kind="scenarios",
                    data=scenario_data,
                )
            )
    if outputs:
        HostedHarnessStageOutput.no_workspace_objects.bulk_create(outputs)


def _load_bundle_file(manifest: dict, relative_path: str, job: HostedHarnessJob):
    """Load a JSON file from the resolved bundle directory."""
    base = getattr(settings, "ALK_HOSTED_BUNDLE_DIR", "")
    if not base:
        return None
    source = (job.payload or {}).get("source") or {}
    repo = source.get("repository") or ""
    if not repo:
        return None
    file_path = Path(base) / repo.replace("/", "__") / relative_path
    if not file_path.is_file():
        return None
    return json.loads(file_path.read_text(encoding="utf-8"))


def _load_bundle_scenarios(manifest: dict, job: HostedHarnessJob) -> list[dict]:
    """Load scenario.json files from bundle directories."""
    base = getattr(settings, "ALK_HOSTED_BUNDLE_DIR", "")
    if not base:
        return []
    source = (job.payload or {}).get("source") or {}
    repo = source.get("repository") or ""
    if not repo:
        return []
    bundle_dir = Path(base) / repo.replace("/", "__")
    scenarios_dir = bundle_dir / "scenarios"
    if not scenarios_dir.is_dir():
        return []
    results = []
    for scenario_path in sorted(scenarios_dir.iterdir()):
        scenario_json = scenario_path / "scenario.json"
        if scenario_json.is_file():
            results.append(json.loads(scenario_json.read_text(encoding="utf-8")))
    return results


class DaytonaHostedGateway:
    def __init__(self) -> None:
        from daytona import Daytona, DaytonaConfig

        api_key = getattr(settings, "DAYTONA_API_KEY", "")
        snapshot = getattr(settings, "ALK_DAYTONA_SNAPSHOT", "")
        dockerfile = getattr(settings, "ALK_DAYTONA_DOCKERFILE", "")
        if not api_key or not (snapshot or dockerfile):
            raise HostedHarnessError(
                "daytona_not_configured",
                "DAYTONA_API_KEY and either ALK_DAYTONA_SNAPSHOT or "
                "ALK_DAYTONA_DOCKERFILE are required",
                status_code=503,
                retryable=True,
            )
        self.snapshot = snapshot
        self.dockerfile = dockerfile
        self.snapshot_digest = getattr(settings, "ALK_DAYTONA_SNAPSHOT_DIGEST", "")
        self.client = Daytona(
            DaytonaConfig(
                api_key=api_key,
                api_url=getattr(settings, "DAYTONA_API_URL", None),
                target=getattr(settings, "DAYTONA_TARGET", None),
                organization_id=getattr(settings, "DAYTONA_ORGANIZATION_ID", None),
            )
        )

    def launch(
        self, job: HostedHarnessJob, *, endpoint_base_url: str
    ) -> HostedHarnessAttempt:
        from daytona import (
            CreateSandboxFromImageParams,
            CreateSandboxFromSnapshotParams,
            Image,
            Resources,
            SessionExecuteRequest,
        )

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
        # Bundle authoring is performed inside the sandbox. The platform sends source plus any
        # frozen authoring inputs; it does not select or execute a host-side bundle.
        secrets_map = PlatformSecretResolver().resolve(job)
        dispatch_payload = prepare_dispatch_payload(payload, secrets_map)
        capability = register_attempt(
            job.id,
            endpoint_base_url=endpoint_base_url,
            snapshot_name=self.snapshot or "direct-image",
            snapshot_digest=(self.snapshot_digest or None) if self.snapshot else None,
        )
        attempt = capability.attempt
        # Record provenance digests on the attempt.
        if commit_sha:
            attempt.source_digest = (
                f"sha256:{commit_sha}" if len(commit_sha) == 64 else commit_sha
            )
        attempt.save(update_fields=["source_digest", "bundle_digest", "updated_at"])
        authoring_seconds = max(
            0,
            int(
                getattr(
                    settings,
                    "ALK_HOSTED_AUTHORING_MAX_DURATION_SECONDS",
                    3600,
                )
            ),
        )
        ttl_seconds = max(
            int(getattr(settings, "ALK_HOSTED_SANDBOX_TTL_SECONDS", 7200)),
            authoring_seconds + payload["runtime"]["max_duration_seconds"] + 120,
        )
        ttl_minutes = max(1, (ttl_seconds + 59) // 60)
        platform_host = urlparse(endpoint_base_url).hostname
        allowed_domains = set(getattr(settings, "ALK_HOSTED_BASE_EGRESS_DOMAINS", []))
        allowed_domains.update(_provider_egress_domains(secrets_map))
        allowed_domains.update(payload["security"]["allowed_egress_domains"])
        if platform_host:
            allowed_domains.add(platform_host)
        # Egress union validation: cap at 20 user-supplied domains.
        _validate_egress_domains(payload["security"]["allowed_egress_domains"])
        _validate_resolved_egress_domains(allowed_domains)
        # Voice/WebRTC media (ICE) needs UDP to the media server's advertised IP, which a DNS
        # domain-allowlist cannot express when media and signaling resolve to different IPs. When
        # unrestricted egress is enabled the sandbox runs with open outbound so media can flow;
        # otherwise the domain allowlist (block-all + allowlist) applies.
        unrestricted = bool(getattr(settings, "ALK_HOSTED_EGRESS_UNRESTRICTED", False))
        network_block_all = False if unrestricted else (not allowed_domains)
        domain_allow_list = (
            None if unrestricted else (",".join(sorted(allowed_domains)) or None)
        )
        sandbox = None
        try:
            common_params = {
                "language": "python",
                "os_user": getattr(
                    settings, "ALK_HOSTED_SANDBOX_OS_USER", "svc-control"
                ),
                "labels": {
                    "futureagi.job": str(job.id),
                    "futureagi.attempt": str(attempt.id),
                },
                "network_block_all": network_block_all,
                "domain_allow_list": domain_allow_list,
                "ephemeral": True,
                "ttl_minutes": ttl_minutes,
                "auto_delete_interval": ttl_minutes,
            }
            dockerfile = getattr(self, "dockerfile", "")
            if dockerfile:
                launch_params = CreateSandboxFromImageParams(
                    image=Image.from_dockerfile(dockerfile),
                    resources=Resources(
                        cpu=payload["runtime"]["cpu_units"],
                        memory=max(4, (payload["runtime"]["memory_mb"] + 1023) // 1024),
                        disk=10,
                    ),
                    **common_params,
                )
                launch_timeout = 1200
            else:
                launch_params = CreateSandboxFromSnapshotParams(
                    snapshot=self.snapshot,
                    **common_params,
                )
                launch_timeout = 300
            sandbox = self.client.create(
                launch_params,
                timeout=launch_timeout,
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
                "if [ -f /work/authoring.tar.gz ]; then "
                "mkdir -p /work/authoring && "
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
            sandbox.process.create_session(_ENTRYPOINT_SESSION)
            command = sandbox.process.execute_session_command(
                _ENTRYPOINT_SESSION,
                SessionExecuteRequest(
                    command=(
                        "if [ ! -f /work/authoring/contract.json ]; then "
                        "python -m fi.alk.harness.hosted_authoring_entrypoint "
                        "/work/job.json --source /work/source --output /work/authoring; "
                        "fi && "
                        "python -m fi.alk.harness.bundle_author_v2 "
                        "--job /work/job.json --source /work/source "
                        "--authoring /work/authoring --output /work/bundle && "
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
        self._sync_authoring_progress(attempt, sandbox)
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
            # The agent/tools-api/postgres run as CHILD processes; their stdout/stderr goes to
            # per-world/build process.log files, never the entrypoint's own stream. Collect their
            # tails too -- an "agent did not become ready" failure is only diagnosable from the
            # agent's own log (STT/LLM/TTS init), which the entrypoint stream never sees.
            try:
                child = sandbox.process.exec(
                    "for f in $(find /work/worlds /work/build -name '*.log' 2>/dev/null | sort); do "
                    'echo "===== $f ====="; tail -120 "$f"; done',
                    timeout=60,
                )
                child_logs = getattr(child, "result", "") or ""
            except Exception:  # noqa: BLE001
                child_logs = ""
            observation["process_logs"] = child_logs[-16000:]
            # Surface everything to the simulation-runner worker log so failures are visible via
            # `docker logs temporal-worker-simulation-runner`, not only the truncated receipt tail.
            logger.info(
                "hosted guest attempt=%s exit_code=%s\n--- entrypoint ---\n%s\n--- processes ---\n%s",
                attempt.id,
                command.exit_code,
                observation["logs"],
                observation["process_logs"],
            )
        return observation

    @staticmethod
    def _sync_authoring_progress(attempt: HostedHarnessAttempt, sandbox) -> None:
        """Expose safe in-sandbox authoring outputs while the unified command is running."""

        def _json(path: str):
            try:
                return json.loads(sandbox.fs.download_file(path).decode("utf-8"))
            except Exception:  # noqa: BLE001 - an absent/incomplete stage file is expected
                return None

        contract = _json("/work/authoring/contract.json")
        environment = _json("/work/authoring/environment.json")
        scenarios = _json("/work/authoring/scenarios.json")
        outputs: list[dict[str, Any]] = []
        stage = "understanding_agent"
        if isinstance(contract, dict):
            tools = [
                {"name": str(item.get("name") or "")}
                for item in contract.get("tools", [])
                if isinstance(item, dict) and item.get("name")
            ]
            outputs.append(
                {
                    "id": "contract",
                    "kind": "contract",
                    "title": "Agent contract",
                    "summary": f"{len(tools)} tools · {contract.get('modality') or 'unknown'} modality",
                    "data": {
                        "one_liner": str(contract.get("one_liner") or ""),
                        "modality": str(contract.get("modality") or "unknown"),
                        "runtime": contract.get("runtime") or {},
                        "tools": tools,
                    },
                }
            )
            stage = "generating_environment"
        if isinstance(environment, dict):
            services = [str(item) for item in environment.get("services", [])]
            outputs.append(
                {
                    "id": "environment",
                    "kind": "environment",
                    "title": "Execution environment",
                    "summary": f"{len(services)} services described",
                    "data": {
                        "services": services,
                        "project": str(environment.get("project") or ""),
                        "managed": bool(environment.get("managed")),
                    },
                }
            )
            stage = "generating_scenarios"
        if isinstance(scenarios, list):
            scenario_data = [
                {
                    "name": str(item.get("name") or "scenario"),
                    "instruction": str(item.get("instruction") or ""),
                    "use_case": str(item.get("use_case") or ""),
                }
                for item in scenarios
                if isinstance(item, dict)
            ]
            outputs.append(
                {
                    "id": "scenarios",
                    "kind": "scenarios",
                    "title": "Generated scenarios",
                    "summary": f"{len(scenario_data)} grounded scenarios",
                    "data": scenario_data,
                }
            )
            stage = "validating_environment"
        if not outputs:
            return
        job = HostedHarnessJob.no_workspace_objects.get(id=attempt.job_id)
        # Once the guest event channel advances into runtime stages it is authoritative.
        if job.current_stage in {
            "queued",
            "admitted",
            "understanding_agent",
            "generating_environment",
            "generating_scenarios",
        }:
            job.current_stage = stage
        job.stage_outputs = outputs
        job.save(update_fields=["current_stage", "stage_outputs", "updated_at"])

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

        # Event/manifest ingestion runs independently from the provider poll.  The attempt object
        # held by the workflow may predate the terminal HTTP requests even though both commits are
        # already visible in the database by the time Daytona reports process exit.  Refresh the
        # delivery fields before classifying exit 0, or a fully acknowledged run is overwritten
        # with the false `terminal_delivery_incomplete` platform failure.
        attempt.refresh_from_db(
            fields=[
                "terminal_event_received",
                "manifest_acked",
                "terminal_stage",
                "terminal_reason",
                "terminal_failure",
                "state",
                "updated_at",
            ]
        )
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
                "details": {
                    "guest_log_tail": observation.get("logs", ""),
                    "process_logs": observation.get("process_logs", ""),
                },
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
                "details": {
                    "guest_log_tail": observation.get("logs", ""),
                    "process_logs": observation.get("process_logs", ""),
                },
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
                "details": {
                    "guest_log_tail": observation.get("logs", ""),
                    "process_logs": observation.get("process_logs", ""),
                },
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


def _bundle_archive_for(job: HostedHarnessJob) -> tuple[bytes | None, dict | None]:
    """Resolve and verify a pre-authored environment-bundle.v2 for this job's source.

    Returns (tar.gz bytes, manifest dict) or (None, None). Verifies:
    - Bundle presence (manifest.json exists)
    - provenance.repository matches source.repository
    - provenance.commit matches source.commit_sha (if provided)
    - file hashes in manifest match actual files
    """
    import hashlib as _hashlib

    base = getattr(settings, "ALK_HOSTED_BUNDLE_DIR", "")
    if not base:
        return None, None
    source = (job.payload or {}).get("source") or {}
    repo = source.get("repository") or ""
    if not repo:
        return None, None
    bundle_dir = Path(base) / repo.replace("/", "__")
    manifest_path = bundle_dir / "manifest.json"
    if not manifest_path.is_file():
        return None, None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    # Verify provenance
    provenance = manifest.get("provenance") or {}
    prov_repo = provenance.get("repository") or ""
    if prov_repo and prov_repo != repo:
        raise HostedHarnessError(
            "bundle_provenance_repository_mismatch",
            f"bundle provenance repository {prov_repo!r} does not match "
            f"source repository {repo!r}",
            status_code=409,
        )
    prov_commit = provenance.get("commit") or ""
    source_commit = source.get("commit_sha") or ""
    if prov_commit and source_commit and prov_commit != source_commit:
        raise HostedHarnessError(
            "bundle_provenance_commit_mismatch",
            f"bundle provenance commit {prov_commit[:12]} does not match "
            f"source commit {source_commit[:12]}",
            status_code=409,
        )
    # Verify file hashes
    for entry in manifest.get("files") or []:
        file_path = bundle_dir / entry["path"]
        if not file_path.is_file():
            raise HostedHarnessError(
                "bundle_file_missing",
                f"bundle file {entry['path']} declared in manifest is missing",
                status_code=422,
            )
        actual_hash = _hashlib.sha256(file_path.read_bytes()).hexdigest()
        if actual_hash != entry["sha256"]:
            raise HostedHarnessError(
                "bundle_file_hash_mismatch",
                f"bundle file {entry['path']} hash mismatch",
                status_code=409,
            )
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        for path in sorted(bundle_dir.rglob("*")):
            if path.is_file():
                tar.add(path, arcname=path.relative_to(bundle_dir).as_posix())
    return archive.getvalue(), manifest


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
