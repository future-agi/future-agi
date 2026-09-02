from __future__ import annotations

import io
import json
import logging
import os
import re
import shlex
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
from django.db import transaction
from django.utils import timezone
from tfc.settings.settings import UPLOAD_BUCKET_NAME
from tfc.utils.storage_client import ensure_bucket, get_storage_client

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
_SCENARIO_DIRECTORY_COUNT_COMMAND = (
    "find /work/authoring/scenarios -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l"
)
_ADJUSTMENTS_PATH = "/run/futureagi/adjustments.jsonl"
_ADJUSTMENT_STATUS_PATH = "/run/futureagi/adjustment-status.jsonl"
_DIRECT_IMAGE_WITH_ADJUSTMENTS = "direct-image-adjustments-v1"
_SIMULATOR_SECRETS_PATH = "/run/futureagi/simulator-secrets.json"
_SIMULATOR_VERTEX_CREDENTIALS_PATH = "/run/futureagi/simulator-vertex-sa.json"


def _platform_simulator_material() -> tuple[dict[str, str], bytes | None]:
    """Return control-process-only simulator config and optional Vertex ADC bytes.

    These values come from platform deployment configuration, never the customer request.  The
    returned map is uploaded on its own channel and consumed by ALK's hosted entrypoint; it is not
    added to ``secrets.json`` and therefore cannot acquire the ``target_provider`` purpose or be
    injected into an untrusted agent process.
    """
    source_path = str(
        os.environ.get("ALK_HOSTED_SIMULATOR_GOOGLE_APPLICATION_CREDENTIALS")
        or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        or ""
    ).strip()
    credential_bytes: bytes | None = None
    project = str(os.environ.get("GOOGLE_CLOUD_PROJECT") or "").strip()
    if source_path:
        try:
            credential_bytes = Path(source_path).read_bytes()
            document = json.loads(credential_bytes)
            if not project and isinstance(document, dict):
                project = str(document.get("project_id") or "").strip()
        except (OSError, ValueError, TypeError) as exc:
            raise HostedHarnessError(
                "simulator_credentials_invalid",
                "the platform simulator Vertex credential could not be loaded",
                status_code=503,
            ) from exc

    provider = str(os.environ.get("SIMULATOR_LLM_PROVIDER") or "vertex").strip()
    model = str(
        os.environ.get("SIMULATOR_LLM_MODEL") or "gemini-2.5-flash"
    ).strip()
    location = str(os.environ.get("GOOGLE_CLOUD_LOCATION") or "global").strip()
    derived_backend = (
        "vertex-gemini"
        if provider.lower() in {"google", "vertex", "vertex-gemini", "vertex_gemini"}
        else provider
    )
    # Authoring and simulation are separate trust/runtime lanes.  Let deployment config select
    # the ALK stage-loop independently instead of forcing it to use the simulated caller's
    # provider and model.  This also keeps the customer request unable to influence either one.
    backend = str(os.environ.get("ALK_HARNESS") or derived_backend).strip()
    authoring_model = str(os.environ.get("ALK_HARNESS_MODEL") or model).strip()
    values = {
        "ALK_HARNESS": backend,
        "ALK_HARNESS_MODEL": authoring_model,
        "ALK_VERTEX_LOCATION": location,
        "GOOGLE_CLOUD_LOCATION": location,
        "GOOGLE_GENAI_USE_VERTEXAI": str(
            os.environ.get("GOOGLE_GENAI_USE_VERTEXAI") or "True"
        ),
        "SIMULATOR_LLM_PROVIDER": provider,
        "SIMULATOR_LLM_MODEL": model,
    }
    for name in (
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
        "CARTESIA_API_KEY",
        "DEEPGRAM_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "OPENAI_API_KEY",
        "SIMULATOR_STT_MODEL",
        "SIMULATOR_STT_PROVIDER",
        "SIMULATOR_TTS_MODEL",
        "SIMULATOR_TTS_PROVIDER",
    ):
        value = str(os.environ.get(name) or "").strip()
        if value:
            values[name] = value
    if project:
        values["GOOGLE_CLOUD_PROJECT"] = project
    if credential_bytes is not None:
        values["GOOGLE_APPLICATION_CREDENTIALS"] = (
            _SIMULATOR_VERTEX_CREDENTIALS_PATH
        )
    return values, credential_bytes


def _scenario_delta(instruction: str) -> int | None:
    number_words = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
    }
    match = re.search(
        r"\b(?:add|create|generate|write)\s+"
        r"(\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
        r"(?:more\s+)?scenarios?\b",
        instruction,
        re.IGNORECASE,
    )
    if match is None:
        return None
    value = match.group(1).lower()
    return min(100, int(value) if value.isdigit() else number_words[value])


