from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import structlog
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from simulate.models import (
    AgentVersion,
    CallExecution,
    HostedHarnessAttempt,
    HostedHarnessCleanupReceipt,
    HostedHarnessConversation,
    HostedHarnessExecution,
    HostedHarnessJob,
    HostedHarnessReceipt,
    HostedHarnessScenario,
    TestExecution,
)
from simulate.services.agent_setup import AgentStoreError, store_agent
from simulate.services.alk_simulate_ingestion import (
    ALKSimulateIngestionError,
    append_alk_sim_scenarios,
    create_alk_sim_call_execution_batch,
    create_alk_sim_test_execution,
    precreate_alk_sim_call_executions,
    provision_alk_sim_run_test,
    resolved_agent_version,
)
from simulate.services.canonical_json import (  # noqa: F401 - re-exported
    _json_value,
    _rfc3339,
    canonical_digest,
    canonical_json_bytes,
)
from simulate.services.scoring_status import (
    TRANSPORT_RUN_STATUSES,
    settle_run_after_commit,
)
from tracer.services.observability_switch import (
    ObservabilityRefused,
    refusal_body,
    set_agent_observability,
)

_CAPABILITY_SCHEMA_VERSION = "futureagi.harness-capabilities.v1"
_JOB_SCHEMA_VERSION = "futureagi.harness-job.v1"
_TOKEN_TAIL_SECONDS = 120 + 300

logger = structlog.get_logger(__name__)


def _active_attempt_budget_seconds(job: HostedHarnessJob) -> int:
    """Budget an active capability for authoring plus scenario execution."""

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
    return authoring_seconds + int(job.payload["runtime"]["max_duration_seconds"])


class HostedHarnessError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        # Extra fields an error's payload carries beside the standard three.
        self.details = details

    def as_dict(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "message": self.message,
            "retryable": self.retryable,
            **(self.details or {}),
        }


@dataclass(frozen=True)
class AttemptCapability:
    attempt: HostedHarnessAttempt
    token: str
    fence: str
    document: dict[str, Any]
    admitted_parallelism: int


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def parallelism_w_gt_1_enabled(snapshot_digest: str | None) -> bool:
    """The ONE shared W>1 admission predicate (C4 §5, decisions D12/D23/D24).

    W>1 is admitted only when the ``HARNESS_PARALLELISM_ENABLED`` flag is truthy
    AND the selected guest runtime identifier is certified by
    ``HARNESS_PARALLEL_SNAPSHOT_DIGESTS``. In the dockerfile-mode dev lane
    (``ALK_DAYTONA_DOCKERFILE`` set) the digest half is skipped — that lane
    carries no meaningful registered digest — so W>1 needs the FLAG only. In the
    production snapshot lane an empty/unset digest FAILS CLOSED (never matches
    the allowlist). This single predicate backs both the ``register_attempt``
    authoritative gate and the create-time serializer / preflight advisory.
    """
    if not getattr(settings, "HARNESS_PARALLELISM_ENABLED", False):
        return False
    from simulate.services.hosted_sandbox import sandbox_runtime_policy

    if sandbox_runtime_policy().permits_unpinned_parallelism:
        return True
    digest = (snapshot_digest or "").strip()
    if not digest:
        return False
    allowlist = set(getattr(settings, "HARNESS_PARALLEL_SNAPSHOT_DIGESTS", ()) or ())
    return digest in allowlist


def clamp_parallelism(
    requested: int | None, snapshot_digest: str | None
) -> tuple[int, bool]:
    """Return ``(admitted, clamped)`` for a requested parallelism.

    A request of W>1 is admitted at 1 (``clamped=True``) whenever the shared
    predicate denies it (flag off OR digest unlisted). W<=1 is always admitted
    unchanged. The caller preserves the requested value separately so a later
    rerun re-evaluates against the then-current flag/digest.
    """
    value = requested or 1
    if value > 1 and not parallelism_w_gt_1_enabled(snapshot_digest):
        return 1, True
    admitted = min(value, getattr(settings, "HARNESS_MAX_WORLD_SLOTS", 8), 8)
    return admitted, admitted != value


def find_replayed_job(
    organization,
    payload: dict[str, Any],
    *,
    idempotency_key: str,
    workspace=None,
    lock: bool = False,
) -> HostedHarnessJob | None:
    """The job this idempotency key already made for the same request, if any."""
    request_digest = canonical_digest(payload)
    jobs = HostedHarnessJob.no_workspace_objects.filter(
        organization=organization,
        idempotency_key=idempotency_key,
    )
    existing = (jobs.select_for_update() if lock else jobs).first()
    if existing is None:
        return None
    if existing.workspace_id != getattr(workspace, "id", None):
        raise HostedHarnessError(
            "idempotency_conflict",
            "the idempotency key was already used in a different workspace",
            status_code=409,
        )
    if existing.request_digest != request_digest:
        raise HostedHarnessError(
            "idempotency_conflict",
            "the idempotency key was already used for a different request",
            status_code=409,
        )
    return existing


def idempotency_lock_name(organization, idempotency_key: str) -> str:
    return f"harness-job:{organization.id}:{idempotency_key}"


def create_hosted_job(
    organization,
    payload: dict[str, Any],
    *,
    idempotency_key: str,
    workspace=None,
    on_create: Callable[[HostedHarnessJob], None] | None = None,
) -> tuple[HostedHarnessJob, bool]:
    """Create the job, or return the one this idempotency key already made.

    ``on_create`` runs once inside the transaction, for a new job only.
    """
    from simulate.services.harness_agent_versions import advisory_xact_lock

    request_digest = canonical_digest(payload)
    with transaction.atomic():
        # Same-key creates take turns, so a concurrent one replays the first's job.
        advisory_xact_lock(idempotency_lock_name(organization, idempotency_key))
        existing = find_replayed_job(
            organization,
            payload,
            idempotency_key=idempotency_key,
            workspace=workspace,
            lock=True,
        )
        if existing is not None:
            return existing, False

        run_id = payload.get("run_id") or uuid.uuid4()
        seed = payload.get("seed")
        if seed is None:
            seed = secrets.randbits(63)
        normalized = _json_value(payload)
        # Deployment policy must not change the submitted request's idempotency digest.
        artifacts = normalized["artifacts"]
        artifacts["max_artifact_bytes"] = max(
            artifacts.get("max_artifact_bytes", 0), settings.HARNESS_MAX_ARTIFACT_BYTES
        )
        normalized.update(
            {
                "schema_version": _JOB_SCHEMA_VERSION,
                "job_id": None,
                "run_id": str(run_id),
                "execution": "hosted",
                "seed": seed,
            }
        )
        now = timezone.now()
        duration = normalized["runtime"]["max_duration_seconds"]
        job = HostedHarnessJob.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            run_id=run_id,
            idempotency_key=idempotency_key,
            request_digest=request_digest,
            schema_version=_JOB_SCHEMA_VERSION,
            payload=normalized,
            state=HostedHarnessJob.State.QUEUED,
            seed=seed,
            scenario_count=normalized["scenario_count"],
            artifact_level=normalized["artifacts"]["level"],
            max_artifact_bytes=normalized["artifacts"]["max_artifact_bytes"],
            deadline_at=now + timedelta(seconds=duration),
            content_updated_at=now,
        )
        normalized["job_id"] = str(job.id)
        job.payload = normalized
        job.save(update_fields=["payload", "updated_at"])
        if on_create is not None:
            on_create(job)
        HostedHarnessConversation.no_workspace_objects.create(
            job=job,
            organization=organization,
            workspace=workspace,
            current_stage="reception",
        )
        return job, True


def create_selected_harness_run(
    environment: HostedHarnessJob,
    *,
    scenario_keys: list[str],
    trials: int,
    idempotency_key: str,
    rerun_of: HostedHarnessJob | None = None,
    picks: VersionPicks | None = None,
) -> tuple[HostedHarnessJob, bool]:
    """Freeze and preallocate one selected-scenario Run for an authored environment.

    A rerun of a pinned run (``rerun_of``) keeps that run's pins; ``picks`` win over
    both, per piece.
    """

    if environment.environment_id is not None:
        raise HostedHarnessError(
            "environment_required",
            "simulation Runs can only be submitted from an authored environment",
            status_code=409,
        )
    if environment.state != HostedHarnessJob.State.COMPLETED:
        raise HostedHarnessError(
            "environment_not_ready",
            "environment authoring must complete before simulations can run",
            status_code=409,
        )
    metadata = dict((environment.payload or {}).get("metadata") or {})
    if not environment.run_test_id or not metadata.get("authoring_object_key"):
        raise HostedHarnessError(
            "environment_snapshot_missing",
            "environment has no durable validated snapshot",
            status_code=409,
        )
    if not scenario_keys:
        raise HostedHarnessError(
            "scenario_selection_empty",
            "select at least one scenario",
            status_code=400,
        )
    if len(scenario_keys) != len(set(scenario_keys)):
        raise HostedHarnessError(
            "scenario_selection_duplicate",
            "scenario selection must not contain duplicates",
            status_code=400,
        )
    if trials < 1 or trials > 20:
        raise HostedHarnessError(
            "trials_out_of_range",
            "trials must be between 1 and 20",
            status_code=400,
        )
    max_executions = int(getattr(settings, "HARNESS_MAX_EXECUTIONS_PER_RUN", 200))
    if len(scenario_keys) * trials > max_executions:
        raise HostedHarnessError(
            "run_too_large",
            f"selected scenarios × trials must not exceed {max_executions}",
            status_code=400,
        )

    with transaction.atomic():
        try:
            environment = HostedHarnessJob.no_workspace_objects.select_for_update().get(
                id=environment.id
            )
        except HostedHarnessJob.DoesNotExist as exc:
            raise HostedHarnessError(
                "environment_not_ready",
                "environment was deleted while submitting this Run",
                status_code=409,
            ) from exc
        if (
            environment.deleted
            or environment.state != HostedHarnessJob.State.COMPLETED
            or not environment.run_test_id
            or not (environment.payload.get("metadata") or {}).get(
                "authoring_object_key"
            )
        ):
            raise HostedHarnessError(
                "environment_not_ready",
                "environment changed while submitting this Run",
                status_code=409,
            )
        idempotency_digest = hashlib.sha256(idempotency_key.encode()).hexdigest()
        child_idempotency_key = f"run:{environment.id}:{idempotency_digest}"
        resolved = resolve_run_pins(environment, rerun_of=rerun_of, picks=picks)
        if resolved is not None:
            # A repeated request is answered before anything is written.
            replayed = _replayed_run(
                environment, child_idempotency_key, resolved, scenario_keys, trials
            )
            if replayed is not None:
                return replayed, False
        pins = _pinned_versions(environment, resolved)
        if pins is None:
            registrations = list(
                HostedHarnessScenario.no_workspace_objects.filter(
                    job=environment, scenario_key__in=scenario_keys
                )
                .select_related("scenario", "dataset_row")
                .order_by("created_at", "id")
            )
        else:
            registrations = _member_registrations(environment, pins, scenario_keys)
        by_key = {
            registration.scenario_key: registration for registration in registrations
        }
        missing = [key for key in scenario_keys if key not in by_key]
        if missing and pins is not None:
            # A rerun of an environment or of an unpinned run selects keys the default
            # suite may have dropped since; a rerun of a pinned run keeps its suite.
            from simulate.services.harness_versioning.read import _label, _message_label

            suite_label = _message_label(_label(pins.suite_version.version_number))
            where = (
                "this environment's current scenarios; select from those it has now"
                if pins.suite_version.id == environment.suite_version_id
                else f"scenario version {suite_label};"
                " select from the scenarios that version has"
            )
            raise HostedHarnessError(
                "scenario_selection_unknown",
                ", ".join(missing)
                + (" is" if len(missing) == 1 else " are")
                + f" not in {where}",
                status_code=400,
            )
        if missing:
            raise HostedHarnessError(
                "scenario_selection_unknown",
                f"scenario keys do not belong to this environment: {missing}",
                status_code=400,
            )
        if pins is not None and pins.assembles:
            _refuse_ungradable(pins, scenario_keys)

        manifest: list[dict[str, Any]] = []
        for scenario_key in scenario_keys:
            registration = by_key[scenario_key]
            for trial_index in range(1, trials + 1):
                execution_key = uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    (
                        f"futureagi:harness-run:{environment.id}:"
                        f"{idempotency_digest}:{registration.id}:{trial_index}"
                    ),
                ).hex
                manifest.append(
                    {
                        "execution_key": execution_key,
                        "scenario_key": registration.scenario_key,
                        "scenario_id": str(registration.scenario_id),
                        "dataset_row_id": (
                            str(registration.dataset_row_id)
                            if registration.dataset_row_id
                            else None
                        ),
                        "trial_index": trial_index,
                    }
                )

        from simulate.services.harness_agent_versions import (
            apply_agent_version,
            own_credential_set,
        )

        child_payload = json.loads(json.dumps(environment.payload))
        if (
            pins is not None
            and pins.runtime is not None
            and own_credential_set(pins.runtime)
        ):
            # A version with its own credential set runs its own agent and source; a
            # legacy one mirrors the environment's payload.
            apply_agent_version(
                child_payload, pins.runtime, environment_id=environment.id
            )
        elif (
            pins is not None
            and pins.rerun_of is not None
            and rerun_of is not None
            and pins.agent_version_id == rerun_of.agent_version_id
        ):
            # The agent source and config the pinned bundle was built from.
            for name in ("source", "agent"):
                value = (rerun_of.payload or {}).get(name)
                if value:
                    child_payload[name] = json.loads(json.dumps(value))
        child_payload["scenario_count"] = len(manifest)
        runtime = child_payload["runtime"]
        runtime["max_duration_seconds"] = max(
            runtime["max_duration_seconds"], len(manifest) * 360
        )
        child_metadata = child_payload.setdefault("metadata", {})
        for key in (
            "scenario_extend",
            "usage_limit",
            "harness_spend",
            "authoring_usage_reports",
            "usage_reports",
            "sandbox_runtime",
            "default_pairs",
            "default_pair_sealed",
            "conversation_checkpoints",
            "draft_saves",
            "observability_refusal",
        ):
            child_metadata.pop(key, None)
        child_metadata.update(
            {
                "environment_job_id": str(environment.id),
                "simulation_only": True,
                "execution_manifest": manifest,
                "selected_scenario_keys": scenario_keys,
                "trials": trials,
                "scenario_edits": (
                    _scenario_edits(environment, registrations)
                    if pins is None
                    else _member_scenario_edits(pins, scenario_keys)
                ),
            }
        )
        if pins is not None:
            child_metadata["version_pins"] = pins.as_metadata()
            if pins.assembly is not None:
                child_metadata["assembly"] = pins.assembly.as_metadata()
            if pins.rerun_of is not None:
                child_metadata["rerun_of"] = pins.rerun_of
        child_payload.pop("job_id", None)
        child_payload.pop("run_id", None)

        child, created = create_hosted_job(
            environment.organization,
            child_payload,
            idempotency_key=child_idempotency_key,
            workspace=environment.workspace,
        )
        if not created:
            return child, False

        scenario_ids = list(dict.fromkeys(entry["scenario_id"] for entry in manifest))
        test_execution = TestExecution.no_workspace_objects.create(
            run_test=environment.run_test,
            status=TestExecution.ExecutionStatus.PENDING,
            started_at=timezone.now(),
            total_scenarios=len(scenario_keys),
            trials=trials,
            scenario_ids=scenario_ids,
            total_calls=len(manifest),
            picked_up_by_executor=True,
            simulator_agent=environment.run_test.simulator_agent,
            # An environment without an agent pin runs the agent the run test
            # names, as before versioning.
            agent_definition=(
                pins.agent_version.agent_definition
                if pins is not None and pins.agent_version is not None
                else environment.run_test.agent_definition
            ),
            agent_version=(
                pins.agent_version
                if pins is not None and pins.agent_version is not None
                else _unpinned_agent_version(environment)
            ),
            execution_metadata={
                "environment_job_id": str(environment.id),
                "harness_job_id": str(child.id),
                "selected_scenario_keys": scenario_keys,
                "trials": trials,
                "harness_executions": manifest,
            },
        )
        call_ids = precreate_alk_sim_call_executions(test_execution)
        calls = CallExecution.no_workspace_objects.filter(id__in=call_ids)
        calls_by_key = {
            (call.call_metadata or {}).get("harness_execution_key"): call
            for call in calls
        }
        if set(calls_by_key) != {entry["execution_key"] for entry in manifest}:
            raise HostedHarnessError(
                "run_preallocation_incomplete",
                "could not preallocate every selected scenario trial",
                status_code=500,
                retryable=True,
            )
        for entry in manifest:
            entry["call_execution_id"] = str(calls_by_key[entry["execution_key"]].id)
        child_payload = dict(child.payload)
        child_metadata = dict(child_payload.get("metadata") or {})
        child_metadata["execution_manifest"] = manifest
        child_payload["metadata"] = child_metadata
        child.payload = child_payload
        execution_metadata = dict(test_execution.execution_metadata or {})
        execution_metadata["harness_executions"] = manifest
        test_execution.execution_metadata = execution_metadata
        test_execution.save(update_fields=["execution_metadata", "updated_at"])
        HostedHarnessExecution.no_workspace_objects.bulk_create(
            [
                HostedHarnessExecution(
                    job=child,
                    source_scenario=by_key[entry["scenario_key"]],
                    execution_key=entry["execution_key"],
                    trial_index=entry["trial_index"],
                    call_execution=calls_by_key[entry["execution_key"]],
                )
                for entry in manifest
            ]
        )
        child.environment = environment
        child.run_test = environment.run_test
        child.stage_outputs = list(environment.stage_outputs or [])
        child.bundle_digest = environment.bundle_digest
        child.test_execution = test_execution
        pin_fields: list[str] = []
        if pins is not None:
            child.stage_outputs = pins.stage_outputs(child.stage_outputs)
            child.agent_version = pins.agent_version
            child.environment_version = pins.environment_version
            child.suite_version = pins.suite_version
            child.assembled_bundle = pins.assembled_bundle
            pin_fields = [
                "agent_version",
                "environment_version",
                "suite_version",
                "assembled_bundle",
            ]
        child.save(
            update_fields=[
                "payload",
                "environment",
                "run_test",
                "test_execution",
                "updated_at",
                "stage_outputs",
                "bundle_digest",
                *pin_fields,
            ]
        )
        return child, True


