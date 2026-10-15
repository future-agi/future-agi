"""Prebuilt environment releases on the platform: registration, compositions and run manifests.

`api_contracts/harness/environment-releases.md` §3, §6, §7. ALK seals and verifies releases; the
platform stores their archives, produces bindings and run manifests with the same canonical digest,
and delivers all of them to the run sandbox, where `fi.alk.harness.releases compose` verifies them
before anything runs.

A composition is the "adapted environment": a completed `HostedHarnessJob` whose payload points at
immutable releases and a binding instead of an authoring archive. Its scenario records are created
from the suite's `provision.json`, so runs use the existing selected-run machinery unchanged.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import tarfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from minio.error import S3Error

from simulate.models import (
    AgentVersion,
    HarnessAgentBinding,
    HarnessEnvironmentRelease,
    HarnessRunManifest,
    HarnessSuiteRelease,
    HostedHarnessJob,
    HostedHarnessScenario,
)
from simulate.services.hosted_harness import (
    HostedHarnessError,
    canonical_digest,
    create_hosted_job,
)
from tfc.settings.settings import UPLOAD_BUCKET_NAME
from tfc.utils.storage_client import ensure_bucket, get_storage_client

ENVIRONMENT_RELEASE_SCHEMA = "futureagi.environment-release.v1"
SUITE_RELEASE_SCHEMA = "futureagi.suite-release.v1"
AGENT_BINDING_SCHEMA = "futureagi.agent-binding.v1"
BINDING_PROOF_SCHEMA = "futureagi.binding-proof.v1"
RUN_MANIFEST_SCHEMA = "futureagi.run-manifest.v1"
ADAPTATION_RESULT_SCHEMA = "futureagi.adaptation-result.v1"
RUNTIME_ENGINE = "postgres"
# What `compose` builds (contract §6); anything else would fail inside the sandbox.
COMPOSABLE = {
    ("retell_chat", "provider_import"),
    ("retell", "provider_import"),
    ("vapi", "provider_import"),
}
MODALITY = {"retell_chat": "text", "retell": "voice", "vapi": "voice"}
# Contract §2.4: a normalized binding always carries every field.
BINDING_FIELDS = frozenset(
    {
        "schema_version",
        "digest",
        "agent",
        "environment",
        "mode",
        "agent_tools",
        "tools",
        "fixtures",
        "aliases",
        "rendering",
        "prompt_changes",
    }
)

RELEASES_ROOT = "/work/releases"
BINDING_PATH = "/work/binding.json"
RUN_MANIFEST_PATH = "/work/run-manifest.json"


def _error(code: str, message: str, status_code: int = 400) -> HostedHarnessError:
    return HostedHarnessError(code, message, status_code=status_code)


def _hex(digest: str) -> str:
    return digest.removeprefix("sha256:")


def _read(directory: Path, name: str) -> Any:
    try:
        return json.loads((directory / name).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise _error("release_invalid", f"{directory / name}: {exc}") from exc


def _archive(directory: Path) -> bytes:
    """A reproducible tar.gz of a sealed release directory."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        for path in sorted(directory.rglob("*")):
            if path.is_dir():
                continue
            info = tarfile.TarInfo(path.relative_to(directory).as_posix())
            content = path.read_bytes()
            info.size, info.mtime, info.mode = len(content), 0, 0o644
            tar.addfile(info, io.BytesIO(content))
    return gzip.compress(raw.getvalue(), mtime=0)


def _upload(object_key: str, body: bytes) -> None:
    client = get_storage_client()
    ensure_bucket(client, UPLOAD_BUCKET_NAME)
    client.put_object(
        bucket_name=UPLOAD_BUCKET_NAME,
        object_name=object_key,
        data=io.BytesIO(body),
        length=len(body),
        content_type="application/gzip",
    )


def _download(object_key: str) -> bytes:
    response = None
    try:
        response = get_storage_client().get_object(UPLOAD_BUCKET_NAME, object_key)
        return response.read()
    except S3Error as exc:
        if exc.code != "NoSuchKey":
            raise
        raise _error(
            "release_archive_missing", f"release archive {object_key} is missing", 422
        ) from exc
    finally:
        if response is not None:
            response.close()
            response.release_conn()


def _key(organization, kind: str, digest: str) -> str:
    owner = str(organization.id) if organization is not None else "system"
    return f"harness-releases/{owner}/{kind}/{_hex(digest)}.tar.gz"


# --- registration ------------------------------------------------------------------------------