def _adjustment_stage(instruction: str, current_stage: str) -> str:
    lowered = instruction.lower()
    if any(word in lowered for word in ("scenario", "persona", "test case")):
        return "scenarios"
    if any(
        word in lowered
        for word in ("environment", "database", "service", "seed", "test data")
    ):
        return "environment"
    if any(word in lowered for word in ("contract", "tool", "capability")):
        return "understand"
    return {
        "understanding_agent": "understand",
        "generating_environment": "environment",
        "building_environment": "environment",
        "validating_environment": "environment",
        "generating_scenarios": "scenarios",
        "validating_scenarios": "scenarios",
    }.get(current_stage, "scenarios")


def _hosted_scenario_repair_script(*, name: str, expected: int, actual: int) -> str:
    """Build the non-interactive scenario-only repair run used by pinned guests.

    Keeping this in the gateway lets the control plane repair an older hosted snapshot without
    weakening Bundle V2's exact-cardinality gate or requiring a privileged in-sandbox updater.
    Values are encoded as JSON literals rather than interpolated as executable source.
    """
    delta = expected - actual
    if delta > 0:
        instruction = (
            f"Exactly {expected} scenarios are required, but {actual} are saved. "
            f"Add exactly {delta} distinct validated scenario(s), preserve the existing "
            "scenarios, and call save_scenarios."
        )
    else:
        instruction = (
            f"Exactly {expected} scenarios are required, but {actual} are saved. "
            f"Remove exactly {-delta} excess scenario(s), preserve the strongest coverage, "
            "and call save_scenarios."
        )
    return (
        "import argparse, asyncio\n"
        "from fi.alk.harness.cli import _scenarios\n"
        "args = argparse.Namespace("
        f"name={json.dumps(name)}, out='/work/authoring', count={expected}, "
        "interactive=False, "
        f"guidance=[{json.dumps(instruction)}])\n"
        "raise SystemExit(asyncio.run(_scenarios(args)))\n"
    )


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


def resolve_platform_simulator_secrets() -> dict[str, str]:
    """Resolve Future AGI-owned simulator credentials from process configuration.

    This deliberately has no job/request argument: callers cannot select, replace, or observe
    these values through the hosted API. The namespaced aliases prevent an agent's own provider
    key from colliding with the simulator provider key for the same vendor.
    """
    resolved: dict[str, str] = {}
    configured = getattr(settings, "ALK_HOSTED_SIMULATOR_SECRET_ENV", {})
    for alias, env_name in configured.items():
        value = str(os.getenv(str(env_name), "") or "")
        if value:
            resolved[str(alias)] = value

    adc_alias = "SIMULATOR_GOOGLE_APPLICATION_CREDENTIALS_JSON"
    if adc_alias in configured and not resolved.get(adc_alias):
        adc_path = str(os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "") or "")
        if adc_path:
            try:
                resolved[adc_alias] = Path(adc_path).read_text(encoding="utf-8")
            except OSError as exc:
                raise HostedHarnessError(
                    "simulator_credentials_unavailable",
                    "configured platform Google credentials cannot be read",
                    status_code=500,
                ) from exc
    return resolved