@dataclass(frozen=True)
class _RunPins:
    """The versions a new run of a versioned environment is pinned to."""

    agent_version: Any
    environment_version: Any
    suite_version: Any
    members: dict[str, Any]
    agent_hash: str | None
    assembly: Any = None
    rerun_of: str | None = None
    agent_version_id: Any = None
    # The pinned agent version's side row.
    runtime: Any = None

    @property
    def assembled_bundle(self) -> Any:
        return self.assembly.bundle if self.assembly is not None else None

    @property
    def assembles(self) -> bool:
        """Whether the run replays or certifies an assembled bundle; a miss of the
        default pins runs them as before, unless their pair holds no sealed bundle."""
        from simulate.services.harness_versioning.assembly import certify_enabled

        return self.assembly is not None and (
            self.assembly.cached
            or (
                (not self.assembly.defaults or self.assembly.unsealed)
                and certify_enabled()
            )
        )

    def as_metadata(self) -> dict[str, Any]:
        pins = {
            "agent_version_id": (
                str(self.agent_version.id) if self.agent_version is not None else None
            ),
            "environment_version_id": str(self.environment_version.id),
            "suite_version_id": str(self.suite_version.id),
            "agent_hash": self.agent_hash,
            "environment_hash": self.environment_version.hash,
            "suite_hash": self.suite_version.hash,
        }
        if self.assembly is not None:
            pins["assembly_key"] = self.assembly.key
        return pins

    def stage_outputs(self, current: list[Any]) -> list[Any]:
        """The run's stage outputs, with the pinned versions' authored snapshots in place.

        Only the kinds a chat save replaces are overlaid; the environment output keeps the
        sealed bundle's manifest the run replays.
        """
        return overlay_stage_outputs(
            current, pinned_stage_outputs(self.environment_version, self.suite_version)
        )


def pinned_stage_outputs(
    environment_version: Any, suite_version: Any
) -> dict[str, dict[str, Any]]:
    """The versions' authored snapshots of the kinds a chat save replaces, by kind."""
    from simulate.services.hosted_harness_conversation import _CHECKPOINT_STAGE_KINDS

    pinned: dict[str, dict[str, Any]] = {}
    for version in (environment_version, suite_version):
        for output in (version.documents or {}).get("stage_outputs") or []:
            if (
                isinstance(output, dict)
                and output.get("kind") in _CHECKPOINT_STAGE_KINDS
            ):
                pinned[output["kind"]] = output
    return pinned


def overlay_stage_outputs(
    current: list[Any], pinned: Mapping[str, Any], *, drop: Iterable[str] = ()
) -> list[Any]:
    """``current`` with each pinned kind in place; other kinds in ``drop`` are left out."""
    pinned = dict(pinned)
    dropped = set(drop) - set(pinned)
    kept = [
        (
            pinned.pop(item["kind"])
            if isinstance(item, dict) and item.get("kind") in pinned
            else item
        )
        for item in current
        if not (isinstance(item, dict) and item.get("kind") in dropped)
    ]
    return kept + list(pinned.values())


@dataclass(frozen=True)
class VersionPicks:
    """Versions a new run asks for in place of the environment's defaults."""

    agent_version_id: Any = None
    environment_version_id: Any = None
    suite_version_id: Any = None

    @property
    def chosen(self) -> bool:
        return any(self.ids())

    def ids(self) -> tuple[Any, Any, Any]:
        return (
            self.agent_version_id,
            self.environment_version_id,
            self.suite_version_id,
        )


@dataclass(frozen=True)
class ResolvedPins:
    """The versions a new run resolves to, read without writing anything."""

    agent_version: Any
    environment_version: Any
    suite_version: Any
    runtime: Any
    # The pinned agent id, kept when its version was soft-deleted since.
    agent_version_id: Any
    defaults: bool
    # Whether the combination's bundle is cached; ``None`` without an agent side row.
    cached: bool | None
    rerun_of: str | None = None
    # The defaults' pair holds no sealed bundle, so a run of them certifies it.
    unsealed: bool = False
    # The cache key a rerun keeps from its source; others are computed from the agent.
    assembly_key: str | None = None


def _source_bundle(environment: HostedHarnessJob, source: HostedHarnessJob) -> Any:
    """The cache row a rerun's source was bound to, read as recorded: the facts its
    key was made from may have changed in the environment since."""
    from simulate.models import HarnessAssembledBundle

    if not source.assembled_bundle_id:
        return None
    return HarnessAssembledBundle._base_manager.filter(
        id=source.assembled_bundle_id, job_id=environment.id
    ).first()


def _replayable(bundle: Any) -> bool:
    from simulate.models import HarnessAssembledBundle

    return bundle.state == HarnessAssembledBundle.State.READY and bool(
        bundle.object_key
    )


def resolve_run_pins(
    environment: HostedHarnessJob,
    *,
    rerun_of: HostedHarnessJob | None = None,
    picks: VersionPicks | None = None,
) -> ResolvedPins | None:
    """The versions a new run would be pinned to, when versioning pins runs.

    Per piece a pick wins, then a rerun's kept pin, then the environment's default. A
    rerun keeps its source's pins when they can run: they are the defaults, their
    bundle is cached, or certifying is on; otherwise it runs the defaults. Picks that
    cannot run are refused rather than run as the defaults.
    """
    from simulate.services.harness_versioning.assembly import (
        bundle_ready,
        certify_enabled,
    )
    from simulate.services.harness_versioning.versions import pins_runs

    chosen = picks is not None and picks.chosen
    if not pins_runs(environment):
        if chosen:
            raise HostedHarnessError(
                "version_not_pickable",
                "Versions can't be picked for this environment",
                status_code=422,
                details={"piece": None},
            )
        return None
    defaults = _pin_ids(environment)
    source, loaded, kept = environment, None, None
    if (
        rerun_of is not None
        and rerun_of.environment_id == environment.id
        and rerun_of.environment_version_id is not None
        and rerun_of.suite_version_id is not None
    ):
        loaded = _load_pins(environment, rerun_of)
        _, environment_version, suite_version, runtime = loaded
        kept = _source_bundle(environment, rerun_of)
        if _pin_ids(rerun_of) == defaults or (
            runtime is not None
            and (
                certify_enabled()
                or (
                    _replayable(kept)
                    if kept is not None
                    else bundle_ready(
                        environment, runtime, environment_version, suite_version
                    )
                )
            )
        ):
            source = rerun_of
        else:
            loaded = None
            logger.info(
                "harness_rerun_pins_fallback",
                environment_id=str(environment.id),
                rerun_of=str(rerun_of.id),
            )
    ids = _pin_ids(source)
    if chosen:
        _check_picks(environment, picks, defaults)
        picked = tuple(
            pick or kept for pick, kept in zip(picks.ids(), ids, strict=True)
        )
        if picked != ids:
            ids, loaded = picked, None
    if loaded is None:
        loaded = _load_ids(environment, ids)
    agent_version, environment_version, suite_version, runtime = loaded
    # A rerun of its source's very pins replays the bundle its source ran.
    if source is not rerun_of or ids != _pin_ids(rerun_of) or runtime is None:
        kept = None
    cached = (
        (
            _replayable(kept)
            if kept is not None
            else bundle_ready(environment, runtime, environment_version, suite_version)
        )
        if runtime is not None
        else None
    )
    # An agent with the default's bundle key runs the default pair's sealed bundle.
    at_defaults = ids == defaults or replays_default_pair(environment, *ids)
    unsealed = at_defaults and default_pair_unsealed(environment)
    runnable = bool(cached) or (runtime is not None and certify_enabled())
    if unsealed and not runnable:
        raise HostedHarnessError(
            "version_not_runnable",
            (
                "These versions can't be run together yet"
                if runtime is None
                else "These versions haven't been prepared yet and building them is"
                " turned off"
            ),
            status_code=422,
        )
    if chosen and not at_defaults and not runnable:
        raise HostedHarnessError(
            "version_not_runnable",
            (
                "These versions can't be run together yet"
                if runtime is None
                else "These versions haven't been run together yet and building"
                " them is turned off"
            ),
            status_code=422,
        )
    return ResolvedPins(
        agent_version=agent_version,
        environment_version=environment_version,
        suite_version=suite_version,
        runtime=runtime,
        agent_version_id=ids[0],
        defaults=at_defaults,
        cached=cached,
        rerun_of=str(rerun_of.id) if source is rerun_of else None,
        unsealed=unsealed,
        assembly_key=kept.assembly_key if kept is not None else None,
    )