@contextmanager
def _owned_by(organization):
    """Write rows owned by exactly `organization`, `None` meaning a system release.

    The tenant-context save hooks (`tfc/utils/signals.py`, `BaseModel.save`) fill a missing
    organization from the current request; for a system release that would silently make it one
    organization's. The caller's context is restored afterwards.
    """
    from tfc.middleware.workspace_context import (
        get_current_organization,
        get_current_workspace,
        set_workspace_context,
    )

    previous = (get_current_workspace(), get_current_organization())
    set_workspace_context(workspace=None, organization=organization)
    try:
        yield
    finally:
        set_workspace_context(workspace=previous[0], organization=previous[1])


def _visible(model, organization, digest: str):
    """The release `organization` may build on: its own first, otherwise a system release.
    Never another organization's, whatever digest it holds."""
    candidates = model.no_workspace_objects.filter(digest=digest)
    own = candidates.filter(organization=organization).first() if organization else None
    return own or candidates.filter(organization__isnull=True).first()


def _usable(model, organization):
    return model.no_workspace_objects.filter(
        Q(organization=organization) | Q(organization__isnull=True), deleted=False
    )


def visible_suite(organization, suite_id) -> HarnessSuiteRelease:
    suite = (
        _usable(HarnessSuiteRelease, organization)
        .select_related("environment", "environment__parent")
        .filter(id=suite_id)
        .first()
    )
    if suite is None:
        raise _error("release_not_found", "suite release not found", 404)
    return suite


def organization_agent_version(organization, version_id) -> AgentVersion:
    version = AgentVersion.no_workspace_objects.filter(
        id=version_id, organization=organization, deleted=False
    ).first()
    if version is None:
        raise _error("agent_version_not_found", "agent version not found", 404)
    return version


def _suite_summary(suite: HarnessSuiteRelease) -> dict[str, Any]:
    scenarios = suite.manifest.get("scenarios") or []
    origins = {"base": 0, "specialized": 0, "custom": 0}
    for entry in scenarios:
        origins[entry["origin"]] = origins.get(entry["origin"], 0) + 1
    return {
        "id": str(suite.id),
        "digest": suite.digest,
        "name": suite.name,
        "scenario_count": len(scenarios),
        "origins": origins,
        "policy_profile": suite.manifest.get("policy_profile") or {},
    }


def list_releases(organization) -> list[dict[str, Any]]:
    """The environment releases `organization` may compose (contract §7.6): system releases and
    its own, each with the suites it may use."""
    environments = list(
        _usable(HarnessEnvironmentRelease, organization)
        .select_related("parent")
        .order_by("name", "created_at")
    )
    suites: dict[Any, list[HarnessSuiteRelease]] = {}
    for suite in (
        _usable(HarnessSuiteRelease, organization)
        .filter(environment__in=environments)
        .order_by("name", "created_at")
    ):
        suites.setdefault(suite.environment_id, []).append(suite)
    results = []
    for environment in environments:
        rules = (environment.policy or {}).get("rules") or []
        results.append(
            {
                "id": str(environment.id),
                "digest": environment.digest,
                "name": environment.name,
                "system": environment.organization_id is None,
                "parent": (
                    {
                        "digest": environment.parent.digest,
                        "name": environment.parent.name,
                    }
                    if environment.parent_id
                    else None
                ),
                "tools": [
                    {
                        "name": tool["name"],
                        "description": tool.get("description") or "",
                        "mutates_state": bool(tool.get("mutates_state")),
                    }
                    for tool in environment.tools
                ],
                "policy": {
                    "parameters": [
                        {
                            "name": name,
                            "type": spec.get("type"),
                            "default": spec.get("default"),
                            "allowed": spec.get("allowed"),
                        }
                        for rule in rules
                        if rule.get("class") == "configurable_environment_policy"
                        for name, spec in (rule.get("parameters") or {}).items()
                    ],
                    "agent_rules": [
                        {"id": rule["id"], "text": rule.get("text") or ""}
                        for rule in rules
                        if rule.get("class") == "agent_policy"
                    ],
                },
                "suites": [
                    _suite_summary(suite) for suite in suites.get(environment.id, [])
                ],
            }
        )
    return results