def attach_platform_simulator_secret_refs(
    payload: dict[str, Any], simulator_secrets: dict[str, str]
) -> dict[str, Any]:
    """Add value-free, internal refs only to the ephemeral sandbox job document."""
    dispatched = dict(payload)
    agent = dict(dispatched.get("agent") or {})
    refs = dict(agent.get("secret_refs") or {})
    for alias in simulator_secrets:
        refs[alias] = {
            "manager": "platform-config",
            "key": alias,
            "purpose": "simulator_provider",
        }
    agent["secret_refs"] = refs
    dispatched["agent"] = agent
    return dispatched


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
    aliases = {name.upper().removeprefix("SIMULATOR_") for name in secrets_map}
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
        for name in (
            "GOOGLE_CLOUD_LOCATION",
            "CLOUD_ML_REGION",
            "SIMULATOR_GOOGLE_CLOUD_LOCATION",
            "SIMULATOR_CLOUD_ML_REGION",
        ):
            region = str(secrets_map.get(name) or "").strip().lower()
            if _GOOGLE_REGION.fullmatch(region):
                domains.add(f"{region}-aiplatform.googleapis.com")
    if aliases & {"GEMINI_API_KEY", "GOOGLE_API_KEY"}:
        domains.add("generativelanguage.googleapis.com")
    if "VAPI_API_KEY" in aliases:
        # Both repository-owned lifecycle commands and the direct websocket caller use Vapi's
        # public API. Vapi's documented websocket URL is also hosted on api.vapi.ai.
        domains.add("api.vapi.ai")
    if "RETELL_API_KEY" in aliases:
        # Retell web calls are created through its API, then bridged through Retell's managed
        # LiveKit deployment. Keep these provider-owned hosts derived from the credential type
        # instead of asking customers to understand Daytona's network policy.
        domains.update(
            {
                "api.retellai.com",
                "*.livekit.cloud",
                "*.turn.livekit.cloud",
            }
        )
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

    def author(self, job: HostedHarnessJob) -> bytes:
        """Run ALK authoring (understand -> environment -> scenarios) inside a Daytona sandbox.

        Scenario generation is LLM-heavy and must run off the control-plane worker. This provisions
        a throwaway sandbox with only the authoring model credentials (never the target call
        secrets), runs the same ``authoring_entrypoint`` the local SDK uses, and returns the packed
        frozen authoring archive that ``bundle_author_v2`` later seals inside the execution sandbox.
        """
        from daytona import (
            CreateSandboxFromImageParams,
            CreateSandboxFromSnapshotParams,
            Image,
            Resources,
        )

        source_archive, commit_sha = HostedSourceAcquirer().acquire(job)
        payload = dict(job.payload)
        source = dict(payload["source"])
        if source["kind"] == "github":
            source["commit_sha"] = commit_sha
            payload["source"] = source
        if payload != job.payload:
            job.payload = payload
            job.save(update_fields=["payload", "updated_at"])

        # An imported provider target is part of the agent source of truth. Give the isolated
        # authoring control process only the one provider credential it needs to fetch a
        # read-only, sanitized behavioral profile. It is uploaded as a one-shot file rather than
        # put in the model environment, and the guest deletes it before any model/source process
        # starts.
        authoring_target_secrets, connector = _provider_import_authoring_material(
            job, payload
        )
        logger.info(
            "hosted authoring provider profile job=%s connector=%s credential=%s",
            job.id,
            connector or "none",
            "available" if authoring_target_secrets else "unavailable",
        )

        # Authoring reaches only the model provider and the source host - never the target
        # (LiveKit/Deepgram) media secrets, which belong to the execution sandbox alone.
        simulator_env, simulator_vertex_credentials = _platform_simulator_material()
        project_id = str(simulator_env.get("GOOGLE_CLOUD_PROJECT") or "")

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
        if authoring_target_secrets:
            provider_domain = {
                "vapi": "api.vapi.ai",
                "retell": "api.retellai.com",
            }.get(connector)
            if provider_domain and provider_domain not in allowed_domains:
                allowed_domains = [provider_domain, *allowed_domains][:20]
        authoring_env = {
            **{
                name: value
                for name, value in simulator_env.items()
                if name
                not in {"LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"}
            },
            "CLAUDE_CODE_USE_VERTEX": "1",
            "GOOGLE_GENAI_USE_VERTEXAI": "True",
            "CLOUD_ML_REGION": getattr(
                settings, "ALK_HOSTED_AUTHORING_CLAUDE_REGION", "us-east5"
            ),
            "GOOGLE_CLOUD_LOCATION": getattr(
                settings, "ALK_HOSTED_AUTHORING_GEMINI_LOCATION", "us-central1"
            ),
            "GOOGLE_APPLICATION_CREDENTIALS": _SIMULATOR_VERTEX_CREDENTIALS_PATH,
        }
        if project_id:
            authoring_env["GOOGLE_CLOUD_PROJECT"] = project_id
            authoring_env["ANTHROPIC_VERTEX_PROJECT_ID"] = project_id

        ttl_minutes = int(getattr(settings, "ALK_HOSTED_AUTHORING_TTL_MINUTES", 40))
        sandbox = None
        try:
            common_params = {
                "language": "python",
                "os_user": getattr(
                    settings, "ALK_HOSTED_SANDBOX_OS_USER", "svc-control"
                ),
                "labels": {
                    "futureagi.job": str(job.id),
                    "futureagi.authoring": "1",
                },
                "network_block_all": not allowed_domains,
                "domain_allow_list": ",".join(sorted(allowed_domains)) or None,
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
                        memory=max(
                            4,
                            (payload["runtime"]["memory_mb"] + 1023) // 1024,
                        ),
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
            sandbox = self.client.create(launch_params, timeout=launch_timeout)
            sandbox.fs.upload_file(source_archive, "/work/source.tar.gz")
            sandbox.fs.upload_file(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
                "/work/job.json",
            )
            if simulator_vertex_credentials is not None:
                sandbox.fs.upload_file(
                    simulator_vertex_credentials,
                    _SIMULATOR_VERTEX_CREDENTIALS_PATH,
                )
            authoring_secrets_arg = ""
            if authoring_target_secrets:
                authoring_secrets_path = "/run/futureagi/authoring-target-secrets.json"
                authoring_secrets_arg = (
                    f" --target-secrets {authoring_secrets_path}"
                    " --provider-profile-cache /work/provider-import-profile.json"
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
            # retry clears it. Early drops fail fast, so retries stay cheap. A zero exit code is
            # not enough: the scenario stage can checkpoint a valid partial suite (for example
            # 2/3). Repair that suite in place before it becomes the frozen archive consumed by
            # Bundle V2.
            attempts = max(
                1, int(getattr(settings, "ALK_HOSTED_AUTHORING_ATTEMPTS", 3))
            )
            run_timeout = int(getattr(settings, "ALK_HOSTED_AUTHORING_TIMEOUT", 1500))
            detail = "authoring produced no scenarios"
            for _ in range(attempts):
                if authoring_target_secrets:
                    # The guest consumes and deletes this credential before invoking the model.
                    # If inspection itself fails, authoring retries need a fresh one-shot copy.
                    # Once the sanitized cache exists, never upload the credential again: this
                    # keeps it out of subsequent model sessions and out of the frozen archive.
                    cached_profile = sandbox.process.exec(
                        "test -f /work/provider-import-profile.json", timeout=30
                    )
                    if cached_profile.exit_code:
                        sandbox.fs.upload_file(
                            json.dumps(
                                authoring_target_secrets,
                                sort_keys=True,
                                separators=(",", ":"),
                            ).encode(),
                            authoring_secrets_path,
                        )
                        protected = sandbox.process.exec(
                            f"chmod 600 {authoring_secrets_path}", timeout=30
                        )
                        if protected.exit_code:
                            raise HostedHarnessError(
                                "authoring_secret_prepare_failed",
                                str(protected.result or "")[-500:],
                                status_code=502,
                                retryable=True,
                            )
                run = sandbox.process.exec(
                    "rm -rf /work/authoring && "
                    "python -m fi.alk.harness.authoring_entrypoint /work/job.json "
                    "--source /work/source --output /work/authoring"
                    + authoring_secrets_arg,
                    env=authoring_env,
                    timeout=run_timeout,
                )
                check = sandbox.process.exec(
                    _SCENARIO_DIRECTORY_COUNT_COMMAND,
                    timeout=30,
                )
                try:
                    produced = int(str(check.result or "0").strip() or "0")
                except ValueError:
                    produced = 0
                if run.exit_code == 0 and produced == job.scenario_count:
                    break
                if run.exit_code == 0 and produced > 0:
                    for repair_attempt in range(1, 3):
                        script = _hosted_scenario_repair_script(
                            name=str(
                                job.metadata.get("agent_name")
                                or job.payload.get("name")
                                or "agent"
                            ),
                            expected=job.scenario_count,
                            actual=produced,
                        )
                        sandbox.fs.upload_file(
                            script.encode("utf-8"), "/tmp/repair-scenarios.py"
                        )
                        repair = sandbox.process.exec(
                            "python /tmp/repair-scenarios.py",
                            env=authoring_env,
                            timeout=run_timeout,
                        )
                        check = sandbox.process.exec(
                            _SCENARIO_DIRECTORY_COUNT_COMMAND,
                            timeout=30,
                        )
                        try:
                            produced = int(str(check.result or "0").strip() or "0")
                        except ValueError:
                            produced = 0
                        logger.info(
                            "hosted authoring scenario repair job=%s attempt=%s "
                            "expected=%s produced=%s exit=%s",
                            job.id,
                            repair_attempt,
                            job.scenario_count,
                            produced,
                            repair.exit_code,
                        )
                        if repair.exit_code == 0 and produced == job.scenario_count:
                            break
                    if produced == job.scenario_count:
                        break
                    detail = (
                        "scenario_count_mismatch: requested "
                        f"{job.scenario_count}, produced {produced} after repair"
                    )
                else:
                    detail = str(run.result or detail)[-1000:]
            else:
                raise HostedHarnessError(
                    "authoring_failed", detail, status_code=422, retryable=False
                )
            packed = sandbox.process.exec(
                "cd /work/authoring && tar -czf /tmp/authoring.tar.gz "
                "$(ls -d world.sqlite schema.sql store.json collections.json "
                "contract.json environment.json provider-import-profile.json "
                "scenarios.json simulator_prompt.md "
                "scenarios handlers 2>/dev/null)",
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
        simulator_env, simulator_vertex_credentials = _platform_simulator_material()
        authoring_target_secrets, _authoring_connector = (
            _provider_import_authoring_material(job, payload)
            if authoring_archive is None
            else ({}, "")
        )
        dispatch_payload = prepare_dispatch_payload(
            payload, secrets_map, simulator_secrets=simulator_env
        )
        capability = register_attempt(
            job.id,
            endpoint_base_url=endpoint_base_url,
            snapshot_name=(
                _DIRECT_IMAGE_WITH_ADJUSTMENTS
                if getattr(self, "dockerfile", "")
                else self.snapshot
            ),
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
        allowed_domains.update(_provider_egress_domains(simulator_env))
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
            authoring_secrets_path = "/run/futureagi/authoring-target-secrets.json"
            if authoring_target_secrets:
                # Imported provider configuration is source material for authoring. Give the
                # control process only the matching provider key in a one-shot file; ALK removes
                # it before any model or source process starts. The normal target secret remains
                # separately available to the eventual provider lifecycle process.
                sandbox.fs.upload_file(
                    json.dumps(
                        authoring_target_secrets,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode(),
                    authoring_secrets_path,
                )
            sandbox.fs.upload_file(
                json.dumps(
                    simulator_env, sort_keys=True, separators=(",", ":")
                ).encode(),
                _SIMULATOR_SECRETS_PATH,
            )
            if simulator_vertex_credentials is not None:
                sandbox.fs.upload_file(
                    simulator_vertex_credentials,
                    _SIMULATOR_VERTEX_CREDENTIALS_PATH,
                )
            sandbox.fs.upload_file(
                json.dumps(
                    capability.document, sort_keys=True, separators=(",", ":")
                ).encode(),
                "/run/futureagi/capabilities.json",
            )
            # Adjustments are durable job inputs, not attempt-local state.  A retry starts a
            # fresh sandbox after the previous attempt has gone away, so seed its inbox from the
            # persisted payload before authoring begins.  Without this, the durable
            # ``scenario_count`` includes an applied chat delta while the retried guest authors
            # the original number of scenarios, and platform pre-allocation correctly rejects
            # the cardinality mismatch.
            adjustments = list(
                (dispatch_payload.get("metadata") or {}).get("adjustments") or []
            )
            if adjustments:
                adjustment_body = "".join(
                    json.dumps(item, separators=(",", ":")) + "\n"
                    for item in adjustments
                ).encode("utf-8")
                sandbox.fs.upload_file(adjustment_body, _ADJUSTMENTS_PATH)
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
                "/run/futureagi/capabilities.json "
                f"{_SIMULATOR_SECRETS_PATH} "
                + (authoring_secrets_path if authoring_target_secrets else "")
                + " "
                + (
                    _SIMULATOR_VERTEX_CREDENTIALS_PATH
                    if simulator_vertex_credentials is not None
                    else ""
                ),
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
            # The fallback authoring command precedes hosted_entrypoint, so provide only the
            # non-secret Vertex selectors and the protected credential-file path here.  API keys
            # remain exclusively in simulator-secrets.json and are loaded (then deleted) by ALK.
            authoring_exports = {
                name: value
                for name, value in simulator_env.items()
                if name
                in {
                    "ALK_HARNESS",
                    "ALK_HARNESS_MODEL",
                    "ALK_VERTEX_LOCATION",
                    "GOOGLE_APPLICATION_CREDENTIALS",
                    "GOOGLE_CLOUD_LOCATION",
                    "GOOGLE_CLOUD_PROJECT",
                    "GOOGLE_GENAI_USE_VERTEXAI",
                }
            }
            export_command = " ".join(
                f"{name}={shlex.quote(value)}"
                for name, value in sorted(authoring_exports.items())
            )
            provider_profile_args = (
                f"--target-secrets {authoring_secrets_path} "
                "--provider-profile-cache /work/provider-import-profile.json "
                if authoring_target_secrets
                else ""
            )
            command = sandbox.process.execute_session_command(
                _ENTRYPOINT_SESSION,
                SessionExecuteRequest(
                    command=(
                        (f"export {export_command} && " if export_command else "")
                        + "if [ ! -f /work/authoring/contract.json ]; then "
                        "python -m fi.alk.harness.hosted_authoring_entrypoint "
                        "/work/job.json --source /work/source --output /work/authoring "
                        f"--adjustments {_ADJUSTMENTS_PATH} "
                        + provider_profile_args
                        + "; "
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

    def adjust(
        self, job: HostedHarnessJob, request: dict[str, Any]
    ) -> HostedHarnessJob:
        """Deliver a user correction to the active ALK authoring process.

        The database record is the durable control-plane copy; the JSONL file is
        the attempt-local inbox consumed by ALK at stage boundaries. Keeping the
        whole inbox in metadata also makes retries and the UI deterministic.
        """
        from daytona import DaytonaNotFoundError

        terminal_states = {
            HostedHarnessJob.State.COMPLETED,
            HostedHarnessJob.State.FAILED,
            HostedHarnessJob.State.CANCELED,
        }
        if job.state in terminal_states:
            raise HostedHarnessError(
                "adjustment_too_late",
                "a completed run cannot be adjusted; start a follow-up run",
                status_code=409,
            )
        if job.current_stage in {
            "connecting_agent",
            "running",
            "grading",
            "uploading_artifacts",
            "cleaning_up",
        }:
            raise HostedHarnessError(
                "adjustment_too_late",
                "scenario authoring has finished; start a follow-up run to change it",
                status_code=409,
            )

        attempt = (
            HostedHarnessAttempt.no_workspace_objects.filter(
                job=job, attempt_number=job.current_attempt_number
            )
            .exclude(provider_ref__isnull=True)
            .first()
        )
        if attempt is None or not attempt.provider_ref:
            raise HostedHarnessError(
                "adjustment_not_ready",
                "the hosted sandbox is not ready for messages yet; retry in a few seconds",
                status_code=409,
                retryable=True,
            )
        if attempt.snapshot_name != _DIRECT_IMAGE_WITH_ADJUSTMENTS:
            raise HostedHarnessError(
                "adjustment_protocol_unavailable",
                "this run started before live messages were enabled; start a new run to use them",
                status_code=409,
            )
        try:
            sandbox = self.client.get(str(attempt.provider_ref))
        except DaytonaNotFoundError as exc:
            raise HostedHarnessError(
                "adjustment_sandbox_missing",
                "the active hosted sandbox no longer exists",
                status_code=409,
            ) from exc

        instruction = str(request["instruction"]).strip()
        client_request_id = str(request.get("client_request_id") or "") or None
        with transaction.atomic():
            locked = HostedHarnessJob.no_workspace_objects.select_for_update().get(
                id=job.id
            )
            payload = dict(locked.payload)
            metadata = dict(payload.get("metadata") or {})
            adjustments = list(metadata.get("adjustments") or [])
            if client_request_id:
                existing = next(
                    (
                        item
                        for item in adjustments
                        if item.get("client_request_id") == client_request_id
                    ),
                    None,
                )
                if existing is not None:
                    return locked

            delta = _scenario_delta(instruction)
            if delta and locked.scenario_count + delta > 200:
                raise HostedHarnessError(
                    "scenario_limit_exceeded",
                    "a hosted run can contain at most 200 scenarios",
                    status_code=422,
                )
            record = {
                "adjustment_id": str(uuid.uuid4()),
                "client_request_id": client_request_id,
                "instruction": instruction,
                "target_stage": _adjustment_stage(instruction, locked.current_stage),
                "scenario_delta": delta,
                "status": "pending",
                "created_at": timezone.now().isoformat(),
            }
            adjustments.append(record)
            metadata["adjustments"] = adjustments
            payload["metadata"] = metadata
            update_fields = ["payload", "updated_at"]
            if delta:
                locked.scenario_count += delta
                # ``scenario_count`` is duplicated in the durable dispatch document and the
                # indexed job column. Keep them atomic. The live guest updates its own job.json
                # after applying a chat adjustment, which masked this drift on attempt one; a
                # saved rerun rebuilt job.json from the stale payload, sealed only one scenario,
                # then failed platform preallocation because the job column correctly expected
                # two.
                payload["scenario_count"] = locked.scenario_count
                update_fields.append("scenario_count")
            locked.payload = payload
            locked.save(update_fields=update_fields)

            body = "".join(
                json.dumps(item, separators=(",", ":")) + "\n" for item in adjustments
            ).encode("utf-8")
            try:
                sandbox.fs.upload_file(body, _ADJUSTMENTS_PATH)
            except Exception as exc:
                raise HostedHarnessError(
                    "adjustment_delivery_failed",
                    "the message could not be delivered to the hosted run; retry",
                    status_code=503,
                    retryable=True,
                ) from exc
        return locked

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
        # The current hosted authoring pipeline writes the deterministic environment compiler
        # output under ``environment-bundle``.  Keep reading the legacy flat artifact for cached
        # runs, but do not leave the UI stuck on Generating environment when the V2 plan already
        # exists and scenario authoring has started.
        if not isinstance(environment, dict):
            environment = _json(
                "/work/authoring/environment-bundle/environment-plan.json"
            )
        authored_bundle = _json("/work/authoring/environment-bundle/manifest.json")
        scenarios = _json("/work/authoring/scenarios.json")
        bundle = _json("/work/bundle/manifest.json")
        job = HostedHarnessJob.no_workspace_objects.get(id=attempt.job_id)

        # Unified hosted execution authors the contract/world/scenarios in the same
        # sandbox that later runs the calls.  Freeze those inputs as soon as Bundle V2
        # has accepted them.  A saved rerun can then rebuild the environment while
        # using the exact same sealed scenario suite instead of asking the model to
        # author a different suite for an already-registered RunTest.
        metadata = (job.payload or {}).get("metadata") or {}
        if (
            isinstance(bundle, dict)
            and isinstance(scenarios, list)
            and len(scenarios) == job.scenario_count
            and not metadata.get("authoring_object_key")
        ):
            try:
                packed = sandbox.process.exec(
                    "cd /work/authoring && set --; "
                    "for path in schema.sql store.json world.sqlite collections.json "
                    "contract.json environment.json scenarios.json simulator_prompt.md "
                    "scenarios handlers; do "
                    '[ -e "$path" ] && set -- "$@" "$path"; '
                    "done; "
                    '[ -f contract.json ] && [ -d scenarios ] && [ "$#" -gt 0 ] && '
                    'tar -czf /tmp/authoring-rerun.tar.gz "$@"',
                    timeout=180,
                )
                if packed.exit_code:
                    raise RuntimeError(str(packed.result or "authoring pack failed"))
                body = sandbox.fs.download_file("/tmp/authoring-rerun.tar.gz")
                store_authoring_archive(job, body, advance_lifecycle=False)
                job.refresh_from_db()
            except Exception:  # noqa: BLE001 - retry on the next poll; do not abort calls
                logger.exception(
                    "could not freeze unified authoring snapshot job=%s attempt=%s",
                    job.id,
                    attempt.id,
                )
        outputs = authoring_stage_outputs(
            contract,
            environment,
            scenarios,
            bundle if isinstance(bundle, dict) else authored_bundle,
        )
        stage = "understanding_agent"
        if isinstance(contract, dict):
            stage = "generating_environment"
        if isinstance(environment, dict):
            stage = "generating_scenarios"
        if isinstance(scenarios, list):
            stage = "validating_environment"
        DaytonaHostedGateway._sync_adjustment_progress(job, sandbox)
        if not outputs:
            return
        # Once the guest event channel advances into runtime stages it is authoritative.
        if job.current_stage in {
            "queued",
            "admitted",
            "understanding_agent",
            "generating_environment",
            "generating_scenarios",
        }:
            job.current_stage = stage
        # Authoring archives use per-scenario files rather than a top-level
        # scenarios.json.  ``store_authoring_archive`` has already materialized
        # that snapshot, so a later heartbeat must enrich the visible stages
        # instead of deleting stages whose source file is not present here.
        merged = {
            item.get("kind"): item
            for item in (job.stage_outputs or [])
            if isinstance(item, dict) and item.get("kind")
        }
        merged.update({item["kind"]: item for item in outputs})
        order = {"contract": 0, "environment": 1, "scenarios": 2}
        job.stage_outputs = sorted(
            merged.values(), key=lambda item: order.get(item.get("kind"), 99)
        )
        job.save(update_fields=["current_stage", "stage_outputs", "updated_at"])

    @staticmethod
    def _sync_adjustment_progress(job: HostedHarnessJob, sandbox) -> None:
        try:
            raw = sandbox.fs.download_file(_ADJUSTMENT_STATUS_PATH).decode("utf-8")
        except Exception:  # noqa: BLE001 - status file does not exist before first boundary
            return
        statuses: dict[str, dict[str, Any]] = {}
        for line in raw.splitlines():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict) and value.get("adjustment_id"):
                statuses[str(value["adjustment_id"])] = value
        if not statuses:
            return
        payload = dict(job.payload)
        metadata = dict(payload.get("metadata") or {})
        adjustments = list(metadata.get("adjustments") or [])
        merged = [
            {**item, **statuses.get(str(item.get("adjustment_id")), {})}
            for item in adjustments
        ]
        if merged == adjustments:
            return
        metadata["adjustments"] = merged
        payload["metadata"] = metadata
        job.payload = payload
        job.save(update_fields=["payload", "updated_at"])

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
        # Cleanup derives the final job state from the attempt's terminal
        # stage. The already-absent branch sets this above; the normal branch
        # must do the same or an intentional cancellation is misreported as a
        # platform failure after the sandbox is deleted.
        attempt.terminal_stage = "canceled"
        attempt.terminal_reason = reason
        attempt.terminal_failure = None
        attempt.save(
            update_fields=[
                "terminal_stage",
                "terminal_reason",
                "terminal_failure",
                "updated_at",
            ]
        )
        self._delete_and_record(attempt)
        return HostedHarnessJob.no_workspace_objects.get(id=job.id)

    def _should_retry(self, attempt: HostedHarnessAttempt, domain: str) -> bool:
        # An infrastructure/connectivity failure is worth a fresh attempt while
        # the domain is retryable and the whole-job attempt budget remains
        # (spine §0.6 + §1 `retry`). Exit 0 (verdict delivered) and exit 3
        # (superseded) never reach here.
        retry = attempt.job.payload.get("retry", {})
        cycle_start = int(
            (attempt.job.payload.get("metadata") or {}).get("attempt_cycle_start") or 1
        )
        attempts_in_cycle = attempt.attempt_number - cycle_start + 1
        return domain in retry.get(
            "retryable_domains", []
        ) and attempts_in_cycle < retry.get("max_infrastructure_attempts", 1)

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


def _provider_import_authoring_material(
    job: HostedHarnessJob, payload: dict[str, Any]
) -> tuple[dict[str, str], str]:
    """Resolve only the provider key needed for read-only imported-target inspection."""
    agent = payload.get("agent") or {}
    connector = str(agent.get("connector") or "").strip().lower()
    if str(agent.get("mode") or "") != "provider_import":
        return {}, connector
    secret_name = {
        "vapi": "VAPI_API_KEY",
        "retell": "RETELL_API_KEY",
    }.get(connector)
    if not secret_name:
        return {}, connector
    resolved = PlatformSecretResolver().resolve(job)
    value = str(resolved.get(secret_name) or "")
    return ({secret_name: value} if value else {}), connector


def prepare_dispatch_payload(
    payload: dict[str, Any],
    secrets_map: dict[str, str],
    *,
    simulator_secrets: dict[str, str] | None = None,
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
    connector = str(agent.get("connector") or "").lower()
    # LiveKit targets use the customer's signaling URL. Provider-hosted voice
    # targets are dialed by our simulator and therefore use the platform-owned
    # LiveKit URL from the isolated simulator channel.
    livekit_url = (
        secrets_map.get("LIVEKIT_URL")
        if connector == "livekit"
        else (simulator_secrets or {}).get("LIVEKIT_URL")
    )
    if connector in {"livekit", "vapi", "retell"} and not config.get(
        "livekit_url"
    ) and livekit_url:
        config["livekit_url"] = livekit_url
        agent["config"] = config
        dispatched["agent"] = agent
    return dispatched


def _secret_safe(value: Any, *, key: str = "") -> Any:
    """Return an authoring document safe to persist and display.

    Authoring outputs should normally contain references rather than secret values. This is a
    second boundary check so a malformed/generated environment cannot leak a credential into the
    job JSON or API response.
    """
    normalized = key.lower()
    safe_secret_metadata = {
        "secret_purposes",
        "secret_refs",
        "required_secrets",
        "required_credentials",
    }
    secret_markers = (
        "api_key",
        "api_secret",
        "password",
        "private_key",
        "access_token",
        "refresh_token",
        "client_secret",
        "credential_json",
        "secret_value",
    )
    if normalized not in safe_secret_metadata and (
        normalized == "secrets"
        or normalized.endswith("_secret")
        or any(marker in normalized for marker in secret_markers)
    ):
        return "[redacted]"
    if isinstance(value, dict):
        return {
            str(item_key): _secret_safe(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_secret_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def authoring_stage_outputs(
    contract: Any, environment: Any, scenarios: Any, bundle: Any = None
) -> list[dict[str, Any]]:
    """Build the complete, secret-safe snapshots shown by the hosted-run UI."""
    outputs: list[dict[str, Any]] = []
    if isinstance(contract, dict):
        tools = contract.get("tools") or []
        outputs.append(
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "kind": "contract",
                "title": "Agent contract",
                "summary": f"{len(tools)} tools · {contract.get('modality') or 'unknown'} modality",
                "data": _secret_safe(contract),
            }
        )
    if isinstance(environment, dict) or isinstance(bundle, dict):
        environment_data = dict(environment or {})
        if isinstance(bundle, dict):
            environment_data["bundle_manifest"] = bundle
        services = environment_data.get("services") or []
        processes = (bundle or {}).get("processes") if isinstance(bundle, dict) else []
        described = len(services) or len(processes or [])
        outputs.append(
            {
                "id": "00000000-0000-0000-0000-000000000002",
                "kind": "environment",
                "title": "Execution environment",
                "summary": f"{described} runtime components described",
                "data": _secret_safe(environment_data),
            }
        )
    if isinstance(scenarios, list):
        outputs.append(
            {
                "id": "00000000-0000-0000-0000-000000000003",
                "kind": "scenarios",
                "title": "Generated scenarios",
                "summary": f"{len(scenarios)} grounded scenarios",
                "data": _secret_safe(scenarios),
            }
        )
    return outputs


def authoring_stage_outputs_from_archive(
    body: bytes, *, scenario_limit: int | None = None
) -> list[dict[str, Any]]:
    """Read only the bounded JSON snapshots from a sealed authoring archive."""
    documents: dict[str, Any] = {}
    scenario_documents: list[dict[str, Any]] = []
    wanted = {"contract.json", "environment.json", "scenarios.json"}
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
        for member in archive.getmembers():
            path = Path(member.name)
            name = path.name
            is_scenario = (
                name == "scenario.json"
                and len(path.parts) >= 3
                and path.parts[-3] == "scenarios"
            )
            if (
                (name not in wanted and not is_scenario)
                or not member.isfile()
                or member.size > 8 * 1024 * 1024
            ):
                continue
            source = archive.extractfile(member)
            if source is None:
                continue
            try:
                value = json.loads(source.read().decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                continue
            if is_scenario and isinstance(value, dict):
                value = dict(value)
                value.setdefault("scenario_key", path.parts[-2])
                scenario_documents.append(value)
            else:
                documents[name] = value
    scenarios = documents.get("scenarios.json")
    if not isinstance(scenarios, list) and scenario_documents:
        scenarios = sorted(
            scenario_documents,
            key=lambda scenario: str(scenario.get("scenario_key") or ""),
        )
    if isinstance(scenarios, list) and scenario_limit is not None:
        scenarios = scenarios[: max(0, scenario_limit)]
    return authoring_stage_outputs(
        documents.get("contract.json"),
        documents.get("environment.json"),
        scenarios,
    )


def store_authoring_archive(
    job: HostedHarnessJob, body: bytes, *, advance_lifecycle: bool = True
) -> str:
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
    update_fields = ["payload", "updated_at"]
    if advance_lifecycle:
        job.stage_outputs = authoring_stage_outputs_from_archive(
            body, scenario_limit=job.scenario_count
        )
        job.current_stage = "validating_scenarios"
        job.state = HostedHarnessJob.State.ADMITTED
        update_fields.extend(["stage_outputs", "current_stage", "state"])
    job.save(update_fields=update_fields)
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