def default_pair_unsealed(environment: HostedHarnessJob) -> bool:
    """Whether the environment's default pair was made without a sealed bundle."""
    metadata = (environment.payload or {}).get("metadata") or {}
    return metadata.get("default_pair_sealed") is False


def replays_default_pair(
    environment: HostedHarnessJob,
    agent_version_id: Any,
    environment_version_id: Any,
    suite_version_id: Any,
) -> bool:
    """Whether a run of these versions replays the default pair's sealed bundle: the
    pair is the default and the agent has the default agent's bundle key."""
    from simulate.services.harness_versioning.assembly import same_bundle_agent

    return (
        (environment_version_id, suite_version_id)
        == (environment.environment_version_id, environment.suite_version_id)
        and environment_version_id is not None
        and not default_pair_unsealed(environment)
        and same_bundle_agent(
            agent_version_id, environment.agent_version_id, environment
        )
    )


def _unpinned_agent_version(environment: HostedHarnessJob) -> Any:
    """The run test's agent version, else the environment's own for an agent made
    with environments, else its agent's active version, else its latest; with
    versioning on, never one with a credential set of its own: those are made for
    one environment."""
    from simulate.models import HarnessAgentVersionRuntime
    from simulate.services.harness_agent_versions import environment_owned
    from simulate.services.harness_versioning.versions import versioning_enabled

    run_test = environment.run_test
    if run_test.agent_version is not None or run_test.agent_definition is None:
        return run_test.agent_version
    if environment_owned(run_test.agent_definition_id):
        # Its versions belong to the environments sharing it: run this one's default.
        return environment.agent_version
    if not versioning_enabled():
        return resolved_agent_version(run_test)
    held = HarnessAgentVersionRuntime.no_workspace_objects.exclude(
        preflight_status=HarnessAgentVersionRuntime.PreflightStatus.LEGACY
    ).values("agent_version_id")
    # TODO: when another environment holds the active version, this falls to the
    # latest unheld one instead of the active.
    unheld = run_test.agent_definition.versions.exclude(id__in=held)
    return (
        unheld.filter(status=AgentVersion.StatusChoices.ACTIVE)
        .order_by("-version_number")
        .first()
        or unheld.order_by("-version_number").first()
    )


def _not_pickable(piece: str) -> HostedHarnessError:
    # Every refused pick of a piece reads the same, so its reason is not revealed.
    return HostedHarnessError(
        "version_not_pickable",
        f"This {piece} version can't be picked for this environment",
        status_code=422,
        details={"piece": piece},
    )


def agent_version_pickable(environment: HostedHarnessJob, agent_id: Any) -> bool:
    """A live version of the environment's agent, in its workspace, whose check
    passed, or a legacy or pre-versioning one."""
    from simulate.models import AgentVersion, HarnessAgentVersionRuntime
    from simulate.services.harness_agent_versions import lineage_version_ids
    from simulate.services.harness_versioning.read import (
        _agent_definition_id,
        in_workspace,
    )

    definition_id = _agent_definition_id(environment)
    if (
        definition_id is None
        or not AgentVersion.no_workspace_objects.filter(
            in_workspace(environment),
            id=agent_id,
            organization_id=environment.organization_id,
            agent_definition_id=definition_id,
        ).exists()
    ):
        return False
    # An agent environments share: only this environment's own versions.
    allowed = lineage_version_ids(environment, definition_id)
    if allowed is not None and str(agent_id) not in allowed:
        return False
    status = (
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version_id=agent_id
        )
        .values_list("preflight_status", flat=True)
        .first()
    )
    return status is None or status in (
        HarnessAgentVersionRuntime.PreflightStatus.PASSED,
        HarnessAgentVersionRuntime.PreflightStatus.LEGACY,
    )


def _check_picks(
    environment: HostedHarnessJob, picks: VersionPicks, defaults: tuple[Any, Any, Any]
) -> None:
    """Refuse a pick that is not one of this environment's runnable versions.

    A pick of the default is the default, so a dialog that sends it back always runs.
    """
    from simulate.models import HarnessEnvironmentVersion, HarnessSuiteVersion
    from simulate.models.harness_versioning import VersionStatus
    from simulate.services.harness_agent_versions import require_agent_pick

    agent_id, environment_id, suite_id = picks.ids()
    for piece, model, pick, default in (
        ("environment", HarnessEnvironmentVersion, environment_id, defaults[1]),
        ("suite", HarnessSuiteVersion, suite_id, defaults[2]),
    ):
        if pick and pick != default:
            if not model.no_workspace_objects.filter(
                job=environment, id=pick, status=VersionStatus.ACTIVE
            ).exists():
                raise _not_pickable(piece)
    if not agent_id or agent_id == defaults[0]:
        return
    require_agent_pick(
        environment,
        agent_id,
        refusal="This older agent version can't be run on its own",
    )


def job_pins(
    environment: HostedHarnessJob, job: HostedHarnessJob
) -> ResolvedPins | None:
    """The versions a run job was pinned to, read like a resolved run; ``None`` when
    it was not pinned.

    Whether they were the defaults and cached is read as the run recorded it when
    created, so a repeated request reads the same.
    """
    from simulate.services.harness_versioning.assembly import bundle_ready

    if job.environment_version_id is None or job.suite_version_id is None:
        return None
    ids = _pin_ids(job)
    agent_version, environment_version, suite_version, runtime = _load_ids(
        environment, ids
    )
    recorded = ((job.payload or {}).get("metadata") or {}).get("assembly") or {}
    defaults = recorded.get("defaults")
    cached = recorded.get("cached")
    return ResolvedPins(
        assembly_key=recorded.get("key") or None,
        unsealed=recorded.get("default_pair_sealed") is False,
        agent_version=agent_version,
        environment_version=environment_version,
        suite_version=suite_version,
        runtime=runtime,
        agent_version_id=ids[0],
        defaults=(
            defaults if isinstance(defaults, bool) else ids == _pin_ids(environment)
        ),
        cached=(
            cached
            if isinstance(cached, bool) or runtime is None
            else bundle_ready(environment, runtime, environment_version, suite_version)
        ),
    )


def _replayed_run(
    environment: HostedHarnessJob,
    idempotency_key: str,
    resolved: ResolvedPins,
    scenario_keys: list[str],
    trials: int,
) -> HostedHarnessJob | None:
    """The run an earlier request with this key created, when this one repeats it.

    A key reused for other versions, scenarios or trials is refused.
    """
    existing = HostedHarnessJob.no_workspace_objects.filter(
        organization_id=environment.organization_id, idempotency_key=idempotency_key
    ).first()
    if existing is None:
        return None
    # By id, so an agent soft-deleted since still reads as the same pin; a run created
    # after the deletion pinned no agent.
    same_agent = existing.agent_version_id in (
        resolved.agent_version_id,
        getattr(resolved.agent_version, "id", None),
    )
    if not same_agent or _pin_ids(existing)[1:] != (
        resolved.environment_version.id,
        resolved.suite_version.id,
    ):
        raise HostedHarnessError(
            "idempotency_key_reused",
            "This request key was already used for a run of other versions; start a"
            " new run",
            status_code=409,
        )
    metadata = (existing.payload or {}).get("metadata") or {}
    if (
        existing.workspace_id != environment.workspace_id
        or metadata.get("selected_scenario_keys") != scenario_keys
        or metadata.get("trials") != trials
    ):
        raise HostedHarnessError(
            "idempotency_conflict",
            "the idempotency key was already used for a different request",
            status_code=409,
        )
    return existing


def _pinned_versions(
    environment: HostedHarnessJob, resolved: ResolvedPins | None
) -> _RunPins | None:
    """The versions a new run is pinned to, with its bundle's cache row.

    With the flag off a run is created exactly as before, versions or not, so turning
    versioning on is what changes which agent and suite a run uses. Call under the
    environment's lock: the cache row is created when the combination is new.
    """
    from simulate.models import HarnessSuiteMember
    from simulate.services.harness_versioning.assembly import run_assembly

    if resolved is None:
        return None
    members = {
        member.scenario_key: member
        for member in HarnessSuiteMember.no_workspace_objects.filter(
            suite_version=resolved.suite_version
        ).order_by("number", "scenario_key")
    }
    return _RunPins(
        agent_version=resolved.agent_version,
        environment_version=resolved.environment_version,
        suite_version=resolved.suite_version,
        members=members,
        agent_hash=resolved.runtime.hash if resolved.runtime is not None else None,
        assembly=run_assembly(
            environment,
            resolved.runtime,
            resolved.environment_version,
            resolved.suite_version,
            defaults=resolved.defaults,
            unsealed=resolved.unsealed,
            key=resolved.assembly_key,
        ),
        rerun_of=resolved.rerun_of,
        agent_version_id=resolved.agent_version_id,
        runtime=resolved.runtime,
    )


def _pin_ids(job: HostedHarnessJob) -> tuple[Any, Any, Any]:
    return (job.agent_version_id, job.environment_version_id, job.suite_version_id)


def _load_pins(
    environment: HostedHarnessJob, source: HostedHarnessJob
) -> tuple[Any, Any, Any, Any]:
    """The agent, environment and suite versions ``source`` is pinned to, and the agent's
    side row."""
    return _load_ids(environment, _pin_ids(source))


def _load_ids(
    environment: HostedHarnessJob, ids: tuple[Any, Any, Any]
) -> tuple[Any, Any, Any, Any]:
    """The agent, environment and suite versions of these ids, and the agent's side
    row with the facts it runs with in ``environment``: a shared legacy row stands
    for the environment's own."""
    from simulate.models import (
        AgentVersion,
        HarnessAgentVersionRuntime,
        HarnessEnvironmentVersion,
        HarnessSuiteVersion,
    )

    agent_version_id, environment_version_id, suite_version_id = ids
    environment_version = HarnessEnvironmentVersion._base_manager.get(
        id=environment_version_id
    )
    suite_version = HarnessSuiteVersion._base_manager.get(id=suite_version_id)
    # A soft-deleted agent version is never pinned onto a run.
    agent_version = (
        AgentVersion.no_workspace_objects.select_related("agent_definition")
        .filter(id=agent_version_id)
        .first()
        if agent_version_id
        else None
    )
    if agent_version_id and agent_version is None:
        logger.warning(
            "harness_run_agent_pin_missing",
            environment_id=str(environment.id),
            agent_version_id=str(agent_version_id),
        )
    from simulate.services.harness_versioning.backfill import effective_runtime

    runtime = (
        effective_runtime(
            environment,
            HarnessAgentVersionRuntime.no_workspace_objects.filter(
                agent_version=agent_version
            ).first(),
        )
        if agent_version is not None
        else None
    )
    return agent_version, environment_version, suite_version, runtime


def _refuse_ungradable(pins: _RunPins, scenario_keys: list[str]) -> None:
    """Refuse selected scenarios that grade no sub-goal of the pinned environment.

    Certifying drops them from the bundle, so the run could not play them. A member
    without a ``sub_goals`` field predates them and is left to the run.
    """
    catalogue = {
        str(goal.get("name"))
        for goal in (pins.environment_version.documents or {}).get("sub_goals") or []
        if isinstance(goal, dict) and goal.get("name")
    }
    if not catalogue:
        return
    ungradable = []
    for key in scenario_keys:
        sub_goals = (
            (pins.members[key].content or {}) if key in pins.members else {}
        ).get("sub_goals")
        if not isinstance(sub_goals, list):
            continue
        names = {
            str(goal.get("name") if isinstance(goal, dict) else goal)
            for goal in sub_goals
        }
        if not names & catalogue:
            ungradable.append(key)
    if ungradable:
        raise HostedHarnessError(
            "scenario_ungradable",
            ", ".join(ungradable)
            + (" grades" if len(ungradable) == 1 else " grade")
            + " no sub-goal of this environment version, so the run cannot score it",
            status_code=422,
        )