def _checked_suite(
    suite_dir: Path, environment_digest: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """A suite's manifest and personas after the §7.2 checks against its environment."""
    suite = _read(suite_dir, "suite.json")
    if suite.get("schema_version") != SUITE_RELEASE_SCHEMA:
        raise _error("release_invalid", "suite.json is not a suite release")
    if (suite.get("environment") or {}).get("digest") != environment_digest:
        raise _error(
            "release_invalid", "the suite was proved against another environment"
        )
    runtime = _read(suite_dir, "proofs.json").get("runtime") or {}
    proved = runtime.get("scenarios") or {}
    if runtime.get("engine") != RUNTIME_ENGINE or not all(
        (proved.get(entry["key"]) or {}).get("holds") for entry in suite["scenarios"]
    ):
        raise _error(
            "suite_runtime_unproved", f"{suite['name']} has no PostgreSQL proof"
        )
    personas = _read(suite_dir, "provision.json").get("personas") or []
    if [persona.get("scenario_key") for persona in personas] != [
        entry["key"] for entry in suite["scenarios"]
    ]:
        raise _error(
            "release_invalid", "provision.json does not match the suite's scenarios"
        )
    return suite, personas


def _store_suite(
    organization,
    suite_dir: Path,
    suite: dict[str, Any],
    personas: list[dict[str, Any]],
    environment: HarnessEnvironmentRelease,
) -> HarnessSuiteRelease:
    """Insert the suite row unless it exists; the caller holds `_owned_by(organization)`."""
    existing = HarnessSuiteRelease.no_workspace_objects.filter(
        organization=organization, digest=suite["digest"]
    ).first()
    if existing is not None:
        return existing
    parent = None
    if suite.get("parent"):
        parent = _visible(HarnessSuiteRelease, organization, suite["parent"]["digest"])
        if parent is None:
            raise _error("release_parent_missing", "register the parent suite first")
    object_key = _key(organization, "suite", suite["digest"])
    _upload(object_key, _archive(suite_dir))
    return HarnessSuiteRelease.no_workspace_objects.create(
        organization=organization,
        digest=suite["digest"],
        name=suite["name"],
        environment=environment,
        parent=parent,
        object_key=object_key,
        manifest=suite,
        personas=personas,
    )


def register_release(
    environment_dir: Path, suite_dir: Path, *, organization=None
) -> tuple[HarnessEnvironmentRelease, HarnessSuiteRelease]:
    """Store one sealed environment release and a suite proved against it.

    The suite must carry a passing PostgreSQL proof, because hosted runs refuse one without it.
    An extension's parent must already be registered. Re-registering identical releases returns
    the existing rows.
    """
    environment_dir, suite_dir = Path(environment_dir), Path(suite_dir)
    release = _read(environment_dir, "release.json")
    if release.get("schema_version") != ENVIRONMENT_RELEASE_SCHEMA:
        raise _error("release_invalid", "release.json is not an environment release")
    suite, personas = _checked_suite(suite_dir, release["digest"])
    parent = None
    if release.get("extends"):
        parent = _visible(
            HarnessEnvironmentRelease, organization, release["extends"]["digest"]
        )
        if parent is None:
            raise _error("release_parent_missing", "register the parent release first")

    with _owned_by(organization), transaction.atomic():
        environment = HarnessEnvironmentRelease.no_workspace_objects.filter(
            organization=organization, digest=release["digest"]
        ).first()
        if environment is None:
            object_key = _key(organization, "environment", release["digest"])
            _upload(object_key, _archive(environment_dir))
            environment = HarnessEnvironmentRelease.no_workspace_objects.create(
                organization=organization,
                digest=release["digest"],
                name=release["name"],
                parent=parent,
                object_key=object_key,
                manifest=release,
                tools=_read(environment_dir, "tools.json"),
                policy=_read(environment_dir, "policy.json"),
            )
        return environment, _store_suite(
            organization, suite_dir, suite, personas, environment
        )


# --- bindings and compositions -------------------------------------------------------------------


def _with_digest(document: dict[str, Any]) -> dict[str, Any]:
    return {**document, "digest": canonical_digest(document)}


def agent_reference(agent_version: AgentVersion, connector: str) -> dict[str, Any]:
    return {
        "agent_definition_id": str(agent_version.agent_definition_id),
        "agent_version_id": str(agent_version.id),
        "configuration_digest": canonical_digest(
            agent_version.configuration_snapshot or {}
        ),
        "connector": connector,
    }


def binding_document(
    agent_version: AgentVersion, environment_digest: str, connector: str
) -> dict[str, Any]:
    """A normalized canonical binding (contract §2.4): every field present, so the guest's
    recomputed digest matches."""
    return _with_digest(
        {
            "schema_version": AGENT_BINDING_SCHEMA,
            "agent": agent_reference(agent_version, connector),
            "environment": {"digest": environment_digest},
            "mode": "canonical",
            "agent_tools": [],
            "tools": [],
            "fixtures": {},
            "aliases": {},
            "rendering": {},
            "prompt_changes": [],
        }
    )


def _checked_binding(
    document: Any,
    *,
    agent: dict[str, Any],
    environment_digest: str,
    suite_digest: str,
    proof: Any,
) -> dict[str, Any]:
    """A given binding the composition may use: normalized, sealed over exactly its content,
    targeting this agent version and environment, and proved against this suite when routed.
    """
    if (
        not isinstance(document, dict)
        or set(document) != BINDING_FIELDS
        or document.get("schema_version") != AGENT_BINDING_SCHEMA
    ):
        raise _error("binding_invalid", "the binding is not a normalized agent binding")
    if canonical_digest(
        {key: value for key, value in document.items() if key != "digest"}
    ) != document.get("digest"):
        raise _error(
            "binding_invalid", "the binding's digest does not match its content"
        )
    if document["agent"] != agent:
        raise _error("binding_invalid", "the binding targets another agent version")
    if document["environment"] != {"digest": environment_digest}:
        raise _error("binding_invalid", "the binding targets another environment")
    if document["prompt_changes"]:
        raise _error("binding_invalid", "prompt_changes must be empty")
    mode = document["mode"]
    if mode == "conversation_only":
        raise _error(
            "compose_unsupported",
            "a conversation_only binding reaches no environment tool and is not composed",
        )
    if mode == "tool_routed":
        if not (
            isinstance(proof, dict)
            and proof.get("schema_version") == BINDING_PROOF_SCHEMA
            and proof.get("passed") is True
            and (proof.get("binding") or {}).get("digest") == document["digest"]
            and (proof.get("suite") or {}).get("digest") == suite_digest
            and (proof.get("environment") or {}).get("digest") == environment_digest
            # A binding with nothing to run must not become a composition.
            and any(
                (entry or {}).get("status") == "proved"
                for entry in (proof.get("scenarios") or {}).values()
            )
        ):
            raise _error(
                "binding_unproved",
                "a tool_routed binding needs a passing proof against this suite",
            )
    elif mode != "canonical":
        raise _error("binding_invalid", f"unknown binding mode {mode!r}")
    elif any(
        document[field] for field in ("agent_tools", "tools", "fixtures", "aliases")
    ):
        raise _error("binding_invalid", "a canonical binding maps no agent tools")
    return document


def _check_modality(agent_version: AgentVersion, connector: str) -> None:
    """The runs record calls under the agent's definition, which must be of the same modality."""
    if agent_version.agent_definition.agent_type != MODALITY[connector]:
        raise _error(
            "agent_modality_mismatch",
            f"a {connector} agent needs a {MODALITY[connector]} agent version",
        )


def _chain(suite: HarnessSuiteRelease) -> list[HarnessEnvironmentRelease]:
    environment = suite.environment
    return [environment.parent, environment] if environment.parent_id else [environment]


# One transaction: a retried call must never find the idempotent job without its scenarios.
@transaction.atomic
def create_composition(
    organization,
    workspace,
    *,
    suite: HarnessSuiteRelease,
    agent: dict[str, Any],
    agent_version: AgentVersion,
    name: str,
    idempotency_key: str,
    binding: dict[str, Any] | None = None,
    alignment: dict[str, Any] | None = None,
    binding_proof: dict[str, Any] | None = None,
) -> HostedHarnessJob:
    """A completed environment that runs `agent` against `suite` and its environment.

    `agent` is the same connection a hosted job carries (connector, mode, config, secret refs).
    `agent_version` is the platform identity results are attributed to. `binding` is a sealed
    document from adaptation (with its `alignment` and, when tool-routed, `binding_proof`);
    without one the composition uses a canonical binding.
    """
    from simulate.serializers.harness_job import HarnessJobCreateSerializer
    from simulate.services.alk_simulate_ingestion import (
        ALKSimulateIngestionError,
        provision_alk_sim_run_test,
    )

    if suite.organization_id not in (None, organization.id):
        raise _error("release_not_found", "suite release not found", 404)
    if agent_version.organization_id != organization.id:
        raise _error("agent_version_not_found", "agent version not found", 404)
    personas = list(suite.personas)
    serializer = HarnessJobCreateSerializer(
        data={
            "agent": agent,
            "artifacts": {"level": "full"},
            "scenario_count": len(personas),
            "metadata": {"name": name},
        }
    )
    if not serializer.is_valid():
        raise _error("composition_invalid", json.dumps(serializer.errors, default=str))
    payload = serializer.validated_data
    connector = payload["agent"]["connector"]
    if (connector, payload["agent"].get("mode")) not in COMPOSABLE:
        raise _error(
            "compose_unsupported",
            f"{connector} agents are not composed; retell_chat, retell and vapi agents "
            "in provider_import mode are",
        )
    _check_modality(agent_version, connector)
    chain = _chain(suite)
    if binding is None:
        binding = binding_document(agent_version, chain[-1].digest, connector)
    else:
        binding = _checked_binding(
            binding,
            agent=agent_reference(agent_version, connector),
            environment_digest=chain[-1].digest,
            suite_digest=suite.digest,
            proof=binding_proof,
        )
    HarnessAgentBinding.no_workspace_objects.get_or_create(
        organization=organization,
        digest=binding["digest"],
        defaults={
            "agent_version": agent_version,
            "environment": chain[-1],
            "mode": binding["mode"],
            "document": binding,
        },
    )
    payload["metadata"]["release_composition"] = {
        "environment": [
            {
                "digest": release.digest,
                "name": release.name,
                "object_key": release.object_key,
            }
            for release in chain
        ],
        "suite": {
            "digest": suite.digest,
            "name": suite.name,
            "object_key": suite.object_key,
        },
        "binding": binding,
        "alignment": alignment,
        "binding_proof": binding_proof,
    }
    job, created = create_hosted_job(
        organization,
        json.loads(json.dumps(payload, default=str)),
        idempotency_key=idempotency_key,
        workspace=workspace,
    )
    if not created:
        return job
    try:
        run_test, scenarios, _definition = provision_alk_sim_run_test(
            organization,
            workspace=workspace,
            name=name,
            personas=[
                {key: value for key, value in persona.items() if key != "scenario_key"}
                for persona in personas
            ],
            agent_definition_id=str(agent_version.agent_definition_id),
            modality=MODALITY[connector],
        )
    except ALKSimulateIngestionError as exc:
        raise _error("composition_invalid", str(exc)) from exc
    rows = list(
        scenarios[0].dataset.row_set.filter(deleted=False).order_by("order", "id")
    )
    if len(scenarios) != 1 or len(rows) != len(personas):
        raise _error(
            "scenario_provision_count_mismatch", "scenario rows were not created", 500
        )
    run_test.agent_version = agent_version
    run_test.save(update_fields=["agent_version", "updated_at"])
    with transaction.atomic():
        HostedHarnessScenario.no_workspace_objects.bulk_create(
            [
                HostedHarnessScenario(
                    job=job,
                    scenario_key=persona["scenario_key"],
                    scenario=scenarios[0],
                    dataset_row=row,
                )
                for persona, row in zip(personas, rows, strict=True)
            ]
        )
        now = timezone.now()
        job.run_test = run_test
        job.state = HostedHarnessJob.State.COMPLETED
        job.current_stage = "completed"
        job.terminal_at = now
        job.content_updated_at = now
        job.save(
            update_fields=[
                "run_test",
                "state",
                "current_stage",
                "terminal_at",
                "content_updated_at",
                "updated_at",
            ]
        )
    return job


# --- run manifests ---------------------------------------------------------------------------------


def composition_of(job: HostedHarnessJob) -> dict[str, Any] | None:
    value = ((job.payload or {}).get("metadata") or {}).get("release_composition")
    return value if isinstance(value, dict) else None


def _voice_choice(prefix: str) -> str | None:
    """`provider/model` of one simulator voice stage as the gateway passes it; either half may be
    empty, and None means ALK picks both."""
    provider = str(os.environ.get(f"{prefix}_PROVIDER") or "").strip()
    model = str(os.environ.get(f"{prefix}_MODEL") or "").strip()
    return f"{provider}/{model}" if provider or model else None


def _simulator(run_test, connector: str) -> dict[str, Any]:
    """What the gateway will give the simulator, read from the same deployment settings at
    submission and then frozen; the gateway applies these values rather than re-reading them.
    """
    thinking = str(os.environ.get("SIMULATOR_LLM_THINKING") or "").strip() or None
    agent = getattr(run_test, "simulator_agent", None)
    prompt = str(getattr(agent, "prompt", "") or "")
    return {
        "llm": {
            "provider": str(
                os.environ.get("SIMULATOR_LLM_PROVIDER") or "vertex"
            ).strip(),
            "model": str(
                os.environ.get("SIMULATOR_LLM_MODEL") or "gemini-3.8-flash"
            ).strip(),
            "location": str(
                os.environ.get("GOOGLE_CLOUD_LOCATION") or "global"
            ).strip(),
            "thinking": thinking,
        },
        "voice": (
            {
                "stt": _voice_choice("SIMULATOR_STT"),
                "tts": _voice_choice("SIMULATOR_TTS"),
                "voice": str(os.environ.get("SIMULATOR_TTS_VOICE") or "").strip()
                or None,
            }
            if MODALITY.get(connector) == "voice"
            else None
        ),
        "persona": {
            "prompt_digest": (
                "sha256:" + hashlib.sha256(prompt.encode()).hexdigest()
                if prompt
                else None
            ),
            "temperature": getattr(agent, "llm_temperature", None),
            "interrupt_sensitivity": getattr(agent, "interrupt_sensitivity", None),
            "conversation_speed": getattr(agent, "conversation_speed", None),
            "finished_speaking_sensitivity": getattr(
                agent, "finished_speaking_sensitivity", None
            ),
            "max_call_duration_in_minutes": getattr(
                agent, "max_call_duration_in_minutes", None
            ),
            "initial_message_delay": getattr(agent, "initial_message_delay", None),
        },
    }


def freeze_run_manifest(
    environment: HostedHarnessJob,
    child: HostedHarnessJob,
    *,
    scenario_keys: list[str],
    trials: int,
) -> dict[str, Any]:
    """The run's manifest (contract §2.5), stored against the run job and returned for dispatch."""
    from simulate.models import SimulateEvalConfig
    from simulate.services.hosted_sandbox.factory import (
        sandbox_provider_name,
        sandbox_runtime_reference,
    )

    composition = composition_of(environment)
    binding = composition["binding"]
    from simulate.services.harness_capacity import configured_capacity

    # The same selection the gateway makes at launch: a resource profile may pin its own image.
    image_name, image_digest = sandbox_runtime_reference()
    try:
        capacity = configured_capacity(child.payload)
    except ValueError as exc:
        raise _error("sandbox_capacity_unavailable", str(exc), 503) from exc
    image_name = capacity.snapshot_name or image_name
    image_digest = capacity.snapshot_digest or image_digest
    evals = sorted(
        (
            {
                "name": str(config.name or config.eval_template_id),
                "version": str(config.updated_at.isoformat()),
            }
            for config in SimulateEvalConfig.no_workspace_objects.filter(
                run_test=environment.run_test, deleted=False
            )
        ),
        key=lambda item: item["name"],
    )
    document = _with_digest(
        {
            "schema_version": RUN_MANIFEST_SCHEMA,
            "environment": [
                {"digest": entry["digest"], "name": entry["name"]}
                for entry in composition["environment"]
            ],
            "suite": {"digest": composition["suite"]["digest"]},
            "binding": {"digest": binding["digest"]},
            "agent": binding["agent"],
            "selection": {"scenario_keys": list(scenario_keys), "trials": trials},
            "simulator": _simulator(
                environment.run_test, binding["agent"]["connector"]
            ),
            "scorer": {"alk_version": image_name, "evals": evals},
            "runtime": {
                "provider": sandbox_provider_name(),
                "image_name": image_name,
                "image_digest": image_digest,
            },
        }
    )
    HarnessRunManifest.no_workspace_objects.create(
        organization=environment.organization,
        digest=document["digest"],
        document=document,
        job=child,
    )
    return document


# --- delivery ----------------------------------------------------------------------------------------


def _release_archives(
    environment: list[dict[str, Any]], suite: dict[str, Any]
) -> tuple[list[tuple[str, bytes]], str]:
    """The release archives for the §6 layout under /work/releases, and the command that
    extracts them. Archives land directly in /work, which exists; the command creates the tree.
    """
    uploads: list[tuple[str, bytes]] = []
    extract: list[str] = []
    archives = [
        (
            f"/work/release-{_hex(entry['digest'])}.tar.gz",
            f"{RELEASES_ROOT}/environment/{_hex(entry['digest'])}",
            entry["object_key"],
        )
        for entry in environment
    ] + [("/work/release-suite.tar.gz", f"{RELEASES_ROOT}/suite", suite["object_key"])]
    for archive, target, object_key in archives:
        uploads.append((archive, _download(object_key)))
        extract.append(
            f"mkdir -p {target} && tar -xzf {archive} -C {target} && rm {archive}"
        )
    return uploads, " && ".join(extract)


@dataclass(frozen=True)
class ReleaseLaunch:
    """What the gateway puts in a release-based run's sandbox, and how it builds the bundle."""

    uploads: tuple[tuple[str, bytes], ...]
    prepare_command: str
    bundle_command: str
    simulator: dict[str, Any]
    runtime: dict[str, Any]


def release_launch_for(job: HostedHarnessJob) -> ReleaseLaunch | None:
    metadata = (job.payload or {}).get("metadata") or {}
    composition = metadata.get("release_composition")
    manifest = metadata.get("run_manifest")
    if not isinstance(composition, dict):
        return None
    if not isinstance(manifest, dict):
        raise _error(
            "run_manifest_missing",
            "a release run must be submitted from a composition",
            409,
        )
    uploads, prepare_command = _release_archives(
        composition["environment"], composition["suite"]
    )
    uploads.append((BINDING_PATH, json.dumps(composition["binding"]).encode()))
    uploads.append((RUN_MANIFEST_PATH, json.dumps(manifest).encode()))
    return ReleaseLaunch(
        uploads=tuple(uploads),
        prepare_command=prepare_command,
        bundle_command=(
            "rm -rf /work/bundle && python -m fi.alk.harness.releases compose "
            f"--releases {RELEASES_ROOT} --binding {BINDING_PATH} "
            f"--manifest {RUN_MANIFEST_PATH} --job /work/job.json --output /work/bundle"
        ),
        simulator=manifest["simulator"],
        runtime=manifest["runtime"],
    )


def apply_frozen_simulator(values: dict[str, str], simulator: dict[str, Any]) -> None:
    """Replace the gateway's simulator env with what a release run's manifest froze. A setting
    frozen as unset is removed, so a later deployment value cannot leak into the run."""
    llm = simulator["llm"]
    voice = simulator.get("voice") or {}
    frozen: dict[str, str | None] = {
        "SIMULATOR_LLM_PROVIDER": llm["provider"],
        "SIMULATOR_LLM_MODEL": llm["model"],
        "SIMULATOR_LLM_THINKING": llm.get("thinking"),
        "SIMULATOR_TTS_VOICE": voice.get("voice"),
    }
    if llm.get("location"):
        frozen["GOOGLE_CLOUD_LOCATION"] = llm["location"]
        frozen["ALK_VERTEX_LOCATION"] = llm["location"]
    for stage in ("stt", "tts"):
        provider, _, model = (voice.get(stage) or "/").partition("/")
        frozen[f"SIMULATOR_{stage.upper()}_PROVIDER"] = provider or None
        frozen[f"SIMULATOR_{stage.upper()}_MODEL"] = model or None
    for name, value in frozen.items():
        if value:
            values[name] = value
        else:
            values.pop(name, None)


# --- adaptation (contract §7.5) -------------------------------------------------------------------

ADAPTATION_OUTPUT = "/work/authoring/adaptation"


def adaptation_of(job: HostedHarnessJob) -> dict[str, Any] | None:
    value = ((job.payload or {}).get("metadata") or {}).get("adaptation")
    return value if isinstance(value, dict) else None


def adaptation_payload(
    organization,
    *,
    suite: HarnessSuiteRelease,
    agent: dict[str, Any],
    agent_version: AgentVersion,
    name: str,
) -> dict[str, Any]:
    """The validated hosted job payload of an adaptation of `agent` to `suite`."""
    from simulate.serializers.harness_job import HarnessJobCreateSerializer

    if suite.organization_id not in (None, organization.id):
        raise _error("release_not_found", "suite release not found", 404)
    if agent_version.organization_id != organization.id:
        raise _error("agent_version_not_found", "agent version not found", 404)
    serializer = HarnessJobCreateSerializer(
        data={
            "agent": agent,
            "artifacts": {"level": "full"},
            "scenario_count": len(suite.personas),
            "metadata": {"name": name},
        }
    )
    if not serializer.is_valid():
        raise _error("adaptation_invalid", json.dumps(serializer.errors, default=str))
    payload = serializer.validated_data
    connector = payload["agent"]["connector"]
    if (connector, payload["agent"].get("mode")) not in COMPOSABLE:
        raise _error(
            "compose_unsupported",
            f"{connector} agents are not adapted; retell_chat, retell and vapi agents "
            "in provider_import mode are",
        )
    _check_modality(agent_version, connector)
    payload["metadata"]["adaptation"] = {
        "environment": [
            {
                "digest": release.digest,
                "name": release.name,
                "object_key": release.object_key,
            }
            for release in _chain(suite)
        ],
        "suite": {
            "digest": suite.digest,
            "name": suite.name,
            "object_key": suite.object_key,
        },
        "agent_version_id": str(agent_version.id),
        "agent": agent_reference(agent_version, connector),
    }
    return json.loads(json.dumps(payload, default=str))


@dataclass(frozen=True)
class AdaptationLaunch:
    """What the gateway puts in an adaptation job's sandbox, and the process it runs there."""

    uploads: tuple[tuple[str, bytes], ...]
    prepare_command: str
    command: str


def adaptation_launch_for(job: HostedHarnessJob) -> AdaptationLaunch | None:
    adaptation = adaptation_of(job)
    if adaptation is None:
        return None
    uploads, prepare_command = _release_archives(
        adaptation["environment"], adaptation["suite"]
    )
    return AdaptationLaunch(
        uploads=tuple(uploads),
        prepare_command=prepare_command,
        command=(
            "python -m fi.alk.harness.adaptation hosted --job /work/job.json "
            f"--releases {RELEASES_ROOT} --output {ADAPTATION_OUTPUT}"
        ),
    )


def _adaptation_failure(code: str, message: str) -> dict[str, Any]:
    return {
        "domain": "environment",
        "stage": "adapting",
        "code": code,
        "message": message[:1000],
    }


def _adapt(job: HostedHarnessJob, output: Path) -> tuple[dict[str, Any], dict | None]:
    """Register what a completed adaptation produced; returns (adaptation_result, failure)."""
    result = _read(output, "result.json")
    if result.get("schema_version") != ADAPTATION_RESULT_SCHEMA:
        raise _error(
            "adaptation_result_invalid", "result.json is not an adaptation result"
        )
    if result.get("status") == "failed":
        failure = result.get("failure") or {}
        return {}, _adaptation_failure(
            str(failure.get("code") or "adaptation_failed"),
            str(failure.get("message") or "the adaptation failed"),
        )
    if result.get("status") != "completed":
        raise _error("adaptation_result_invalid", "result.json has no final status")
    adaptation = adaptation_of(job)
    organization = job.organization
    base = _visible(HarnessSuiteRelease, organization, adaptation["suite"]["digest"])
    agent_version = AgentVersion.no_workspace_objects.filter(
        id=adaptation["agent_version_id"], organization=organization, deleted=False
    ).first()
    if base is None or agent_version is None:
        raise _error(
            "adaptation_inputs_missing",
            "the base suite or agent version of this adaptation no longer exists",
        )
    # Adaptation never changes the suite (§7.5): the composition runs the base suite as sealed.
    suite = base
    reported = result.get("suite") or {}
    if reported.get("derived") or reported.get("digest") != suite.digest:
        raise _error(
            "adaptation_result_invalid",
            "result.json names another suite than the one adapted to",
        )
    binding = _read(output, "binding.json")
    mode = binding.get("mode")
    adaptation_result = {
        "composition_id": None,
        "suite_digest": suite.digest,
        "binding_digest": binding.get("digest"),
        "mode": mode,
    }
    if mode == "conversation_only":
        # Recorded, not composed: no world-state claim can be made for such an agent.
        adaptation_result["compose"] = {
            "code": "compose_unsupported",
            "message": "the agent reaches no environment tool",
        }
        return adaptation_result, None
    composition = create_composition(
        organization,
        job.workspace,
        suite=suite,
        agent=job.payload["agent"],
        agent_version=agent_version,
        name=str((job.payload.get("metadata") or {}).get("name") or suite.name),
        idempotency_key=f"adaptation:{job.id}",
        binding=binding,
        alignment=result.get("alignment"),
        # A missing proof is refused by the composition as binding_unproved.
        binding_proof=(
            _read(output, "binding-proof.json")
            if mode == "tool_routed" and (output / "binding-proof.json").is_file()
            else None
        ),
    )
    adaptation_result["composition_id"] = str(composition.id)
    return adaptation_result, None


def complete_adaptation(job: HostedHarnessJob, output: Path | None) -> HostedHarnessJob:
    """Finish an adaptation job from its `/work/authoring/adaptation` output (contract §7.5).

    `output` is None when the process left no output. A completed result registers the binding
    and the composition on the base suite and records `metadata.adaptation_result`; anything the
    platform refuses, or a failed result, fails the job with the reason.
    """
    from simulate.models import HostedHarnessConversation

    adaptation_result: dict[str, Any] = {}
    if output is None or not (Path(output) / "result.json").is_file():
        failure = _adaptation_failure(
            "adaptation_result_missing", "the adaptation process wrote no result.json"
        )
    else:
        try:
            adaptation_result, failure = _adapt(job, Path(output))
        except HostedHarnessError as exc:
            failure = _adaptation_failure(exc.code, exc.message)
    now = timezone.now()
    with transaction.atomic():
        locked = HostedHarnessJob.no_workspace_objects.select_for_update().get(
            id=job.id
        )
        payload = dict(locked.payload)
        payload["metadata"] = {
            **(payload.get("metadata") or {}),
            "adaptation_result": adaptation_result or None,
        }
        locked.payload = payload
        locked.state = (
            HostedHarnessJob.State.FAILED
            if failure
            else HostedHarnessJob.State.COMPLETED
        )
        locked.current_stage = locked.state
        locked.failure = failure
        locked.terminal_at = now
        locked.content_updated_at = now
        locked.save(
            update_fields=[
                "payload",
                "state",
                "current_stage",
                "failure",
                "terminal_at",
                "content_updated_at",
                "updated_at",
            ]
        )
        # The builder has finished; nothing it asked is left to answer.
        HostedHarnessConversation.no_workspace_objects.filter(job=locked).update(
            blocking_input=None, updated_at=now
        )
    return locked