def _member_registrations(
    environment: HostedHarnessJob, pins: _RunPins, scenario_keys: list[str]
) -> list[HostedHarnessScenario]:
    """The identity rows behind the selected members of the pinned suite.

    Only the pinned suite's scenarios can be selected. A member's row is loaded even if
    a later suite dropped it, and a member never given a platform scenario is bound
    first.
    """
    from simulate.services.hosted_harness_conversation import _bind_added_scenarios

    selected = [pins.members[key] for key in scenario_keys if key in pins.members]

    def load() -> list[HostedHarnessScenario]:
        return list(
            HostedHarnessScenario._base_manager.filter(
                job=environment, id__in=[member.scenario_id for member in selected]
            )
            .select_related("scenario", "dataset_row")
            .order_by("created_at", "id")
        )

    rows = load()
    if any(row.scenario_id is None for row in rows):
        # Binding gives the row a platform scenario and a row in the run test's grouped
        # dataset, which a run's calls are created from, even for a dropped member.
        try:
            _bind_added_scenarios(environment, rows)
        except HostedHarnessError as exc:
            if exc.code != "scenario_provision_failed":
                raise
            raise HostedHarnessError(
                "scenario_unavailable",
                "some selected scenarios can't be run yet; try again in a moment",
                status_code=409,
            ) from exc
        rows = load()
    if unbound := [row.scenario_key for row in rows if row.scenario_id is None]:
        raise HostedHarnessError(
            "scenario_unavailable",
            f"{', '.join(unbound)} can't be run yet; try again in a moment",
            status_code=409,
        )
    return rows


def _member_scenario_edits(
    pins: _RunPins, scenario_keys: list[str]
) -> dict[str, dict[str, Any]]:
    """Each selected scenario's editable fields as the pinned suite holds them."""
    from simulate.services.harness_provider import HostedHarnessProvider

    fields = (
        HostedHarnessProvider._DESCRIPTIVE_FIELDS
        | HostedHarnessProvider._BEHAVIOURAL_FIELDS
    )
    edits: dict[str, dict[str, Any]] = {}
    for key in scenario_keys:
        member = pins.members.get(key)
        content = member.content if member is not None else None
        if not isinstance(content, dict):
            continue
        edit = {field: content[field] for field in fields if field in content}
        persona = content.get("persona")
        if isinstance(persona, dict):
            edit["persona"] = {
                name: value
                for name, value in persona.items()
                if name in HostedHarnessProvider._PERSONA_FIELDS
            }
        if edit:
            edits[key] = edit
    return edits


def _scenario_edits(
    environment: HostedHarnessJob, registrations: list[HostedHarnessScenario]
) -> dict[str, dict[str, Any]]:
    """Each selected scenario's editable fields as the environment holds them now."""
    from simulate.models import HostedHarnessStageOutput
    from simulate.services.harness_provider import HostedHarnessProvider
    from simulate.services.hosted_harness_gateway import _scenario_token

    suite = (
        HostedHarnessStageOutput.no_workspace_objects.filter(
            job=environment, kind="scenarios"
        )
        .values_list("data", flat=True)
        .first()
    ) or next(
        (
            one.get("data")
            for one in (environment.stage_outputs or [])
            if isinstance(one, dict) and one.get("kind") == "scenarios"
        ),
        None,
    )
    by_token = {
        token: one
        for one in (suite if isinstance(suite, list) else [])
        if isinstance(one, dict)
        for field in ("name", "scenario_key")
        if (token := _scenario_token(one.get(field)))
    }
    fields = (
        HostedHarnessProvider._DESCRIPTIVE_FIELDS
        | HostedHarnessProvider._BEHAVIOURAL_FIELDS
    )
    edits: dict[str, dict[str, Any]] = {}
    for registration in registrations:
        document = next(
            (
                by_token[token]
                for value in (
                    registration.name,
                    registration.scenario_key,
                    registration.folder,
                )
                if (token := _scenario_token(value)) in by_token
            ),
            None,
        )
        if document is None:
            continue
        edit = {field: document[field] for field in fields if field in document}
        persona = document.get("persona")
        if isinstance(persona, dict):
            edit["persona"] = {
                key: value
                for key, value in persona.items()
                if key in HostedHarnessProvider._PERSONA_FIELDS
            }
        if edit:
            edits[registration.scenario_key] = edit
    return edits


# Job payload metadata naming the executions an in-run re-run chose. Every later attempt
# of the job is that re-run (infrastructure retries included), so launch dispatches only
# these and the job's counters treat every other receipt as still current.
RERUN_EXECUTION_KEYS = "rerun_execution_keys"


def rerun_calls_in_run(
    test_execution: TestExecution, call_execution_ids: list[uuid.UUID]
) -> HostedHarnessJob:
    """Re-run chosen calls of a finished Run as a new attempt of that same Run.

    Each call row is reset in place and refilled by the new attempt's receipt. The
    earlier result survives as a ``CallExecutionSnapshot`` and in the earlier attempt's
    ``receipt_history``; nothing about it is deleted.
    """
    from simulate.services.alk_simulate_ingestion import (
        PREALLOCATED_CALL_METADATA_KEYS,
    )
    from simulate.services.call_rerun import clear_call_for_rerun
    from simulate.temporal.client import (
        hosted_harness_gateway_workflow_running,
        start_hosted_harness_gateway_workflow,
    )

    base_url = str(getattr(settings, "HARNESS_PUBLIC_BASE_URL", "") or "").rstrip("/")
    if not base_url:
        raise HostedHarnessError(
            "public_base_url_missing",
            "HARNESS_PUBLIC_BASE_URL is required to rerun a hosted harness job",
            status_code=503,
        )
    if not call_execution_ids:
        raise HostedHarnessError(
            "call_selection_empty", "select at least one call", status_code=400
        )
    job_id = (
        HostedHarnessJob.no_workspace_objects.filter(
            test_execution_id=test_execution.id
        )
        .values_list("id", flat=True)
        .first()
    )
    if job_id is None:
        raise HostedHarnessError(
            "run_not_rerunnable",
            "this run has no saved harness job to re-run",
            status_code=409,
        )
    # The job and run turn terminal before the previous gateway workflow returns, and
    # starting its id again while it is still exiting would attach to it, not launch.
    try:
        workflow_running = hosted_harness_gateway_workflow_running(str(job_id))
    except Exception as exc:
        raise HostedHarnessError(
            "scheduler_unavailable",
            "Hosted rerun could not be scheduled",
            status_code=503,
            retryable=True,
        ) from exc
    if workflow_running:
        raise HostedHarnessError(
            "rerun_in_progress",
            "this run is still running or grading; re-run its calls once it finishes",
            status_code=409,
        )
    with transaction.atomic():
        job = HostedHarnessJob.no_workspace_objects.select_for_update().get(id=job_id)
        execution = TestExecution.no_workspace_objects.select_for_update().get(
            id=test_execution.id
        )
        # The job and the run settle separately: transport ends with the job, grading
        # with the run. A re-run waits for both so it never races either one.
        if job.state not in _TERMINAL_STATES or execution.status not in (
            TestExecution.ExecutionStatus.COMPLETED,
            TestExecution.ExecutionStatus.FAILED,
            TestExecution.ExecutionStatus.CANCELLED,
        ):
            raise HostedHarnessError(
                "rerun_in_progress",
                "this run is still running or grading; re-run its calls once it finishes",
                status_code=409,
            )
        allocations = list(
            HostedHarnessExecution.no_workspace_objects.filter(
                job=job, call_execution_id__in=call_execution_ids
            ).select_related("call_execution")
        )
        if len(allocations) != len(set(call_execution_ids)):
            raise HostedHarnessError(
                "call_selection_unknown",
                "some selected calls do not belong to this run",
                status_code=400,
            )
        for allocation in allocations:
            clear_call_for_rerun(
                allocation.call_execution,
                keep_metadata_keys=PREALLOCATED_CALL_METADATA_KEYS,
            )
        payload = dict(job.payload)
        payload["metadata"] = {
            **(payload.get("metadata") or {}),
            RERUN_EXECUTION_KEYS: sorted(
                allocation.execution_key for allocation in allocations
            ),
            # The re-run gets the infrastructure retry budget of a new Run.
            "attempt_cycle_start": job.current_attempt_number + 1,
        }
        job.payload = payload
        job.state = HostedHarnessJob.State.QUEUED
        job.current_stage = "queued"
        job.failure = None
        job.terminal_at = None
        job.cancel_requested_at = None
        job.cancel_reason = None
        job.save(
            update_fields=[
                "payload",
                "state",
                "current_stage",
                "failure",
                "terminal_at",
                "cancel_requested_at",
                "cancel_reason",
                "updated_at",
            ]
        )
        execution.status = TestExecution.ExecutionStatus.RUNNING
        execution.completed_at = None
        execution.error_reason = None
        execution.save(
            update_fields=["status", "completed_at", "error_reason", "updated_at"]
        )

    retry_cfg = job.payload["retry"]
    try:
        start_hosted_harness_gateway_workflow(
            str(job.id),
            base_url,
            retry_cfg["max_infrastructure_attempts"],
            retry_cfg["initial_backoff_seconds"],
            retry_cfg["max_backoff_seconds"],
        )
    except Exception as exc:
        with transaction.atomic():
            job = HostedHarnessJob.no_workspace_objects.select_for_update().get(
                id=job.id
            )
            now = timezone.now()
            job.state = HostedHarnessJob.State.FAILED
            job.current_stage = "failed"
            job.terminal_at = now
            job.failure = {
                "domain": "infrastructure",
                "stage": "queued",
                "code": "scheduler_unavailable",
                "message": "Hosted rerun could not be scheduled",
            }
            job.save(
                update_fields=[
                    "state",
                    "current_stage",
                    "terminal_at",
                    "failure",
                    "updated_at",
                ]
            )
            project_terminal_job_onto_run(job, now)
        raise HostedHarnessError(
            "scheduler_unavailable",
            "Hosted rerun could not be scheduled",
            status_code=503,
            retryable=True,
        ) from exc
    return job


def scope_dispatch_to_rerun(dispatch_payload: dict[str, Any]) -> dict[str, Any]:
    """Narrow a Run's dispatch document to the executions an in-run re-run chose."""

    metadata = dict(dispatch_payload.get("metadata") or {})
    chosen = set(metadata.pop(RERUN_EXECUTION_KEYS, None) or ())
    if not chosen:
        return dispatch_payload
    manifest = [
        entry
        for entry in metadata.get("execution_manifest") or []
        if entry["execution_key"] in chosen
    ]
    metadata["execution_manifest"] = manifest
    return {**dispatch_payload, "scenario_count": len(manifest), "metadata": metadata}


def _apply_parallelism_admission(
    job: HostedHarnessJob, snapshot_digest: str | None
) -> int:
    """Decide + record the W>1 admission under the shared guard; return admitted W.

    Reads the requested parallelism from ``job.payload.runtime.parallelism`` (the
    immutable stored intent) and, when the shared guard denies W>1, records
    ``metadata.parallelism_clamped = {"requested": N}`` so support and the FE can
    see it. When the request is admitted (W<=1, or W>1 that qualifies), any stale
    clamp marker from a prior attempt is cleared so a rerun that now qualifies
    drops the notice. The requested value itself is never rewritten.

    Returns the ADMITTED parallelism so ``register_attempt`` can surface it as the
    single admission source of truth — the gateway applies this returned value to
    the ephemeral guest job.json rather than independently re-running the guard.
    """
    runtime = job.payload.get("runtime") or {}
    requested = runtime.get("parallelism") or 1
    admitted, clamped = clamp_parallelism(requested, snapshot_digest)
    from simulate.services.harness_capacity import configured_capacity

    # An in-run re-run executes only its chosen calls, so W is bounded by those.
    capacity = configured_capacity(scope_dispatch_to_rerun(job.payload))
    admitted = min(admitted, capacity.parallelism)
    clamped = admitted != requested
    metadata = dict(job.payload.get("metadata") or {})
    changed = False
    if clamped:
        marker = {"requested": requested, "admitted": admitted}
        if metadata.get("parallelism_clamped") != marker:
            metadata["parallelism_clamped"] = marker
            changed = True
    elif "parallelism_clamped" in metadata:
        metadata.pop("parallelism_clamped")
        changed = True
    if changed:
        payload = dict(job.payload)
        payload["metadata"] = metadata
        job.payload = payload
        job.save(update_fields=["payload", "updated_at"])
    return admitted


BUILD_PIN_KEY = "build_agent_version_id"


def _freeze_build_pin(job: HostedHarnessJob) -> None:
    """Keep the agent version a build is tied to on its first attempt, so a retry and
    the write-back land on it even when the environment's default moves meanwhile."""
    if job.environment_id is not None or job.test_execution_id is not None:
        return
    from simulate.services.harness_agent_versions import agent_pin_candidate

    pinned = job.agent_version_id or getattr(agent_pin_candidate(job), "id", None)
    if pinned is None:
        return
    metadata = dict((job.payload or {}).get("metadata") or {})
    metadata[BUILD_PIN_KEY] = str(pinned)
    job.payload = {**(job.payload or {}), "metadata": metadata}


def frozen_build_pin(job: HostedHarnessJob) -> AgentVersion | None:
    """The version a build was frozen on, or None for a build made before pins."""
    pinned = ((job.payload or {}).get("metadata") or {}).get(BUILD_PIN_KEY)
    if not pinned:
        return None
    return AgentVersion._base_manager.filter(id=pinned).first()


def platform_set_failure(attempt: HostedHarnessAttempt) -> dict[str, Any] | None:
    """A failure the platform put on the attempt itself, which the guest's later
    terminal event or exit code must not replace."""
    failure = attempt.terminal_failure
    if (
        isinstance(failure, dict)
        and (failure.get("details") or {}).get("set_by") == "platform"
    ):
        return failure
    return None


def _fail_attempt_from_platform(
    attempt: HostedHarnessAttempt, *, stage: str, code: str, message: str
) -> None:
    attempt.terminal_stage = "failed"
    attempt.terminal_failure = {
        "domain": "infrastructure",
        "stage": stage,
        "code": code,
        "message": message,
        "retryable": False,
        "details": {"set_by": "platform"},
    }
    attempt.save(update_fields=["terminal_stage", "terminal_failure", "updated_at"])


def _check_subfolder_echo(
    attempt: HostedHarnessAttempt, payload: dict[str, Any]
) -> None:
    """A build that named a folder must hear the harness used it; an older harness
    that built the repository root instead is an infrastructure failure, not a result.
    """
    job = attempt.job
    expected = str((job.payload.get("source") or {}).get("subfolder") or "")
    sent = payload.get("agent")
    if not expected or not isinstance(sent, dict):
        return
    extension = (sent.get("agent") or {}).get("extension") or {}
    echoed = (extension.get("source") or {}).get("subfolder")
    if echoed == expected:
        return
    _fail_attempt_from_platform(
        attempt,
        stage="understanding_agent",
        code="subfolder_ignored",
        message=(
            f"the build asked for folder {expected!r} but the harness read "
            + (f"{echoed!r}" if echoed else "the repository root")
        ),
    )
    raise HostedHarnessError(
        "subfolder_ignored",
        "the harness did not build from the requested folder",
        status_code=422,
    )


def register_attempt(
    job_id: uuid.UUID | str,
    *,
    endpoint_base_url: str,
    provider_ref: str | None = None,
    snapshot_name: str | None = None,
    snapshot_digest: str | None = None,
) -> AttemptCapability:
    now = timezone.now()
    token = secrets.token_urlsafe(32)
    fence = secrets.token_urlsafe(32)
    with transaction.atomic():
        job = HostedHarnessJob.no_workspace_objects.select_for_update().get(id=job_id)
        # AUTHORITATIVE W>1 admission gate (C4 §4 pin ii / §5, decision D23).
        # register_attempt is the single chokepoint every attempt crosses — fresh
        # create, rerun_saved, harness_sandbox.rerun, and gateway retry all reach
        # here — so the shared guard runs here regardless of how the attempt was
        # requested. A denied W>1 is admitted at 1 and the clamp is recorded on
        # the job metadata; the job's stored REQUESTED value
        # (job.payload.runtime.parallelism) is preserved so a later rerun
        # re-evaluates honestly against the then-current flag/digest. A saved W=4
        # job therefore reruns at W=1 when the flag/digest no longer qualify. The
        # admitted value is returned on the capability so the gateway applies it
        # verbatim (single source of truth) instead of re-deriving the guard.
        admitted_parallelism = _apply_parallelism_admission(job, snapshot_digest)
        previous_number = job.current_attempt_number
        attempt_number = previous_number + 1
        if previous_number:
            HostedHarnessAttempt.no_workspace_objects.filter(
                job=job,
                attempt_number__lte=previous_number,
                state__in=(
                    HostedHarnessAttempt.State.REGISTERED,
                    HostedHarnessAttempt.State.PROVISIONING,
                    HostedHarnessAttempt.State.RUNNING,
                    HostedHarnessAttempt.State.FINALIZING,
                    HostedHarnessAttempt.State.CLEANING_UP,
                ),
            ).update(state=HostedHarnessAttempt.State.SUPERSEDED)
        runnable_deadline = now + timedelta(seconds=_active_attempt_budget_seconds(job))
        expires_at = runnable_deadline + timedelta(seconds=_TOKEN_TAIL_SECONDS)
        attempt = HostedHarnessAttempt.no_workspace_objects.create(
            job=job,
            attempt_number=attempt_number,
            token_hash=hash_secret(token),
            fence_hash=hash_secret(fence),
            expires_at=expires_at,
            provider_ref=provider_ref,
            snapshot_name=snapshot_name,
            snapshot_digest=snapshot_digest,
        )
        job.current_attempt_number = attempt_number
        if attempt_number == 1:
            _freeze_build_pin(job)
        job.state = HostedHarnessJob.State.PROVISIONING
        # A retry is a fresh active attempt. Do not keep presenting the previous
        # attempt's terminal state while Daytona is launching (or even running)
        # the replacement guest. Platform-side pre-guest progress (source
        # acquisition) is kept: the gateway records it just before this call.
        if job.current_stage not in {
            "queued",
            "acquiring_source",
            "understanding_agent",
        }:
            job.current_stage = "queued"
        job.failure = None
        job.terminal_at = None
        job.deadline_at = runnable_deadline
        job.save(
            update_fields=[
                "current_attempt_number",
                "payload",
                "state",
                "current_stage",
                "failure",
                "terminal_at",
                "deadline_at",
                "updated_at",
            ]
        )
        if job.test_execution_id:
            TestExecution.no_workspace_objects.filter(id=job.test_execution_id).update(
                status=TestExecution.ExecutionStatus.RUNNING,
                completed_at=None,
                error_reason=None,
            )

    base = endpoint_base_url.rstrip("/")
    prefix = f"{base}/simulate/api/harness/attempts/{attempt.id}"
    document = {
        "schema_version": _CAPABILITY_SCHEMA_VERSION,
        "job_id": str(job.id),
        "attempt_id": str(attempt.id),
        "attempt_number": attempt.attempt_number,
        "fence": fence,
        "expires_at": _rfc3339(attempt.expires_at),
        "token": token,
        "endpoints": {
            "events": f"{prefix}/events/",
            "results": f"{prefix}/results/",
            "artifacts": f"{prefix}/artifacts/",
            "scenarios": f"{prefix}/scenarios/",
            "ingress": f"{prefix}/ingress/",
        },
    }
    return AttemptCapability(
        attempt=attempt,
        token=token,
        fence=fence,
        document=document,
        admitted_parallelism=admitted_parallelism,
    )


def activate_attempt_capability(capability: AttemptCapability) -> AttemptCapability:
    """Start the guest's time budget immediately before its capability is uploaded.

    A managed sandbox can take a long time to create or accept source uploads. The
    token is not available to the guest during that work, so charging that time
    against the guest's deadline can expire an otherwise healthy run before its
    first call. This is only for the unissued, provisioning capability; it must
    never extend a running guest's access.
    """

    with transaction.atomic():
        job = HostedHarnessJob.no_workspace_objects.select_for_update().get(
            id=capability.attempt.job_id
        )
        attempt = HostedHarnessAttempt.no_workspace_objects.select_for_update().get(
            id=capability.attempt.id, job_id=job.id
        )
        if (
            attempt.state != HostedHarnessAttempt.State.PROVISIONING
            or not attempt.provider_ref
            or job.current_attempt_number != attempt.attempt_number
        ):
            raise HostedHarnessError(
                "attempt_capability_activation_invalid",
                "Only the current, provisioned attempt can be activated",
            )
        runnable_deadline = timezone.now() + timedelta(
            seconds=_active_attempt_budget_seconds(job)
        )
        attempt.expires_at = runnable_deadline + timedelta(seconds=_TOKEN_TAIL_SECONDS)
        attempt.save(update_fields=["expires_at", "updated_at"])
        job.deadline_at = runnable_deadline
        job.save(update_fields=["deadline_at", "updated_at"])

    return AttemptCapability(
        attempt=attempt,
        token=capability.token,
        fence=capability.fence,
        document={
            **capability.document,
            "expires_at": _rfc3339(attempt.expires_at),
        },
        admitted_parallelism=capability.admitted_parallelism,
    )


_TERMINAL_STATES = frozenset(
    {
        HostedHarnessJob.State.COMPLETED,
        HostedHarnessJob.State.FAILED,
        HostedHarnessJob.State.CANCELED,
    }
)


DELETE_CANCEL_REASON = "environment_deleted"


def delete_environment(job: HostedHarnessJob) -> None:
    """Hide an environment and request cleanup for every live hosted sandbox."""
    from simulate.temporal.client import cancel_hosted_harness_gateway_workflow

    jobs_to_cancel = []
    parent_cancel_id = None
    with transaction.atomic():
        locked = HostedHarnessJob.no_workspace_objects.select_for_update().get(
            id=job.id
        )
        from simulate.services.harness_versioning.nudges import (
            close_environment_nudges,
        )

        close_environment_nudges(locked, code="environment_deleted")
        active_children = list(
            HostedHarnessJob.no_workspace_objects.select_for_update()
            .filter(environment=locked)
            .exclude(state__in=_TERMINAL_STATES)
            .order_by("id")
        )
        for child in active_children:
            child = request_cancellation(child, DELETE_CANCEL_REASON)
            if child.state not in _TERMINAL_STATES:
                jobs_to_cancel.append(str(child.id))

        if locked.state in _TERMINAL_STATES:
            _soft_delete(locked)
        else:
            locked = request_cancellation(locked, DELETE_CANCEL_REASON)
            if locked.state in _TERMINAL_STATES:
                _soft_delete(locked)
            else:
                parent_cancel_id = str(locked.id)
                jobs_to_cancel.append(parent_cancel_id)

    for job_id in jobs_to_cancel:
        try:
            cancel_hosted_harness_gateway_workflow(job_id)
        except Exception:
            logger.exception("hosted_harness_delete_cancel_failed", job_id=job_id)
            try:
                from simulate.services.hosted_harness_gateway import (
                    HostedHarnessGateway,
                )

                cancel_job = HostedHarnessJob.no_workspace_objects.get(id=job_id)
                HostedHarnessGateway().cancel(cancel_job, reason=DELETE_CANCEL_REASON)
            except Exception:
                logger.exception(
                    "hosted_harness_delete_direct_cancel_failed", job_id=job_id
                )
                if job_id == parent_cancel_id:
                    _soft_delete(HostedHarnessJob.no_workspace_objects.get(id=job_id))


def _soft_delete(job: HostedHarnessJob) -> None:
    job.deleted = True
    job.deleted_at = timezone.now()
    job.save(update_fields=["deleted", "deleted_at", "updated_at"])


def finish_deferred_delete(job: HostedHarnessJob) -> list[str]:
    """Hide a job its owner deleted while it ran, now that the run is over.

    Returns the fields set, for the caller's ``update_fields``.
    """
    if job.cancel_reason != DELETE_CANCEL_REASON or job.deleted:
        return []
    job.deleted = True
    job.deleted_at = timezone.now()
    return ["deleted", "deleted_at"]


def request_cancellation(job: HostedHarnessJob, reason: str) -> HostedHarnessJob:
    with transaction.atomic():
        locked = HostedHarnessJob.no_workspace_objects.select_for_update().get(
            id=job.id
        )
        if locked.state in {
            HostedHarnessJob.State.COMPLETED,
            HostedHarnessJob.State.FAILED,
            HostedHarnessJob.State.CANCELED,
        }:
            return locked
        locked.cancel_requested_at = timezone.now()
        locked.cancel_reason = reason
        locked.state = HostedHarnessJob.State.CLEANING_UP
        # State and stage are both part of the public read model. Leaving the
        # previous authoring stage here makes a successful cancel request look
        # like it did nothing until remote sandbox cleanup finishes.
        locked.current_stage = HostedHarnessJob.State.CLEANING_UP
        locked.save(
            update_fields=[
                "cancel_requested_at",
                "cancel_reason",
                "state",
                "current_stage",
                "updated_at",
            ]
        )
        return locked


def _is_bare_uuid(value: str) -> bool:
    """Return True when *value* looks like a bare UUID (no surrounding text)."""
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError):
        return False


def _derive_name_from_job(job: HostedHarnessJob) -> str:
    """Derive a human-readable run name from the job when the guest sends a UUID."""
    source = (job.payload or {}).get("source", {})
    repo = source.get("repository", "") if isinstance(source, dict) else ""
    if repo and "/" in repo:
        return repo.rsplit("/", 1)[-1]
    if repo:
        return repo
    return f"simulation-{str(job.id)[:8]}"


def _derive_agent_name_from_job(job: HostedHarnessJob) -> str:
    """Derive an agent name from the job's contract stage output."""
    for output in job.stage_outputs or []:
        if not isinstance(output, dict) or output.get("kind") != "contract":
            continue
        data = output.get("data")
        if not isinstance(data, dict):
            continue
        agent = str(data.get("agent") or "").strip()
        if agent:
            return agent.replace("_", " ").replace("-", " ").title()[:200]
        one_liner = str(data.get("one_liner") or "").strip()
        if one_liner:
            # Use the first few words of the one-liner
            return one_liner[:100]
    return _derive_name_from_job(job)


def provision_scenarios(
    attempt: HostedHarnessAttempt, payload: dict[str, Any]
) -> dict[str, Any]:
    job = attempt.job
    # Before any transaction: the failure it records must survive the raise.
    _check_subfolder_echo(attempt, payload)
    # Enforce exactly scenario_count unique personas.
    persona_keys = [p["scenario_key"] for p in payload["personas"]]
    if len(persona_keys) != len(set(persona_keys)):
        raise HostedHarnessError(
            "scenario_key_duplicate",
            "persona scenario_key values must be unique",
            status_code=400,
        )
    if (
        len(persona_keys) != job.scenario_count
        and job.environment_id is None
        and job.test_execution_id is None
    ):
        # A build holds what its harness built; the count it was asked for was the starting brief.
        job.scenario_count = len(persona_keys)
        job.payload = {**(job.payload or {}), "scenario_count": len(persona_keys)}
        job.save(update_fields=["scenario_count", "payload", "updated_at"])
    if len(persona_keys) != job.scenario_count:
        raise HostedHarnessError(
            "scenario_count_mismatch",
            f"expected exactly {job.scenario_count} personas, got {len(persona_keys)}",
            status_code=400,
        )
    if job.run_test_id:
        registrations = list(
            HostedHarnessScenario.no_workspace_objects.filter(job=job).order_by(
                "created_at"
            )
        )
        existing_keys = [item.scenario_key for item in registrations]
        existing_set = set(existing_keys)
        requested_keys = [persona["scenario_key"] for persona in payload["personas"]]
        requested_set = set(requested_keys)
        if requested_set == existing_set:
            return _provision_response(job, registrations)
        # A chat "add scenarios" follow-up seals the existing set PLUS new personas. Dropping
        # an existing scenario is a real conflict, not an extension.
        if not existing_set.issubset(requested_set):
            raise HostedHarnessError(
                "scenario_registration_conflict",
                "the job already has a different sealed scenario registration",
                status_code=409,
            )
        new_personas = [
            persona
            for persona in payload["personas"]
            if persona["scenario_key"] not in existing_set
        ]
        shared_scenario, appended = append_alk_sim_scenarios(
            job.run_test,
            [
                {key: value for key, value in persona.items() if key != "scenario_key"}
                for persona in new_personas
            ],
        )
        if shared_scenario is not None:
            bindings = [(shared_scenario, row) for row in appended]
        else:
            # Compatibility for a job provisioned with the former one-row,
            # one-scenario layout before this deployment.
            bindings = [(scenario, None) for scenario in appended]
        with transaction.atomic():
            for persona, (scenario, row) in zip(new_personas, bindings, strict=True):
                registrations.append(
                    # A key an amend dropped comes back as its own row, not a duplicate.
                    HostedHarnessScenario.all_objects.update_or_create(
                        job=job,
                        scenario_key=persona["scenario_key"],
                        defaults={
                            "scenario": scenario,
                            "dataset_row": row,
                            "deleted": False,
                            "deleted_at": None,
                        },
                    )[0]
                )
        return _provision_response(job, registrations)

    personas = [
        {key: value for key, value in persona.items() if key != "scenario_key"}
        for persona in payload["personas"]
    ]
    try:
        # Older hosted guests did not include ``modality`` in the scenario
        # provision request.  The authoring contract is already persisted on
        # the job before scenario registration, so use it as the authoritative
        # fallback instead of silently defaulting every hosted run to text.
        # Misclassifying a LiveKit run here makes completed voice calls render
        # through the chat schema and effectively disappear from call-details.
        modality = _resolve_scenario_modality(job, payload)
        from simulate.services.harness_agent_versions import agent_definition_owner

        bound_id = payload.get("agent_definition_id")
        if agent_definition_owner(job) == "environment":
            # Passed on, never put in the guest's request: it stays unbound there.
            guest_id = bound_id
            bound_id, modality = _environment_definition_for(job, modality)
            if guest_id and str(guest_id) != str(bound_id):
                logger.info(
                    "harness_agent_not_bound",
                    job_id=str(job.id),
                    reason="guest_id_ignored",
                )
        # TODO: refuse a guest's id for a connected agent too (connected branch).
        # Defense: when the guest sends a bare UUID as the run name
        # (older guests use job.run_id), derive something readable.
        run_name = payload["name"]
        if _is_bare_uuid(run_name):
            run_name = _derive_name_from_job(job)
        incoming_agent_name = payload.get("agent_name")
        if not incoming_agent_name or incoming_agent_name == "alk-sdk-agent":
            incoming_agent_name = _derive_agent_name_from_job(job)
        run_test, scenarios, agent_definition = provision_alk_sim_run_test(
            job.organization,
            workspace=job.workspace,
            name=run_name,
            personas=personas,
            agent_definition_id=bound_id,
            agent_name=incoming_agent_name,
            description=payload.get("description", ""),
            modality=modality,
        )
    except ALKSimulateIngestionError as exc:
        raise HostedHarnessError("scenario_provision_failed", str(exc)) from exc
    if len(scenarios) != 1 or not scenarios[0].dataset_id:
        raise HostedHarnessError(
            "scenario_provision_count_mismatch",
            "scenario provisioning did not create one dataset-backed suite",
            status_code=500,
            retryable=True,
        )
    dataset_rows = list(
        scenarios[0].dataset.row_set.filter(deleted=False).order_by("order", "id")
    )
    if len(dataset_rows) != len(payload["personas"]):
        raise HostedHarnessError(
            "scenario_provision_count_mismatch",
            "scenario dataset row count did not preserve the requested cardinality",
            status_code=500,
            retryable=True,
        )
    with transaction.atomic():
        locked = HostedHarnessJob.no_workspace_objects.select_for_update().get(
            id=job.id
        )
        existing_registrations = list(
            HostedHarnessScenario.no_workspace_objects.select_for_update()
            .filter(job=locked)
            .order_by("created_at")
        )
        requested_keys = [persona["scenario_key"] for persona in payload["personas"]]
        existing_by_key = {
            registration.scenario_key: registration
            for registration in existing_registrations
        }
        if locked.run_test_id:
            if set(existing_by_key) == set(requested_keys):
                # A concurrent request already provisioned this suite; return it.
                return _provision_response(locked, existing_registrations)
            raise HostedHarnessError(
                "scenario_registration_conflict",
                "another attempt registered scenarios first",
                status_code=409,
            )
        locked.run_test = run_test
        locked.content_updated_at = timezone.now()
        locked.save(update_fields=["run_test", "content_updated_at", "updated_at"])
        if existing_registrations:
            # Rows indexed for scenarios the final suite dropped are hidden, not fatal.
            requested = set(requested_keys)
            stale = [
                registration
                for key, registration in existing_by_key.items()
                if key not in requested
            ]
            if any(row.scenario_id or row.call_execution_id for row in stale):
                raise HostedHarnessError(
                    "scenario_registration_conflict",
                    "the indexed authored suite differs from the provision request",
                    status_code=409,
                )
            bound_at = timezone.now()
            for row in stale:
                row.deleted, row.deleted_at, row.updated_at = True, bound_at, bound_at
            HostedHarnessScenario.all_objects.bulk_update(
                stale, ["deleted", "deleted_at", "updated_at"]
            )
            hidden = {
                row.scenario_key: row
                for row in HostedHarnessScenario.all_objects.filter(
                    job=locked,
                    deleted=True,
                    scenario_key__in=requested - set(existing_by_key),
                )
            }
            registrations = []
            for persona, row in zip(payload["personas"], dataset_rows, strict=True):
                key = persona["scenario_key"]
                registration = existing_by_key.get(key) or hidden.get(key)
                if registration is None:
                    registration = HostedHarnessScenario.no_workspace_objects.create(
                        job=locked, scenario_key=key
                    )
                registration.deleted, registration.deleted_at = False, None
                registration.scenario = scenarios[0]
                registration.dataset_row = row
                registration.updated_at = bound_at
                registrations.append(registration)
            HostedHarnessScenario.all_objects.bulk_update(
                registrations,
                ["deleted", "deleted_at", "scenario", "dataset_row", "updated_at"],
            )
        else:
            registrations = [
                HostedHarnessScenario.no_workspace_objects.create(
                    job=locked,
                    scenario_key=persona["scenario_key"],
                    scenario=scenarios[0],
                    dataset_row=row,
                )
                for persona, row in zip(payload["personas"], dataset_rows, strict=True)
            ]
        _record_target_agent_facts(locked, agent_definition, payload)
        stored = _store_authored_agent(locked, agent_definition, payload)
        _turn_on_observability(locked, agent_definition, payload, stored)
        from simulate.services.harness_agent_versions import fill_agent_pin

        if fill_agent_pin(locked):
            locked.save(update_fields=["agent_version", "updated_at"])
        _select_platform_evals(locked, run_test, payload, modality)
    return _provision_response(locked, registrations)


def _environment_definition_for(
    job: HostedHarnessJob, modality: str
) -> tuple[str | None, str]:
    """The agent made with the environment, and the modality its connector runs: an
    ``auto`` agent takes the authored modality, a known connector keeps its own."""
    from simulate.models import AgentDefinition, AgentVersion

    definition_id = (
        AgentVersion._base_manager.filter(id=job.agent_version_id)
        .values_list("agent_definition_id", flat=True)
        .first()
        if job.agent_version_id
        else None
    )
    definition = (
        AgentDefinition.no_workspace_objects.filter(
            id=definition_id,
            organization_id=job.organization_id,
            workspace_id=job.workspace_id,
            deleted=False,
        ).first()
        if definition_id
        else None
    )
    if definition is None:
        logger.info(
            "harness_agent_not_bound",
            job_id=str(job.id),
            reason="environment_definition_missing",
        )
        return None, modality
    from simulate.serializers.harness_job import PROVIDER_TARGET_CONNECTORS
    from simulate.services.harness_agent_versions import _connector_agent_type

    connector = str((job.payload.get("agent") or {}).get("connector") or "")
    Types = AgentDefinition.AgentTypeChoices
    if connector not in PROVIDER_TARGET_CONNECTORS:
        wanted = Types.VOICE if modality == "voice" else Types.TEXT
        if definition.agent_type != wanted:
            AgentDefinition.no_workspace_objects.filter(id=definition.id).update(
                agent_type=wanted, updated_at=timezone.now()
            )
            logger.info(
                "harness_agent_type_aligned",
                job_id=str(job.id),
                definition_id=str(definition.id),
                from_type=definition.agent_type,
                to_type=wanted,
            )
        return str(definition.id), modality
    runs = "text" if _connector_agent_type(connector) == Types.TEXT else "voice"
    if modality != runs:
        logger.info(
            "harness_modality_overridden",
            job_id=str(job.id),
            from_modality=modality,
            to_modality=runs,
        )
    return str(definition.id), runs


def _target_agent_prompt(job: HostedHarnessJob, payload: dict[str, Any]) -> str:
    """Resolve target instructions without reducing an Others prompt."""
    connector = str((job.payload.get("agent") or {}).get("connector") or "")
    if connector in {"phone", "livekit"}:
        # "Others" is configured from a user-supplied system prompt.  Keep that
        # complete prompt as the agent definition's source of truth: the hosted
        # guest's ``agent_prompt`` may only be the short contract excerpt used
        # during authoring and must not replace it.
        configured = str(
            ((job.payload.get("agent") or {}).get("config") or {}).get(
                "target_system_prompt"
            )
            or ""
        ).strip()
        if configured:
            return configured
    supplied = str(payload.get("agent_prompt") or "").strip()
    if supplied:
        return supplied
    return str(_authored_contract_data(job).get("system_prompt_excerpt") or "").strip()


def _authored_contract_data(job: HostedHarnessJob) -> dict[str, Any]:
    """The authored contract body, or empty before the contract stage has landed."""
    for output in job.stage_outputs or []:
        if not isinstance(output, dict) or output.get("kind") != "contract":
            continue
        data = output.get("data")
        if isinstance(data, dict):
            return data
    return {}


def _record_target_agent_facts(
    job: HostedHarnessJob, agent_definition, payload: dict[str, Any]
) -> None:
    """Record the target agent's prompt and identity where the platform already looks.

    `agent_prompt` in an eval mapping resolves from the agent version's configuration snapshot,
    and that snapshot takes its ``description`` from the agent definition, so the definition's
    description is the platform's own home for an agent's instructions. Without a version row
    there is nothing for the mapping to read and nothing for the UI to show.

    An agent made with the environment gets the columns only, never a new version;
    one shared by environments only has its blanks filled.
    """
    from simulate.services.harness_agent_versions import (
        _shared_key,
        agent_definition_owner,
        resnapshot_version,
    )

    owner = agent_definition_owner(job)
    if owner == "connected":
        # TODO: write nothing to a connected agent (connected branch, once P26/P26a
        # merge).
        return
    if owner == "environment":
        _copy_target_facts(
            job,
            agent_definition,
            payload,
            fill_only=_shared_key(job.payload) is not None,
        )
        if job.agent_version_id:
            resnapshot_version(job.agent_version_id, agent_definition.id)
        return
    prompt, changed = _copy_target_facts(job, agent_definition, payload)
    # Any changed fact gets a version, so the newest one's snapshot matches.
    if prompt and (agent_definition.latest_version is None or changed):
        from simulate.models import AgentDefinition
        from simulate.services.harness_agent_versions import next_version_number

        AgentDefinition.no_workspace_objects.select_for_update().filter(
            id=agent_definition.id
        ).first()
        agent_definition.create_version(
            description=prompt,
            commit_message="hosted harness target agent prompt",
            status="active",
            version_number=next_version_number(agent_definition.id),
        )


def _copy_target_facts(
    job: HostedHarnessJob,
    agent_definition,
    payload: dict[str, Any],
    *,
    fill_only: bool = False,
) -> tuple[str, list[str]]:
    """Copy the target's prompt and identity onto its agent; with ``fill_only`` only
    into columns still blank. Returns the prompt and the columns changed."""
    changed: list[str] = []
    prompt = _target_agent_prompt(job, payload)
    if (
        prompt
        and agent_definition.description != prompt
        and not (fill_only and agent_definition.description)
    ):
        agent_definition.description = prompt
        changed.append("description")
    # model and language are left alone: the contract carries neither.
    from simulate.serializers.harness_job import PROVIDER_TARGET_CONNECTORS
    from simulate.services.harness_agent_versions import (
        connector_provider,
        target_identity,
    )

    connector = str((job.payload.get("agent") or {}).get("connector") or "").lower()
    if connector in PROVIDER_TARGET_CONNECTORS and not agent_definition.provider:
        agent_definition.provider = connector_provider(connector)
        changed.append("provider")
    authored = _authored_contract_data(job)
    named = str(authored.get("agent") or "").strip()
    if named and agent_definition.agent_name == "alk-sdk-agent":
        agent_definition.agent_name = named[:255]
        changed.append("agent_name")
    # Provider-backed web voice targets are identified by different payload
    # keys, but AgentDefinition intentionally stores both in assistant_id.
    # Without copying this identity into the definition/version, authoring can
    # succeed while the later simulation-runner build has no Retell agent_id
    # or Vapi assistant_id and fails every selected call before transport.
    # The user's RL Environment selection is authoritative for inbound: the
    # explicit boolean first, then the submitted direction, then the authored guess.
    assistant_id, contact_number, inbound, target_speaks_first = target_identity(
        job.payload.get("agent") or {}, authored.get("call_direction")
    )
    if (
        assistant_id
        and agent_definition.assistant_id != assistant_id
        and not (fill_only and agent_definition.assistant_id)
    ):
        agent_definition.assistant_id = assistant_id
        changed.append("assistant_id")
    if (
        contact_number
        and agent_definition.contact_number != contact_number
        and not (fill_only and agent_definition.contact_number)
    ):
        # Phone targets arrive through the hosted job payload rather than the
        # native agent-definition form. Persist the validated destination on
        # the definition before creating its version so the hosted runner sees
        # a SIP target instead of treating it as a LiveKit-native agent.
        agent_definition.contact_number = contact_number
        changed.append("contact_number")
    if not fill_only and inbound is not None and agent_definition.inbound != inbound:
        agent_definition.inbound = inbound
        changed.append("inbound")
    if (
        target_speaks_first is not None
        and agent_definition.target_speaks_first != target_speaks_first
        and not (fill_only and agent_definition.target_speaks_first is not None)
    ):
        agent_definition.target_speaks_first = target_speaks_first
        changed.append("target_speaks_first")
    if changed:
        agent_definition.save(update_fields=[*changed, "updated_at"])
    return prompt, changed


def _store_authored_agent(
    job: HostedHarnessJob, agent_definition, payload: dict[str, Any]
) -> tuple[str | None, uuid.UUID | None]:
    sent = payload.get("agent")
    source_kind = str((job.payload.get("source") or {}).get("kind") or "")
    if (
        sent is None
        or payload.get("agent_definition_id")
        or source_kind not in ("github", "archive")
    ):
        return None, None
    agent_version_id = None
    try:
        with transaction.atomic():
            from simulate.services.harness_agent_versions import agent_pin_candidate

            # The build's frozen pin; an unpinned environment made before pins stores
            # on the version it is about to be pinned to.
            pinned = (
                frozen_build_pin(job) or job.agent_version or agent_pin_candidate(job)
            )
            version = (
                pinned
                if pinned is not None
                and pinned.agent_definition_id == agent_definition.id
                else agent_definition.latest_version
            )
            if version is None:
                version = agent_definition.create_version(
                    description="",
                    commit_message="hosted harness authored agent",
                    status="active",
                )
                AgentVersion.no_workspace_objects.filter(id=version.id).update(
                    configuration_snapshot={
                        **version.configuration_snapshot,
                        "description": "",
                    }
                )
            agent_version_id = version.id
            if not (
                isinstance(sent, dict)
                and isinstance(sent.get("agent"), dict)
                and isinstance(sent.get("sdk_version"), str)
            ):
                raise AgentStoreError("agent_invalid")
            outcome = store_agent(
                agent_version_id=version.id,
                agent=sent["agent"],
                sdk_version=sent["sdk_version"],
            )
        return outcome, agent_version_id
    except Exception as exc:
        logger.warning(
            "harness_agent_not_stored",
            job_id=str(job.id),
            agent_version_id=str(agent_version_id) if agent_version_id else None,
            code=exc.code if isinstance(exc, AgentStoreError) else type(exc).__name__,
        )
        return None, None


def _turn_on_observability(
    job: HostedHarnessJob,
    agent_definition,
    payload: dict[str, Any],
    stored: tuple[str | None, uuid.UUID | None],
) -> None:
    """Turn observability on for the agent just stored, when the Environment asked for it.

    A refusal never fails the provision: the reason is logged and kept on the job.
    """
    if (job.payload.get("metadata") or {}).get("observability_enabled") is not True:
        return
    details: dict[str, Any] = {}
    failure: Exception | None = None
    if payload.get("agent_definition_id"):
        code = "bound"
    elif stored[0] != "stored":
        code = "agent_not_stored"
    else:
        # The try sits outside the savepoint so its rollback runs before the handler.
        try:
            with transaction.atomic():
                row = set_agent_observability(
                    agent_definition=agent_definition,
                    enabled=True,
                    user_id=None,
                    organization=job.organization,
                    workspace=job.workspace,
                )
                if row is not None:
                    versions = AgentVersion.no_workspace_objects.filter(id=stored[1])
                    snapshot = dict(
                        versions.values_list("configuration_snapshot", flat=True).get()
                        or {}
                    )
                    snapshot["observability_enabled"] = True
                    versions.update(configuration_snapshot=snapshot)
            return
        except ObservabilityRefused as exc:
            code = ",".join(entry.code for entry in exc.missing)
            details = refusal_body(exc)["details"]
        except Exception as exc:  # noqa: BLE001 - the provision must go on
            code = type(exc).__name__
            failure = exc
    logger.warning(
        "harness_observability_not_enabled",
        job_id=str(job.id),
        agent_definition_id=str(agent_definition.id),
        code=code,
        **({"exc_info": failure} if failure is not None else {}),
    )
    _save_observability_refusal(job, code, details)


def _save_observability_refusal(
    job: HostedHarnessJob, code: str, details: dict[str, Any]
) -> None:
    try:
        with transaction.atomic():
            payload = dict(job.payload)
            metadata = dict(payload.get("metadata") or {})
            metadata["observability_refusal"] = {"code": code, "details": details}
            payload["metadata"] = metadata
            job.payload = payload
            job.save(update_fields=["payload", "updated_at"])
    except Exception as exc:  # noqa: BLE001 - the provision must go on
        logger.error(
            "harness_observability_refusal_not_saved",
            job_id=str(job.id),
            code=type(exc).__name__,
            exc_info=True,
        )


def _select_platform_evals(
    job: HostedHarnessJob,
    run_test,
    payload: dict[str, Any],
    modality: str,
) -> None:
    """Bind the guest's chosen platform evals to this run, or refuse an unknown name."""
    from simulate.services.harness_evals import (
        UnknownEvalSelection,
        create_selected_eval_configs,
    )

    chosen = payload.get("chosen_evals") or []
    if not chosen:
        return
    try:
        create_selected_eval_configs(run_test, list(chosen), modality)
    except UnknownEvalSelection as unknown:
        raise HostedHarnessError(
            "eval_selection_unknown",
            f"eval template(s) not available to this organization: {unknown}",
            status_code=400,
        ) from unknown


def _resolve_scenario_modality(job: HostedHarnessJob, payload: dict[str, Any]) -> str:
    """Resolve text/voice without requiring a lock-step guest rollout."""
    explicit = str(payload.get("modality") or "").lower()
    if explicit in {"text", "voice"}:
        return explicit
    for output in job.stage_outputs or []:
        if not isinstance(output, dict) or output.get("kind") != "contract":
            continue
        data = output.get("data")
        if not isinstance(data, dict):
            continue
        authored = str(data.get("modality") or "").lower()
        if authored in {"text", "voice"}:
            return authored
    return "text"


def begin_scenarios(
    attempt: HostedHarnessAttempt, payload: dict[str, Any]
) -> dict[str, Any]:
    job = attempt.job
    if not job.run_test_id or str(job.run_test_id) != str(payload["run_test_id"]):
        raise HostedHarnessError(
            "run_test_mismatch", "run test is not registered", status_code=403
        )
    registrations = list(
        HostedHarnessScenario.no_workspace_objects.filter(job=job).select_related(
            "scenario", "dataset_row", "call_execution"
        )
    )
    by_key = {item.scenario_key: item for item in registrations}
    if set(payload["scenario_keys"]) != set(by_key):
        raise HostedHarnessError(
            "scenario_key_mismatch",
            "begin must name the complete sealed scenario set",
            status_code=409,
        )
    if job.test_execution_id:
        unlinked = [item for item in registrations if item.call_execution_id is None]
        if not unlinked:
            return _begin_response(job, registrations)
        # A chat "add scenarios" follow-up appends the new personas' calls to the existing
        # execution: extend its scenario set, create only the missing calls (the batch is
        # idempotent + additive), and link the new registrations. Existing calls stay.
        with transaction.atomic():
            execution = TestExecution.no_workspace_objects.select_for_update().get(
                id=job.test_execution_id
            )
            existing_ids = list(
                dict.fromkeys(str(sid) for sid in (execution.scenario_ids or []))
            )
            existing_id_set = set(existing_ids)
            added_ids = [
                str(item.scenario_id)
                for item in unlinked
                if str(item.scenario_id) not in existing_id_set
            ]
            if added_ids:
                execution.scenario_ids = existing_ids + added_ids
                execution.total_scenarios = len(execution.scenario_ids)
                execution.save(update_fields=["scenario_ids", "total_scenarios"])
        try:
            batch = create_alk_sim_call_execution_batch(execution, count=len(unlinked))
        except ALKSimulateIngestionError as exc:
            raise HostedHarnessError("scenario_begin_failed", str(exc)) from exc
        added_calls = CallExecution.no_workspace_objects.filter(
            id__in=batch.call_execution_ids
        ).select_related("scenario")
        calls_by_key = {(call.scenario_id, call.row_id): call for call in added_calls}
        with transaction.atomic():
            for item in unlinked:
                call = _call_for_registration(item, calls_by_key)
                if call is None:
                    raise HostedHarnessError(
                        "scenario_call_mapping_incomplete",
                        "extension pre-allocation did not create a call for a new scenario",
                        status_code=500,
                        retryable=True,
                    )
                item.call_execution = call
                item.save(update_fields=["call_execution", "updated_at"])
        return _begin_response(job, registrations)

    from simulate.services.harness_agent_versions import agent_definition_owner

    test_execution = create_alk_sim_test_execution(
        job.run_test,
        scenario_ids=list(dict.fromkeys(item.scenario_id for item in registrations)),
        # A shared agent's latest version may be another environment's.
        agent_version=(
            job.agent_version
            if agent_definition_owner(job) == "environment"
            else job.agent_version or _unpinned_agent_version(job)
        ),
    )
    try:
        batch = create_alk_sim_call_execution_batch(
            test_execution, count=len(registrations)
        )
    except ALKSimulateIngestionError as exc:
        raise HostedHarnessError("scenario_begin_failed", str(exc)) from exc
    calls = CallExecution.no_workspace_objects.filter(
        id__in=batch.call_execution_ids
    ).select_related("scenario")
    calls_by_key = {(call.scenario_id, call.row_id): call for call in calls}
    mapped_calls = [
        _call_for_registration(item, calls_by_key) for item in registrations
    ]
    if any(call is None for call in mapped_calls) or len(set(mapped_calls)) != len(
        registrations
    ):
        raise HostedHarnessError(
            "scenario_call_mapping_incomplete",
            "execution pre-allocation did not create exactly one call per dataset row",
            status_code=500,
            retryable=True,
        )
    with transaction.atomic():
        locked = HostedHarnessJob.no_workspace_objects.select_for_update().get(
            id=job.id
        )
        if locked.test_execution_id and locked.test_execution_id != test_execution.id:
            raise HostedHarnessError(
                "scenario_begin_conflict",
                "another attempt began the execution first",
                status_code=409,
            )
        locked.test_execution = test_execution
        locked.state = HostedHarnessJob.State.RUNNING
        locked.content_updated_at = timezone.now()
        locked.save(
            update_fields=[
                "test_execution",
                "state",
                "content_updated_at",
                "updated_at",
            ]
        )
        for registration, call in zip(registrations, mapped_calls, strict=True):
            registration.call_execution = call
            registration.save(update_fields=["call_execution", "updated_at"])
    return _begin_response(locked, registrations)


def record_cleanup(
    attempt_id: uuid.UUID | str,
    *,
    provider_ref: str,
    verified_absent: bool,
    retry_pending: bool = False,
    details: dict[str, Any] | None = None,
) -> HostedHarnessJob:
    if not verified_absent:
        raise HostedHarnessError(
            "sandbox_cleanup_unverified",
            "provider did not verify that the sandbox is absent",
            status_code=409,
            retryable=True,
        )
    with transaction.atomic():
        attempt = (
            HostedHarnessAttempt.no_workspace_objects.select_for_update()
            .select_related("job")
            .get(id=attempt_id)
        )
        if attempt.provider_ref and attempt.provider_ref != provider_ref:
            raise HostedHarnessError(
                "provider_ref_mismatch",
                "cleanup provider reference does not match the registered lease",
                status_code=409,
            )
        HostedHarnessCleanupReceipt.no_workspace_objects.get_or_create(
            attempt=attempt,
            defaults={
                "provider_ref": provider_ref,
                "verified_absent": True,
                "details": details or {},
            },
        )
        now = timezone.now()
        attempt.cleanup_verified_at = now
        if attempt.state != HostedHarnessAttempt.State.SUPERSEDED:
            attempt.state = _attempt_terminal_state(attempt)
        attempt.save(update_fields=["cleanup_verified_at", "state", "updated_at"])
        from simulate.services.harness_usage import record_sandbox_runtime
        from simulate.tasks.hosted_harness_usage import schedule_usage_seal

        record_sandbox_runtime(attempt, final=True)
        # Sealed after commit on the backend queue: the sandbox runner has no billing config.
        sealed_attempt_id = str(attempt.id)
        transaction.on_commit(lambda: schedule_usage_seal(sealed_attempt_id))
        job = HostedHarnessJob.no_workspace_objects.select_for_update().get(
            id=attempt.job_id
        )
        if attempt.attempt_number < job.current_attempt_number:
            return job
        if retry_pending:
            job.state = HostedHarnessJob.State.RETRY_WAIT
            job.save(update_fields=["state", "updated_at"])
            return job
        snapshot_missing = (
            job.environment_id is None
            and job.test_execution_id is None
            and attempt.terminal_stage == "completed"
            and (
                not job.run_test_id
                or not (job.payload.get("metadata") or {}).get("authoring_object_key")
                or HostedHarnessScenario.no_workspace_objects.filter(job=job).count()
                != job.scenario_count
            )
        )
        if snapshot_missing:
            job.state = HostedHarnessJob.State.FAILED
        elif attempt.terminal_stage == "completed":
            job.state = HostedHarnessJob.State.COMPLETED
        elif attempt.terminal_stage == "canceled":
            job.state = HostedHarnessJob.State.CANCELED
        else:
            job.state = HostedHarnessJob.State.FAILED
        # Cleanup is an intermediate lifecycle stage. Once absence has been verified, expose the
        # guest's terminal stage so a completed job cannot remain visually stuck on cleaning_up.
        job.current_stage = (
            HostedHarnessJob.State.FAILED
            if snapshot_missing
            else attempt.terminal_stage or job.state
        )
        job.terminal_at = now
        job.failure = (
            {
                "domain": "platform_sync",
                "stage": "validating_scenarios",
                "code": "authoring_snapshot_missing",
                "message": "Validated scenarios or their durable bundle are missing",
            }
            if snapshot_missing
            else attempt.terminal_failure
        )
        job.content_updated_at = now
        job.save(
            update_fields=[
                "state",
                "terminal_at",
                "current_stage",
                "failure",
                "content_updated_at",
                "updated_at",
                *finish_deferred_delete(job),
            ]
        )
        if (
            job.environment_id is None
            and job.test_execution_id is None
            and job.state == HostedHarnessJob.State.COMPLETED
        ):
            from simulate.services.harness_versioning.nudges import schedule_reconcile
            from simulate.services.harness_versioning.rebuild import (
                schedule_deferred_activations,
            )

            schedule_reconcile(job.id)
            schedule_deferred_activations(job.id)
        if job.state == HostedHarnessJob.State.COMPLETED:
            built_job_id, built_attempt_id = str(job.id), str(attempt.id)
            transaction.on_commit(
                lambda: _ask_builder_to_check(built_job_id, built_attempt_id)
            )
        project_terminal_job_onto_run(job, now)
        return job


def _ask_builder_to_check(job_id: str, attempt_id: str) -> None:
    """Tell the builder session that built this environment to check it against the agent."""
    from simulate.models import HostedHarnessConversationMessage
    from simulate.services.hosted_harness_conversation import send_to_harness
    from simulate.services.hosted_harness_ingress import _public_base_url

    try:
        if not HostedHarnessConversationMessage.no_workspace_objects.filter(
            conversation__job_id=job_id, client_request_id=f"build-{attempt_id}"
        ).exists():
            return
        send_to_harness(
            HostedHarnessJob.no_workspace_objects.get(id=job_id),
            content="The environment is validated and sealed. Check it against the agent.",
            client_request_id=f"check-{attempt_id}",
            endpoint_base_url=_public_base_url(None),
        )
    except Exception:
        logger.exception("builder_check_request_failed", job_id=job_id)


def project_terminal_job_onto_run(job: HostedHarnessJob, now: datetime) -> None:
    """Move a terminal job's Run, and the calls it left open, to their end states."""
    if not job.test_execution_id:
        return
    message = (job.failure or {}).get("message")
    calls = CallExecution.no_workspace_objects.filter(
        test_execution_id=job.test_execution_id
    )
    calls.filter(
        status__in=(
            CallExecution.CallStatus.PENDING,
            CallExecution.CallStatus.REGISTERED,
            CallExecution.CallStatus.ONGOING,
        )
    ).update(
        status=(
            CallExecution.CallStatus.CANCELLED
            if job.state == HostedHarnessJob.State.CANCELED
            else CallExecution.CallStatus.FAILED
        ),
        completed_at=now,
        **({"ended_reason": message} if message else {}),
    )
    execution_status = {
        HostedHarnessJob.State.COMPLETED: TestExecution.ExecutionStatus.COMPLETED,
        HostedHarnessJob.State.CANCELED: TestExecution.ExecutionStatus.CANCELLED,
        HostedHarnessJob.State.FAILED: TestExecution.ExecutionStatus.FAILED,
    }[job.state]
    has_completed_calls = calls.filter(
        status=CallExecution.CallStatus.COMPLETED
    ).exists()
    # An in-run re-run stops only its chosen calls; the Run's other calls already
    # finished, so the Run settles from its calls whatever ended the re-run.
    rerun = bool((job.payload.get("metadata") or {}).get(RERUN_EXECUTION_KEYS))
    if execution_status == TestExecution.ExecutionStatus.COMPLETED or (
        rerun and has_completed_calls
    ):
        # Transport finished, scoring may not have: only a run still in
        # transport is moved, and only the settle decides it is done.
        in_transport = TestExecution.no_workspace_objects.filter(
            id=job.test_execution_id, status__in=TRANSPORT_RUN_STATUSES
        )
        if has_completed_calls:
            in_transport.update(
                status=TestExecution.ExecutionStatus.EVALUATING, error_reason=None
            )
            settle_run_after_commit(job.test_execution_id)
        else:
            in_transport.update(
                status=execution_status, completed_at=now, error_reason=None
            )
        return
    TestExecution.no_workspace_objects.filter(id=job.test_execution_id).update(
        status=execution_status,
        completed_at=now,
        error_reason=message if job.state == HostedHarnessJob.State.FAILED else None,
    )


def update_execution_counts(job: HostedHarnessJob) -> None:
    # Receipt deliveries for parallel scenarios arrive independently. Lock the job projection
    # while taking the snapshot so a slower request cannot overwrite counters with an older view
    # after a faster request has already accounted a later receipt.
    with transaction.atomic():
        locked_job = HostedHarnessJob.no_workspace_objects.select_for_update().get(
            id=job.id
        )
        if not locked_job.test_execution_id:
            return
        current_attempt_number = locked_job.current_attempt_number
        # Reruns retain old receipts until their replacements arrive. Counters must describe
        # only the current attempt while its parallel receipts are delivered independently.
        receipts = HostedHarnessReceipt.no_workspace_objects.filter(job=locked_job)
        rerun_keys = (locked_job.payload.get("metadata") or {}).get(
            RERUN_EXECUTION_KEYS
        )
        if rerun_keys:
            # An in-run re-run replaces only its chosen calls: every other call's
            # receipt is still that call's result in the Run.
            receipts = receipts.exclude(
                execution__execution_key__in=rerun_keys,
                attempt_number__lt=current_attempt_number,
            )
        else:
            receipts = receipts.filter(attempt_number=current_attempt_number)
        # Harness receipt outcomes answer "did the scenario satisfy its checks?"; TestExecution
        # counters answer "did the call transport complete?". Keep those dimensions separate so a
        # completed, playable call with a failed behavioural/evidence verdict is not reported as a
        # failed call.
        scenario_completed = receipts.filter(status="passed").count()
        scenario_failed = receipts.filter(status__in=("failed", "errored")).count()
        # A run's calls hang off its executions, a build's off its scenarios.
        calls = CallExecution.no_workspace_objects.filter(
            Q(hosted_registration__receipts__attempt_number=current_attempt_number)
            | Q(
                hosted_harness_execution__receipt__attempt_number=(
                    current_attempt_number
                )
            ),
            test_execution_id=locked_job.test_execution_id,
        ).distinct()
        calls_completed = calls.filter(
            status=CallExecution.CallStatus.COMPLETED
        ).count()
        calls_failed = calls.filter(
            status__in=(
                CallExecution.CallStatus.FAILED,
                CallExecution.CallStatus.CANCELLED,
            )
        ).count()
        TestExecution.no_workspace_objects.filter(
            id=locked_job.test_execution_id
        ).update(
            completed_calls=calls_completed,
            failed_calls=calls_failed,
        )
        # A queryset update bypasses ``auto_now``, so the content timestamp is set
        # explicitly here: scenarios finishing is exactly the kind of progress the
        # environments list means by "last updated".
        HostedHarnessJob.no_workspace_objects.filter(id=locked_job.id).exclude(
            completed_count=scenario_completed, failed_count=scenario_failed
        ).update(
            completed_count=scenario_completed,
            failed_count=scenario_failed,
            content_updated_at=timezone.now(),
        )


def _attempt_terminal_state(attempt: HostedHarnessAttempt) -> str:
    if attempt.terminal_stage == "completed":
        return HostedHarnessAttempt.State.COMPLETED
    if attempt.terminal_stage == "canceled":
        return HostedHarnessAttempt.State.CANCELED
    return HostedHarnessAttempt.State.FAILED


def _provision_response(
    job: HostedHarnessJob, registrations: list[HostedHarnessScenario]
) -> dict[str, Any]:
    return {
        "result": {
            "run_test_id": str(job.run_test_id),
            "scenarios": [
                {
                    "scenario_key": item.scenario_key,
                    "scenario_id": str(item.scenario_id),
                }
                for item in registrations
            ],
        }
    }


def _call_for_registration(
    registration: HostedHarnessScenario,
    calls_by_key: dict[tuple[uuid.UUID, uuid.UUID | None], CallExecution],
) -> CallExecution | None:
    """Resolve a hosted scenario key to its exact dataset-row call.

    ``dataset_row`` is null only for registrations created by an older
    deployment. Their legacy datasets contain exactly one row, so a unique
    scenario match remains safe during a rolling upgrade.
    """
    if registration.dataset_row_id:
        return calls_by_key.get((registration.scenario_id, registration.dataset_row_id))
    matches = [
        call
        for (scenario_id, _row_id), call in calls_by_key.items()
        if scenario_id == registration.scenario_id
    ]
    return matches[0] if len(matches) == 1 else None


def _begin_response(
    job: HostedHarnessJob, registrations: list[HostedHarnessScenario]
) -> dict[str, Any]:
    return {
        "result": {
            "test_execution_id": str(job.test_execution_id),
            "scenarios": [
                {
                    "scenario_key": item.scenario_key,
                    "scenario_id": str(item.scenario_id),
                    "call_execution_id": str(item.call_execution_id),
                }
                for item in registrations
            ],
        }
    }
