"""Daemon-free slot lifecycle planning and local generated-file management."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Callable, Sequence

from .catalog import (
    CATALOG,
    estimate_resources,
    expand_services,
    parse_isolated_infra,
    parse_services,
    private_ports,
)
from .models import (
    ProviderRecord,
    Registry,
    ResourceEstimate,
    SlotRecord,
    StateIdentity,
)
from .provisioning import (
    StateCommand,
    ProvisionPlan,
    PurgePlan,
    SuspendPlan,
    apply_provision_plan,
    apply_purge_plan,
    apply_suspend_plan,
    build_provision_plan,
    build_purge_plan,
    build_suspend_plan,
)
from .registry import RegistryStore
from .registry import CommandRunner, _subprocess_runner
from .state import adapt_orchestrator_state, build_state_plan, volume_name

DEFAULT_MEMORY_CAP_MIB = 16 * 1024
ADMISSION_RATIO = 0.75
CONTROL_PLANE_MEMORY_MIB = 2_600
SIMULATION_WHEEL = "agent_learning_kit-0.1.0-py3-none-any.whl"
STATE_COUPLED_GROUPS = frozenset(("simulation", "collector", "peerdb"))
ISOLATED_MEMORY_MIB = {
    "postgres": 700,
    "clickhouse": 1_000,
    "redis": 200,
    "rabbitmq": 500,
    "minio": 500,
    "temporal": 900,
}
COMPOSE_GROUP_MEMBERS: dict[str, tuple[str, ...]] = {
    "backend": ("backend", "worker"),
    # The PeerDB template owns this aggregate target.  Its dependency graph
    # starts catalog, Temporal/init, MinIO, flow, server, UI and setup jobs.
    "peerdb": ("peerdb",),
}
COMPOSE_GROUP_PRIMARY: dict[str, str] = {"peerdb": "peerdb-ui"}
BUILDABLE_GROUPS = frozenset(
    ("frontend", "backend", "simulation", "gateway", "collector", "serving", "executor")
)
SHARED_PROVIDER_OVERRIDES: dict[str, dict[str, str]] = {
    "backend": {
        "SLOT_BACKEND_NAME": "shared-backend",
        "SLOT_WORKER_NAME": "shared-backend-worker",
        "SLOT_BACKEND_MEDIA_VOLUME": "futureagi-shared-backend-media",
    },
    "simulation": {"SLOT_SIMULATION_NAME": "shared-simulation"},
    "gateway": {"SLOT_GATEWAY_NAME": "shared-gateway"},
    "collector": {
        "SLOT_COLLECTOR_NAME": "shared-collector",
        "SLOT_COLLECTOR_VOLUME": "futureagi-shared-collector-data",
    },
    "serving": {"SLOT_SERVING_NAME": "shared-serving"},
    "executor": {"SLOT_EXECUTOR_NAME": "shared-executor"},
    "observability": {"SLOT_OBSERVABILITY_NAME": "shared-observability"},
    "peerdb": {
        "SLOT_PEERDB_AGGREGATE_NAME": "shared-peerdb-aggregate",
        "SLOT_PEERDB_CATALOG_NAME": "shared-peerdb-catalog",
        "SLOT_PEERDB_TEMPORAL_NAME": "shared-peerdb-temporal",
        "SLOT_PEERDB_TEMPORAL_INIT_NAME": "shared-peerdb-temporal-init",
        "SLOT_PEERDB_MINIO_NAME": "shared-peerdb-minio",
        "SLOT_PEERDB_MINIO_INIT_NAME": "shared-peerdb-minio-init",
        "SLOT_PEERDB_FLOW_API_NAME": "shared-peerdb-flow-api",
        "SLOT_PEERDB_FLOW_WORKER_NAME": "shared-peerdb-flow-worker",
        "SLOT_PEERDB_SERVER_NAME": "shared-peerdb-server",
        "SLOT_PEERDB_UI_NAME": "shared-peerdb-ui",
        "SLOT_PEERDB_INIT_NAME": "shared-peerdb-init",
        "SLOT_PEERDB_CATALOG_VOLUME": "futureagi-shared-peerdb-catalog-data",
        "SLOT_PEERDB_MINIO_VOLUME": "futureagi-shared-peerdb-minio-data",
    },
}


class ResourceAdmissionError(ValueError):
    pass


@dataclass(frozen=True)
class Command:
    argv: tuple[str, ...]
    cwd: Path
    already_exists_markers: tuple[str, ...] = ()


@dataclass(frozen=True)
class LifecyclePlan:
    action: str
    slot: int
    record: SlotRecord | None
    commands: tuple[Command, ...]
    state_commands: tuple[StateCommand, ...] = ()
    stopped_providers: tuple[str, ...] = ()


CommandExecutor = Callable[[Sequence[str], Path], None]
StateCommandExecutor = Callable[[StateCommand], object]


def allocate_slot(value: str | int, registry: Registry) -> int:
    if isinstance(value, str) and value.strip().lower() == "auto":
        occupied = {record.slot for record in registry.slots.values()}
        for candidate in range(1, 21):
            if candidate not in occupied:
                return candidate
        raise ValueError("no slots available (all slots 1 through 20 are in use)")
    try:
        slot = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("SLOT must be 1 through 20 or auto") from error
    if not 1 <= slot <= 20:
        raise ValueError("SLOT must be an integer from 1 through 20")
    return slot


def routes_for_slot(slot: int, http_port: str = "80") -> dict[str, str]:
    suffix = "" if http_port == "80" else f":{http_port}"
    routes = {
        "frontend": f"http://{slot}.localhost{suffix}",
        "backend": f"http://api.{slot}.localhost{suffix}",
    }
    for service in (
        "temporal",
        "peerdb",
        "minio",
        "minio-console",
        "rabbitmq",
        "gateway",
        "serving",
        "collector",
        "executor",
        "jaeger",
    ):
        routes[service] = f"http://{service}.{slot}.localhost{suffix}"
    return routes


def normalise_http_port(environment: dict[str, str]) -> str:
    """Return a canonical public HTTP port without touching runtime state."""
    raw = environment.get("SLOTS_HTTP_PORT", "80")
    if not isinstance(raw, str) or not raw.isdecimal():
        raise ValueError("SLOTS_HTTP_PORT must be an integer from 1 through 65535")
    port = int(raw)
    if not 1 <= port <= 65535:
        raise ValueError("SLOTS_HTTP_PORT must be an integer from 1 through 65535")
    return str(port)


def effective_private_groups(requested_private: tuple[str, ...]) -> tuple[str, ...]:
    """Close private backend selection over providers bound to its state tuple."""
    selected = set(requested_private)
    if "backend" in selected:
        selected.update(STATE_COUPLED_GROUPS)
    return tuple(group for group in CATALOG if group in selected)


def provider_name(slot: int, group: str, private: bool) -> str:
    return f"slot-{slot:02d}-{group}" if private else f"shared-{group}"


def compose_members(group: str) -> tuple[str, ...]:
    """Return exact Compose service members for one logical provider group."""
    return COMPOSE_GROUP_MEMBERS.get(group, (group,))


def compose_primary_member(group: str) -> str:
    return COMPOSE_GROUP_PRIMARY.get(group, group)


def command_for(
    action: str,
    record: SlotRecord,
    state_dir: Path,
    service: str | None = None,
    command: str | None = None,
) -> Command:
    """Build a future Compose command without executing it."""
    private_services = tuple(
        service for service in record.resources.services if service != "frontend"
    )
    compose_files = (
        "-f",
        "slots/compose/frontend.yaml",
        *(
            argument
            for service in private_services
            for argument in ("-f", f"slots/compose/{service}.yaml")
        ),
    )
    base = (
        "docker",
        "compose",
        "--project-name",
        f"futureagi-slot-{record.slot:02d}",
        *compose_files,
        "--env-file",
        str(state_dir / "slots" / f"{record.slot:02d}" / "slot.env"),
    )
    if action == "up":
        targets = [
            member for group in private_services for member in compose_members(group)
        ]
        targets.append("frontend")
        args = (*base, "up", "--detach", *targets)
    elif action == "build":
        if service not in BUILDABLE_GROUPS:
            raise ValueError(f"service has no local build: {service}")
        args = (*base, "build", compose_primary_member(service))
    elif action == "down":
        args = (*base, "down")
    elif action == "logs":
        args = (
            *base,
            "logs",
            "--follow",
            compose_primary_member(service or "frontend"),
        )
    elif action == "shell":
        args = (*base, "exec", compose_primary_member(service or "frontend"), "sh")
    elif action == "run":
        if not command:
            raise ValueError("COMMAND is required for slot-run")
        args = (
            *base,
            "exec",
            compose_primary_member(service or "frontend"),
            "sh",
            "-lc",
            command,
        )
    else:
        raise ValueError(f"unsupported lifecycle action: {action}")
    return Command(args, Path(record.worktree))


def _control_plane_command(record: SlotRecord, state_dir: Path) -> Command:
    return Command(
        (
            "docker",
            "compose",
            "--project-name",
            "futureagi-slots",
            "-f",
            "slots/compose/control-plane.yaml",
            "--env-file",
            str(state_dir / "slots" / f"{record.slot:02d}" / "slot.env"),
            "up",
            "--detach",
            "--wait",
        ),
        Path(record.worktree),
    )


def _shared_backend_command(record: SlotRecord, state_dir: Path) -> Command:
    return Command(
        (
            "docker",
            "compose",
            "--project-name",
            "futureagi-slots-default-backend",
            "-f",
            "slots/compose/backend.yaml",
            "--env-file",
            str(state_dir / "slots" / f"{record.slot:02d}" / "slot.env"),
            "up",
            "--detach",
            "backend",
            "worker",
        ),
        Path(record.worktree),
    )


def _network_command(record: SlotRecord) -> Command:
    # This is deliberately an exact, small idempotent operation.  It runs before
    # the control plane so every subsequent Compose project can join the network.
    return Command(
        ("docker", "network", "create", "futureagi-slots"),
        Path(record.worktree),
        ("already exists",),
    )


def _shared_provider_command(
    group: str,
    record: SlotRecord | None,
    state_dir: Path,
    action: str,
    owner_worktree: Path | None = None,
    force_recreate: bool = False,
) -> Command:
    targets = compose_members(group)
    suffix = (
        (compose_primary_member(group),)
        if action == "build"
        else (
            "--detach",
            *(("--force-recreate",) if force_recreate else ()),
            *targets,
        )
        if action == "up"
        else ()
    )
    if owner_worktree is None:
        if record is None:
            raise ValueError("shared provider command requires an owner worktree")
        owner_worktree = Path(record.worktree)
    return Command(
        (
            "docker",
            "compose",
            "--project-name",
            f"futureagi-slots-default-{group}",
            "-f",
            f"slots/compose/{group}.yaml",
            "--env-file",
            str(state_dir / "shared" / f"{group}.env"),
            action,
            *suffix,
        ),
        owner_worktree,
    )


def _control_plane_down_command(record: SlotRecord, state_dir: Path) -> Command:
    return _control_plane_down_with_env(
        Path(record.worktree), state_dir / "slots" / f"{record.slot:02d}" / "slot.env"
    )


def _traefik_restart_command(record: SlotRecord, state_dir: Path) -> Command:
    """Reload bind-mounted routes reliably on Docker Desktop/Colima fileshares."""
    return Command(
        (
            "docker",
            "compose",
            "--project-name",
            "futureagi-slots",
            "-f",
            "slots/compose/control-plane.yaml",
            "--env-file",
            str(state_dir / "slots" / f"{record.slot:02d}" / "slot.env"),
            "restart",
            "traefik",
        ),
        Path(record.worktree),
    )


def _control_plane_down_with_env(worktree: Path, env_file: Path) -> Command:
    return Command(
        (
            "docker",
            "compose",
            "--project-name",
            "futureagi-slots",
            "-f",
            "slots/compose/control-plane.yaml",
            "--env-file",
            str(env_file),
            "down",
        ),
        worktree,
    )


def _network_remove_command(record: SlotRecord) -> Command:
    return Command(
        ("docker", "network", "rm", "futureagi-slots"), Path(record.worktree)
    )


def _purge_project_command(record: SlotRecord, state_dir: Path) -> Command:
    command = command_for("down", record, state_dir)
    return Command((*command.argv, "--volumes"), command.cwd)


def execute_commands(commands: Sequence[Command], executor: CommandExecutor) -> None:
    """Execute plans only through an explicitly supplied adapter."""
    for command in commands:
        try:
            executor(command.argv, command.cwd)
        except Exception as error:
            message = " ".join(
                str(value)
                for value in (
                    error,
                    getattr(error, "stderr", ""),
                    getattr(error, "output", ""),
                )
                if value
            ).casefold()
            if command.already_exists_markers and any(
                marker in message for marker in command.already_exists_markers
            ):
                continue
            raise


class SlotRuntime:
    def __init__(
        self,
        store: RegistryStore,
        worktree: Path,
        git_runner: CommandRunner = _subprocess_runner,
    ) -> None:
        self.store = store
        self.worktree = worktree.resolve()
        self.git_runner = git_runner

    def plan_up(
        self,
        slot_value: str | int,
        services_value: str | None = None,
        isolated_infra_value: str | None = None,
        revision: str = "",
        environment: dict[str, str] | None = None,
    ) -> LifecyclePlan:
        with self.store.locked() as registry:
            return self._build_up(
                registry,
                slot_value,
                services_value,
                isolated_infra_value,
                revision,
                environment or {},
            )

    def apply_up(
        self,
        slot_value: str | int,
        services_value: str | None,
        isolated_infra_value: str | None,
        revision: str,
        environment: dict[str, str],
        executor: CommandExecutor,
        state_executor: StateCommandExecutor | None = None,
    ) -> LifecyclePlan:
        with self.store.locked() as registry:
            self._require_clean_recovery()
            plan = self._build_up(
                registry,
                slot_value,
                services_value,
                isolated_infra_value,
                revision,
                environment,
            )
            assert plan.record is not None
            self._validate_local_build_inputs(plan.record, registry)
            previous = registry.slots.get(str(plan.record.slot))
            suspend_commands: tuple[StateCommand, ...] = ()
            if previous is not None and previous.isolated_infra:
                previous_state = self._state_plan_for(previous)
                suspend_commands = build_suspend_plan(
                    previous_state, state_dir=self.store.state_dir
                ).commands
            if (plan.state_commands or suspend_commands) and state_executor is None:
                raise ValueError(
                    "runtime state execution requires an injected StateCommand executor"
                )
            replacement_count = 1 if previous is not None else 0
            snapshots: dict[Path, bytes | None] = {}
            journal_written = False
            external_attempted = False
            try:
                if replacement_count:
                    self._write_recovery_journal(plan.record, "up")
                    journal_written = True
                    if suspend_commands:
                        external_attempted = True
                        apply_suspend_plan(
                            SuspendPlan(previous_state, suspend_commands),
                            state_executor,
                        )
                    external_attempted = True
                    execute_commands(plan.commands[:replacement_count], executor)
                snapshots = self._stage_artifacts(plan.record)
                new_shared_groups = tuple(
                    group
                    for group, name in plan.record.providers.items()
                    if group != "frontend"
                    and name.startswith("shared-")
                    and name not in registry.providers
                )
                for group in new_shared_groups:
                    self._write_shared_environment(group, plan.record)
                if not journal_written:
                    self._write_recovery_journal(plan.record, "up")
                    journal_written = True
                external_attempted = True
                execute_commands(
                    plan.commands[replacement_count : replacement_count + 2], executor
                )
                if plan.state_commands:
                    state = build_state_plan(
                        plan.record.slot,
                        provider_slot=plan.record.slot
                        if plan.record.providers["backend"]
                        == provider_name(plan.record.slot, "backend", True)
                        else None,
                        isolate_infra=plan.record.isolated_infra,
                    )
                    external_attempted = True
                    apply_provision_plan(
                        ProvisionPlan(state, plan.state_commands), state_executor
                    )
                external_attempted = True
                execute_commands(plan.commands[replacement_count + 2 :], executor)
                updated = self._attach(registry, plan.record)
                updated, handoffs = self._handoff_shared_owners(
                    updated,
                    previous.worktree if previous is not None else "",
                    executor,
                )
                stopped_shared = tuple(
                    provider
                    for name, provider in registry.providers.items()
                    if name not in updated.providers and not provider.private
                )
                execute_commands(
                    tuple(
                        _shared_provider_command(
                            provider.group,
                            plan.record,
                            self.store.state_dir,
                            "down",
                            Path(provider.worktree),
                        )
                        for provider in stopped_shared
                    ),
                    executor,
                )
                self.store.save(updated)
            except Exception:
                # Preserve the exact desired topology once a Docker mutation
                # may have happened so `slots-recover` can reconcile it.
                if snapshots and not external_attempted:
                    self._restore_artifacts(snapshots)
                if journal_written and not external_attempted:
                    self._remove_recovery_journal()
                raise
            self._remove_recovery_journal()
            return LifecyclePlan(
                plan.action,
                plan.slot,
                plan.record,
                (*plan.commands, *handoffs) if previous is not None else plan.commands,
                plan.state_commands,
                plan.stopped_providers,
            )

    def _validate_local_build_inputs(
        self, record: SlotRecord, registry: Registry
    ) -> None:
        """Reject unavailable simulation inputs before any runtime mutation.

        The repository's dev simulation Dockerfile deliberately consumes an
        unreleased Agent Learning Kit wheel from the build-context root. A
        missing wheel otherwise fails only after the large backend build and
        after the shared control plane has already started.
        """
        dockerfile = self.worktree / "Dockerfile.simulation-runner.dev"
        if not dockerfile.is_file():
            # Synthetic/unit-test worktrees do not carry application sources.
            return
        simulation = record.providers["simulation"]
        simulation_will_build = (
            "simulation" in record.resources.services
            or simulation not in registry.providers
        )
        if simulation_will_build and not (self.worktree / SIMULATION_WHEEL).is_file():
            raise ValueError(
                f"simulation build requires {SIMULATION_WHEEL} at the worktree "
                "root (required by Dockerfile.simulation-runner.dev); copy or "
                "build the Agent Learning Kit wheel before slot-up"
            )

    def plan_down(self, slot_value: str | int) -> LifecyclePlan:
        with self.store.locked() as registry:
            slot = allocate_slot(slot_value, registry)
            record = registry.slots.get(str(slot))
            if record is None:
                raise ValueError(f"slot {slot} is not registered")
            suspend_commands = (
                build_suspend_plan(
                    self._state_plan_for(record), state_dir=self.store.state_dir
                ).commands
                if record.isolated_infra
                else ()
            )
            return LifecyclePlan(
                "down",
                slot,
                record,
                (command_for("down", record, self.store.state_dir),),
                suspend_commands,
            )

    def apply_down(
        self,
        slot_value: str | int,
        executor: CommandExecutor,
        state_executor: StateCommandExecutor | None = None,
    ) -> LifecyclePlan:
        with self.store.locked() as registry:
            self._require_clean_recovery()
            slot = allocate_slot(slot_value, registry)
            record = registry.slots.get(str(slot))
            if record is None:
                raise ValueError(f"slot {slot} is not registered")
            suspend_commands = (
                build_suspend_plan(
                    self._state_plan_for(record), state_dir=self.store.state_dir
                ).commands
                if record.isolated_infra
                else ()
            )
            if suspend_commands and state_executor is None:
                raise ValueError(
                    "runtime state execution requires an injected StateCommand executor"
                )
            plan = LifecyclePlan(
                "down",
                slot,
                record,
                (command_for("down", record, self.store.state_dir),),
            )
            self._write_recovery_journal(record, "down")
            if suspend_commands:
                apply_suspend_plan(
                    SuspendPlan(self._state_plan_for(record), suspend_commands),
                    state_executor,
                )
            execute_commands(plan.commands, executor)
            updated, stopped = self._detach(registry, record)
            updated, handoffs = self._handoff_shared_owners(
                updated, record.worktree, executor
            )
            shared_stops = tuple(
                _shared_provider_command(
                    registry.providers[name].group,
                    record,
                    self.store.state_dir,
                    "down",
                    Path(registry.providers[name].worktree),
                )
                for name in stopped
                if name in registry.providers and not registry.providers[name].private
            )
            execute_commands(shared_stops, executor)
            tail: tuple[Command, ...] = ()
            if not updated.slots:
                tail = (
                    _control_plane_down_command(record, self.store.state_dir),
                    _network_remove_command(record),
                )
                execute_commands(tail, executor)
            self.store.save(updated)
            route_removed = self._remove_route(record)
            route_reload = self._reload_routes_for_survivors(
                updated, executor, route_removed
            )
            self._remove_recovery_journal()
            return LifecyclePlan(
                "down",
                slot,
                record,
                (*plan.commands, *handoffs, *shared_stops, *tail, *route_reload),
                suspend_commands,
                stopped,
            )

    def plan_purge(
        self, slot_value: str | int, confirmation: str | None
    ) -> LifecyclePlan:
        slot = allocate_slot(slot_value, self.store.load())
        if confirmation != str(slot):
            raise ValueError(f"slot-purge requires CONFIRM={slot}")
        record = self.store.load().slots.get(str(slot)) or self._manifest_record(slot)
        if record is None:
            raise ValueError(f"slot {slot} is not registered")
        state_commands: tuple[StateCommand, ...] = ()
        if (
            record.providers["backend"] == provider_name(slot, "backend", True)
            or record.retained_private_backend_state
        ):
            state = self._purge_state_plan(record)
            state_commands = build_purge_plan(
                state,
                state_dir=self.store.state_dir if state.isolated_infra else None,
                confirm=confirmation,
            ).commands
        retired_volumes = self._retired_private_volume_commands(record)
        return LifecyclePlan(
            "purge",
            slot,
            record,
            (_purge_project_command(record, self.store.state_dir), *retired_volumes),
            state_commands,
        )

    def apply_purge(
        self,
        slot_value: str | int,
        confirmation: str,
        executor: CommandExecutor,
        state_executor: StateCommandExecutor | None = None,
    ) -> LifecyclePlan:
        with self.store.locked() as registry:
            self._require_clean_recovery()
            slot = allocate_slot(slot_value, registry)
            record = registry.slots.get(str(slot)) or self._manifest_record(slot)
            if record is None:
                raise ValueError(f"slot {slot} is not registered")
            if confirmation != str(slot):
                raise ValueError(f"slot-purge requires CONFIRM={slot}")
            private_backend = (
                record.providers["backend"] == provider_name(slot, "backend", True)
                or record.retained_private_backend_state
            )
            purge_commands: tuple[StateCommand, ...] = ()
            purge_state = None
            if private_backend:
                purge_state = self._purge_state_plan(record)
                purge_commands = build_purge_plan(
                    purge_state,
                    state_dir=(
                        self.store.state_dir if purge_state.isolated_infra else None
                    ),
                    confirm=confirmation,
                ).commands
            if purge_commands and state_executor is None:
                raise ValueError(
                    "runtime state execution requires an injected StateCommand executor"
                )
            runtime_down = _purge_project_command(record, self.store.state_dir)
            retired_volumes = self._retired_private_volume_commands(record)
            self._write_recovery_journal(record, "purge")
            if record.isolated_infra:
                suspend = build_suspend_plan(
                    self._state_plan_for(record), state_dir=self.store.state_dir
                )
                apply_suspend_plan(suspend, state_executor)
            execute_commands((runtime_down,), executor)
            execute_commands(retired_volumes, executor)
            if purge_commands:
                assert purge_state is not None
                apply_purge_plan(
                    PurgePlan(
                        purge_state,
                        purge_commands,
                        confirmation,
                    ),
                    state_executor,
                )
            updated, stopped = (
                self._detach(registry, record)
                if str(slot) in registry.slots
                else (registry, ())
            )
            updated, handoffs = self._handoff_shared_owners(
                updated, record.worktree, executor
            )
            shared_stops = tuple(
                _shared_provider_command(
                    registry.providers[name].group,
                    record,
                    self.store.state_dir,
                    "down",
                    Path(registry.providers[name].worktree),
                )
                for name in stopped
                if name in registry.providers and not registry.providers[name].private
            )
            execute_commands(shared_stops, executor)
            tail: tuple[Command, ...] = ()
            if not updated.slots and str(slot) in registry.slots:
                tail = (
                    _control_plane_down_command(record, self.store.state_dir),
                    _network_remove_command(record),
                )
                execute_commands(tail, executor)
            self.store.save(updated)
            route_removed = self._remove_route(record)
            route_reload = self._reload_routes_for_survivors(
                updated, executor, route_removed
            )
            directory = self.store.state_dir / "slots" / f"{slot:02d}"
            for path in (directory / "slot.env", directory / "manifest.json"):
                if path.exists():
                    path.unlink()
            self._remove_recovery_journal()
            return LifecyclePlan(
                "purge",
                slot,
                record,
                (
                    runtime_down,
                    *retired_volumes,
                    *handoffs,
                    *shared_stops,
                    *tail,
                    *route_reload,
                ),
                purge_commands,
                stopped,
            )

    def status(self, slot_value: str | int | None = None) -> tuple[SlotRecord, ...]:
        registry = self.store.load()
        if slot_value is None:
            return tuple(registry.slots[key] for key in sorted(registry.slots, key=int))
        slot = allocate_slot(slot_value, registry)
        record = registry.slots.get(str(slot))
        return () if record is None else (record,)

    def doctor(
        self, executor: CommandExecutor, environment: dict[str, str] | None = None
    ) -> dict[str, object]:
        """Run approved, read-only runtime inspection through the injected adapter."""
        registry = self.store.load()
        commands: list[Command] = [
            Command(
                ("docker", "network", "inspect", "futureagi-slots"),
                self.worktree,
                # A missing network is the healthy clean-state result before
                # the first slot starts, not a doctor failure.
                ("no such network", "network futureagi-slots not found"),
            )
        ]
        if registry.slots:
            owner = registry.slots[sorted(registry.slots, key=int)[0]]
            commands.append(
                Command(
                    (
                        "docker",
                        "compose",
                        "--project-name",
                        "futureagi-slots",
                        "-f",
                        "slots/compose/control-plane.yaml",
                        "--env-file",
                        str(self._slot_env_path(owner)),
                        "ps",
                    ),
                    Path(owner.worktree),
                )
            )
        execute_commands(commands, executor)
        cap = int(
            (environment or os.environ).get(
                "SLOTS_MEMORY_CAP_MIB", DEFAULT_MEMORY_CAP_MIB
            )
        )
        journal = self._recovery_journal_path()
        return {
            "configured_memory_cap_mib": cap,
            "admission_limit_mib": int(cap * ADMISSION_RATIO),
            "recovery_journal": (
                journal.read_text(encoding="utf-8").strip()
                if journal.exists()
                else None
            ),
            "slots": len(registry.slots),
        }

    def apply_prune(self, executor: CommandExecutor) -> LifecyclePlan:
        """Recover legacy/corrupt zero-reference shared provider metadata.

        Normal `down` removes the final provider reference and its metadata in
        one transaction.  Consequently prune is deliberately *not* a normal
        lifecycle operation or a way to preserve reusable defaults: it repairs
        stale registry entries left by an interrupted older implementation.
        """
        with self.store.locked() as registry:
            self._require_clean_recovery()
            stale = tuple(
                provider
                for provider in registry.providers.values()
                if not provider.references and not provider.private
            )
            commands = tuple(
                Command(
                    (
                        "docker",
                        "compose",
                        "--project-name",
                        f"futureagi-slots-default-{provider.group}",
                        "-f",
                        f"slots/compose/{provider.group}.yaml",
                        "down",
                    ),
                    Path(provider.worktree),
                )
                for provider in stale
            )
            execute_commands(commands, executor)
            if stale:
                stale_names = {provider.name for provider in stale}
                providers = {
                    name: provider
                    for name, provider in registry.providers.items()
                    if name not in stale_names
                }
                self.store.save(
                    Registry(registry.version, dict(registry.slots), providers)
                )
            return LifecyclePlan("prune", 0, None, commands)

    def apply_recover(
        self,
        executor: CommandExecutor,
        state_executor: StateCommandExecutor | None = None,
    ) -> LifecyclePlan:
        """Reconcile an interrupted lifecycle operation without deleting volumes."""
        with self.store.locked() as registry:
            journal_path = self._recovery_journal_path()
            if not journal_path.exists():
                raise ValueError("no slot recovery journal exists")
            details = json.loads(journal_path.read_text(encoding="utf-8"))
            action = details.get("action")
            if action not in {"up", "down", "purge"}:
                raise ValueError(f"unsupported recovery journal action: {action!r}")
            slot = allocate_slot(details.get("slot"), registry)
            raw_record = details.get("record")
            record = (
                SlotRecord.from_dict(raw_record)
                if isinstance(raw_record, dict)
                else self._manifest_record(slot)
            )
            previous = registry.slots.get(str(slot))
            if action in {"down", "purge"}:
                # Down and purge commit the registry only after their exact
                # runtime/state operations succeed. If the old record remains,
                # clearing the marker makes the original idempotent command
                # explicitly retryable. Purge is never resumed automatically:
                # it must cross the CONFIRM and approval gates again.
                if previous is not None:
                    self._remove_recovery_journal()
                    return LifecyclePlan(f"recover-{action}-retry", slot, previous, ())

                # A missing registry entry means the operation committed and
                # only post-commit artifact cleanup may have been interrupted.
                route_removed = (
                    self._remove_route(record) if record is not None else False
                )
                commands = self._reload_routes_for_survivors(
                    registry, executor, route_removed, force=True
                )
                if record is not None:
                    if action == "purge":
                        directory = self.store.state_dir / "slots" / f"{slot:02d}"
                        for path in (
                            directory / "slot.env",
                            directory / "manifest.json",
                        ):
                            if path.exists():
                                path.unlink()
                self._remove_recovery_journal()
                return LifecyclePlan(
                    f"recover-{action}-complete", slot, record, commands
                )

            commands: list[Command] = []
            if record is not None:
                # The code may have been fixed after the failed attempt. Rebuild
                # generated env/config from the typed journal record so recovery
                # is not blocked by newly required interpolation variables.
                self._stage_artifacts(record)
                if previous is not None:
                    # The committed registry still describes the pre-replace
                    # topology. Remove only the partially recreated private
                    # project; shared providers and the control plane may be
                    # serving other slots. The next slot-up can then perform a
                    # clean replacement while every named volume remains.
                    suspend_commands: tuple[StateCommand, ...] = ()
                    if record.isolated_infra:
                        if state_executor is None:
                            raise ValueError(
                                "runtime state execution requires an injected "
                                "StateCommand executor"
                            )
                        state = self._state_plan_for(record)
                        suspend_commands = build_suspend_plan(
                            state, state_dir=self.store.state_dir
                        ).commands
                        apply_suspend_plan(
                            SuspendPlan(state, suspend_commands), state_executor
                        )
                    commands.append(command_for("down", record, self.store.state_dir))
                    for group, name in record.providers.items():
                        if (
                            group != "frontend"
                            and name.startswith("shared-")
                            and name not in registry.providers
                        ):
                            commands.append(
                                _shared_provider_command(
                                    group,
                                    record,
                                    self.store.state_dir,
                                    "down",
                                    Path(record.worktree),
                                )
                            )
                    execute_commands(commands, executor)
                    self._remove_recovery_journal()
                    return LifecyclePlan(
                        "recover-replacement",
                        slot,
                        record,
                        tuple(commands),
                        suspend_commands,
                    )
                if record.isolated_infra:
                    if state_executor is None:
                        raise ValueError(
                            "runtime state execution requires an injected StateCommand executor"
                        )
                    suspend = build_suspend_plan(
                        self._state_plan_for(record), state_dir=self.store.state_dir
                    )
                    apply_suspend_plan(suspend, state_executor)
                commands.append(command_for("down", record, self.store.state_dir))
                for group, name in record.providers.items():
                    if (
                        group != "frontend"
                        and name.startswith("shared-")
                        and name not in registry.providers
                    ):
                        commands.append(
                            _shared_provider_command(
                                group,
                                record,
                                self.store.state_dir,
                                "down",
                                Path(record.worktree),
                            )
                        )
                control_env = self._slot_env_path(record)
                control_worktree = Path(record.worktree)
            else:
                # Older journals did not embed the desired record. Provider
                # env files still identify safe, exact shared projects and can
                # also tear down the control plane created before the failure.
                shared_envs = tuple(
                    sorted((self.store.state_dir / "shared").glob("*.env"))
                )
                if not shared_envs:
                    raise ValueError(
                        "legacy recovery journal has no manifest or shared environment"
                    )
                for env_path in shared_envs:
                    group = env_path.stem
                    if group in CATALOG and f"shared-{group}" not in registry.providers:
                        commands.append(
                            _shared_provider_command(
                                group,
                                None,
                                self.store.state_dir,
                                "down",
                                self.worktree,
                            )
                        )
                control_env = shared_envs[0]
                control_worktree = self.worktree
            if not registry.slots:
                commands.extend(
                    (
                        _control_plane_down_with_env(control_worktree, control_env),
                        Command(
                            ("docker", "network", "rm", "futureagi-slots"),
                            control_worktree,
                            ("not found", "no such network"),
                        ),
                    )
                )
            execute_commands(commands, executor)
            if record is not None:
                self._remove_route(record)
            self._remove_recovery_journal()
            return LifecyclePlan("recover", slot, record, tuple(commands))

    def service_command(
        self, record: SlotRecord, action: str, service: str, command: str | None = None
    ) -> Command:
        if service != "frontend" and service not in CATALOG:
            raise ValueError(f"unsupported service: {service}")
        provider = self.store.load().providers.get(record.providers.get(service, ""))
        if provider is None or provider.private or service == "frontend":
            return command_for(action, record, self.store.state_dir, service, command)
        base = (
            "docker",
            "compose",
            "--project-name",
            f"futureagi-slots-default-{provider.group}",
            "-f",
            f"slots/compose/{provider.group}.yaml",
            "--env-file",
            str(self._shared_env_path(provider.group)),
        )
        if action == "logs":
            argv = (*base, "logs", "--follow", compose_primary_member(provider.group))
        elif action == "shell":
            argv = (*base, "exec", compose_primary_member(provider.group), "sh")
        elif action == "run" and command:
            argv = (
                *base,
                "exec",
                compose_primary_member(provider.group),
                "sh",
                "-lc",
                command,
            )
        else:
            raise ValueError("COMMAND is required for slot-run")
        return Command(argv, Path(provider.worktree))

    def write_generated_files(self, record: SlotRecord) -> tuple[Path, Path]:
        directory = self.store.state_dir / "slots" / f"{record.slot:02d}"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        env_path = directory / "slot.env"
        manifest_path = directory / "manifest.json"
        values = self.environment_for(record)
        self._write_private(
            env_path,
            self._environment_contents(Path(record.worktree), values),
        )
        self._write_private(
            manifest_path, json.dumps(record.to_dict(), sort_keys=True, indent=2) + "\n"
        )
        return env_path, manifest_path

    def _shared_env_path(self, group: str) -> Path:
        return self.store.state_dir / "shared" / f"{group}.env"

    def _write_shared_environment(
        self,
        group: str,
        record: SlotRecord,
        owner_worktree: Path | None = None,
    ) -> Path:
        """Publish a provider-stable env file; never borrow a slot's env file."""
        shared_state = StateIdentity.for_slot(0)
        values = self.environment_for(record)
        for key in tuple(values):
            if (
                key.startswith("SLOT_ISOLATED_")
                or key.endswith("_HOST")
                or key.endswith("_VOLUME")
                or key == "SLOT_TEMPORAL_UI_NAME"
            ):
                values.pop(key)
        owner = owner_worktree or Path(record.worktree)
        values.update(
            {
                "SLOT": "0",
                "SLOT_ID": "00",
                "SLOT_PROJECT": f"futureagi-slots-default-{group}",
                # Several provider templates bind-mount or build from the
                # provider owner.  This must travel with owner handoff rather
                # than accidentally retaining the first runtime instance.
                "SLOT_WORKTREE": str(owner),
                "SLOT_ENV_FILE": str(self._shared_env_path(group)),
                "SLOT_STATE_ID": shared_state.temporal_namespace,
                "SLOT_PG_DB": shared_state.postgres_database,
                "SLOT_CH_DATABASE": shared_state.clickhouse_database,
                "SLOT_REDIS_DB": str(shared_state.redis_databases[0]),
                "SLOT_REDIS_DB_CACHE": str(shared_state.redis_databases[1]),
                "SLOT_REDIS_DB_LOCK": str(shared_state.redis_databases[2]),
                "SLOT_REDIS_CACHE_DB": str(shared_state.redis_databases[1]),
                "SLOT_REDIS_LOCK_DB": str(shared_state.redis_databases[2]),
                "SLOT_RABBITMQ_VHOST": shared_state.rabbitmq_vhost,
                "SLOT_MINIO_BUCKET": shared_state.minio_bucket,
                "SLOT_TEMPORAL_NAMESPACE": shared_state.temporal_namespace,
            }
        )
        for service in CATALOG:
            values[f"SLOT_{service.upper()}_NAME"] = f"shared-{service}"
        values.update(SHARED_PROVIDER_OVERRIDES[group])
        values["SLOT_TEMPORAL_UI_NAME"] = "futureagi-slots-temporal-ui"
        path = self._shared_env_path(group)
        self._write_private(path, self._environment_contents(owner, values))
        return path

    @staticmethod
    def _environment_contents(worktree: Path, values: dict[str, str]) -> str:
        """Layer generated topology over the worktree's application environment."""
        source = worktree / ".env"
        inherited = source.read_text(encoding="utf-8") if source.is_file() else ""
        if inherited and not inherited.endswith("\n"):
            inherited += "\n"
        generated = "".join(f"{key}={value}\n" for key, value in sorted(values.items()))
        return (
            inherited
            + "# Generated slot topology; values below override matching worktree entries.\n"
            + generated
        )

    def environment_for(self, record: SlotRecord) -> dict[str, str]:
        slot_id = f"{record.slot:02d}"
        values = {
            "SLOTS_STATE_DIR": str(self.store.state_dir),
            "SLOTS_CONTROL_PROJECT": "futureagi-slots",
            "SLOTS_NETWORK": "futureagi-slots",
            "SLOTS_HTTP_PORT": record.http_port,
            "SLOTS_TRAEFIK_NAME": "futureagi-slots-traefik",
            "SLOTS_POSTGRES_NAME": "futureagi-slots-postgres",
            "SLOTS_CLICKHOUSE_NAME": "futureagi-slots-clickhouse",
            "SLOTS_REDIS_NAME": "futureagi-slots-redis",
            "SLOTS_RABBITMQ_NAME": "futureagi-slots-rabbitmq",
            "SLOTS_MINIO_NAME": "futureagi-slots-minio",
            "SLOTS_TEMPORAL_NAME": "futureagi-slots-temporal",
            "SLOT": str(record.slot),
            "SLOT_ID": slot_id,
            "SLOT_PROJECT": f"futureagi-slot-{slot_id}",
            "SLOT_NETWORK": "futureagi-slots",
            "SLOTS_ROUTE_DIR": str(self.store.state_dir / "routes"),
            "SLOTS_TRAEFIK_STATIC_CONFIG": str(
                self.store.state_dir / "control-plane" / "traefik.yaml"
            ),
            "SLOTS_CLICKHOUSE_STORAGE_CONFIG": str(
                self.store.state_dir / "control-plane" / "clickhouse-storage-policy.xml"
            ),
            "SLOTS_CLICKHOUSE_TEST_CONFIG": str(
                self.store.state_dir / "control-plane" / "clickhouse-test-config.xml"
            ),
            "SLOT_WORKTREE": record.worktree,
            "SLOT_ENV_FILE": str(self.store.state_dir / "slots" / slot_id / "slot.env"),
            "SLOT_ROUTE_FILE": str(
                self.store.state_dir / "routes" / f"slot-{slot_id}.yaml"
            ),
            "SLOT_PORT_BASE": str(20000 + record.slot * 100),
            "SLOT_STATE_ID": record.state.temporal_namespace,
            "SLOT_PG_DB": record.state.postgres_database,
            "SLOT_CH_DATABASE": record.state.clickhouse_database,
            "SLOT_REDIS_DB": str(record.state.redis_databases[0]),
            "SLOT_REDIS_DB_CACHE": str(record.state.redis_databases[1]),
            "SLOT_REDIS_DB_LOCK": str(record.state.redis_databases[2]),
            "SLOT_REDIS_CACHE_DB": str(record.state.redis_databases[1]),
            "SLOT_REDIS_LOCK_DB": str(record.state.redis_databases[2]),
            "SLOT_RABBITMQ_VHOST": record.state.rabbitmq_vhost,
            "SLOT_MINIO_BUCKET": record.state.minio_bucket,
            "SLOT_TEMPORAL_NAMESPACE": record.state.temporal_namespace,
        }
        ports = dict(record.ports)
        ports.update(
            {
                key: value
                for key, value in private_ports(
                    record.slot,
                    (),
                    (
                        "postgres",
                        "clickhouse",
                        "redis",
                        "rabbitmq",
                        "minio",
                        "temporal",
                    ),
                ).items()
                if key not in ports
            }
        )
        for service, port in ports.items():
            upper = service.upper()
            values[f"SLOT_{upper}_NAME"] = record.providers.get(
                service, f"slot-{slot_id}-{service}"
            )
            values[f"SLOT_{upper}_PORT"] = str(port)
        for service in CATALOG:
            values.setdefault(
                f"SLOT_{service.upper()}_NAME",
                record.providers.get(
                    service, provider_name(record.slot, service, False)
                ),
            )
        values["SLOT_WORKER_NAME"] = f"{record.providers['backend']}-worker"
        values["SLOT_BACKEND_IMAGE_REF"] = (
            f"futureagi/slot-backend:{slot_id}"
            if record.providers["backend"].startswith(f"slot-{slot_id}-")
            else "futureagi/slot-backend:00"
        )
        peerdb_prefix = f"futureagi-slot-{slot_id}-peerdb"
        if record.providers["peerdb"].startswith(f"slot-{slot_id}-"):
            values.update(
                {
                    "SLOT_PEERDB_AGGREGATE_NAME": f"{peerdb_prefix}-aggregate",
                    "SLOT_PEERDB_CATALOG_NAME": f"{peerdb_prefix}-catalog",
                    "SLOT_PEERDB_TEMPORAL_NAME": f"{peerdb_prefix}-temporal",
                    "SLOT_PEERDB_TEMPORAL_INIT_NAME": f"{peerdb_prefix}-temporal-init",
                    "SLOT_PEERDB_MINIO_NAME": f"{peerdb_prefix}-minio",
                    "SLOT_PEERDB_MINIO_INIT_NAME": f"{peerdb_prefix}-minio-init",
                    "SLOT_PEERDB_FLOW_API_NAME": f"{peerdb_prefix}-flow-api",
                    "SLOT_PEERDB_FLOW_WORKER_NAME": f"{peerdb_prefix}-flow-worker",
                    "SLOT_PEERDB_SERVER_NAME": f"{peerdb_prefix}-server",
                    "SLOT_PEERDB_UI_NAME": f"{peerdb_prefix}-ui",
                    "SLOT_PEERDB_INIT_NAME": f"{peerdb_prefix}-init",
                    "SLOT_PEERDB_CATALOG_VOLUME": f"{peerdb_prefix}-catalog-data",
                    "SLOT_PEERDB_MINIO_VOLUME": f"{peerdb_prefix}-minio-data",
                }
            )
        else:
            values["SLOT_PEERDB_UI_NAME"] = "shared-peerdb-ui"
        values["SLOTS_TEMPORAL_UI_NAME"] = "futureagi-slots-temporal-ui"
        isolated_names = {
            "postgres": f"futureagi-slot-{slot_id}-postgres",
            "clickhouse": f"futureagi-slot-{slot_id}-clickhouse",
            "redis": f"futureagi-slot-{slot_id}-redis",
            "rabbitmq": f"futureagi-slot-{slot_id}-rabbitmq",
            "minio": f"futureagi-slot-{slot_id}-minio",
            "temporal": f"futureagi-slot-{slot_id}-temporal",
        }
        for engine, name in isolated_names.items():
            values[f"SLOT_ISOLATED_{engine.upper()}_NAME"] = name
        values["SLOT_ISOLATED_TEMPORAL_POSTGRES_NAME"] = (
            f"futureagi-slot-{slot_id}-temporal-postgres"
        )
        values["SLOT_TEMPORAL_UI_NAME"] = (
            f"futureagi-slot-{slot_id}-temporal-ui"
            if "temporal" in record.isolated_infra
            else values["SLOTS_TEMPORAL_UI_NAME"]
        )
        if record.isolated_infra:
            prefix = f"futureagi-slot-{slot_id}"
            hosts = {
                "postgres": ("SLOT_PG_HOST", f"{prefix}-postgres"),
                "clickhouse": ("SLOT_CH_HOST", f"{prefix}-clickhouse"),
                "redis": ("SLOT_REDIS_HOST", f"{prefix}-redis"),
                "rabbitmq": ("SLOT_RABBITMQ_HOST", f"{prefix}-rabbitmq"),
                "minio": ("SLOT_MINIO_HOST", f"{prefix}-minio"),
                "temporal": ("SLOT_TEMPORAL_HOST", f"{prefix}-temporal"),
            }
            for engine in record.isolated_infra:
                key, value = hosts[engine]
                values[key] = value
                values[f"SLOT_{engine.upper()}_VOLUME"] = volume_name(
                    record.slot, engine
                )
            if "postgres" in record.isolated_infra:
                values["SLOT_TEMPORAL_POSTGRES_HOST"] = values["SLOT_PG_HOST"]
        return values

    def _build_up(
        self,
        registry: Registry,
        slot_value: str | int,
        services_value: str | None,
        isolated_infra_value: str | None,
        revision: str,
        environment: dict[str, str],
    ) -> LifecyclePlan:
        slot = allocate_slot(slot_value, registry)
        environment = dict(environment)
        environment["SLOTS_HTTP_PORT"] = normalise_http_port(environment)
        active_ports = {item.http_port for item in registry.slots.values()}
        if active_ports and environment["SLOTS_HTTP_PORT"] not in active_ports:
            raise ValueError(
                "SLOTS_HTTP_PORT must match the single public port used by active slots"
            )
        requested = parse_services(services_value)
        requested_private = (
            tuple(CATALOG)
            if requested == ("all",)
            else (() if requested == ("none",) else requested)
        )
        private = effective_private_groups(requested_private)
        services = expand_services(requested)
        isolated = parse_isolated_infra(isolated_infra_value)
        if isolated and "backend" not in private:
            raise ValueError("ISOLATE_INFRA requires SERVICES=backend")
        prior = registry.slots.get(str(slot)) or self._manifest_record(slot)
        record = self._record(
            registry,
            slot,
            requested,
            private,
            services,
            isolated,
            revision,
            environment,
            tuple(
                sorted(
                    (set(prior.isolated_infra) | set(prior.retired_isolated_infra))
                    - set(isolated)
                )
            )
            if prior is not None
            else (),
            (
                "backend" in private
                or (
                    prior.retained_private_backend_state if prior is not None else False
                )
                or (
                    prior.providers["backend"] == provider_name(slot, "backend", True)
                    if prior is not None
                    else False
                )
            ),
        )
        default_groups = tuple(
            service
            for service, name in record.providers.items()
            if service != "frontend"
            and not name.startswith(f"slot-{slot:02d}-")
            and name not in registry.providers
        )
        backend_needs_state = "backend" in default_groups or "backend" in private
        overhead = (CONTROL_PLANE_MEMORY_MIB if not registry.slots else 0) + sum(
            CATALOG[group].memory_mib for group in default_groups
        )
        self._admit(registry, record, environment, overhead)
        commands: list[Command] = []
        previous = registry.slots.get(str(slot))
        if previous is not None:
            commands.append(command_for("down", previous, self.store.state_dir))
        commands.extend(
            (
                _network_command(record),
                _control_plane_command(record, self.store.state_dir),
            )
        )
        state_commands: tuple[StateCommand, ...] = ()
        for group in default_groups:
            self._validate_shared_candidate(
                group, environment.get("BASE_REF", "origin/dev")
            )
        if backend_needs_state:
            state = build_state_plan(
                slot,
                provider_slot=slot if "backend" in private else None,
                isolate_infra=isolated,
            )
            adapt_orchestrator_state(record.state)
            state_commands = build_provision_plan(
                state,
                state_dir=self.store.state_dir if isolated else None,
            ).commands
        for group in default_groups:
            if group in BUILDABLE_GROUPS:
                commands.append(
                    _shared_provider_command(
                        group, record, self.store.state_dir, "build"
                    )
                )
            commands.append(
                _shared_provider_command(group, record, self.store.state_dir, "up")
            )
        # Re-evaluate every source-backed private image sequentially. BuildKit
        # keeps unchanged builds fast; sequencing avoids the large transient
        # memory spike from Compose building all images while starting the graph.
        # Backend precedes simulation because the latter inherits its exact tag.
        for group in (*private, "frontend"):
            if group in BUILDABLE_GROUPS:
                commands.append(
                    command_for("build", record, self.store.state_dir, service=group)
                )
        # Atomic route-file creation is not consistently reported through
        # macOS VM bind mounts. Restart only the stateless proxy so every new
        # slot route is loaded without recreating shared databases or queues.
        commands.append(_traefik_restart_command(record, self.store.state_dir))
        commands.append(command_for("up", record, self.store.state_dir))
        return LifecyclePlan("up", slot, record, tuple(commands), state_commands)

    def _validate_shared_candidate(
        self, group: str, base_ref: str, worktree: Path | None = None
    ) -> None:
        paths = {
            "backend": ("futureagi", "Dockerfile", "docker-compose.local.yml"),
            "simulation": ("futureagi/simulate", "Dockerfile.simulation-runner.dev"),
            "gateway": ("agentcc-gateway",),
            "collector": ("fi-collector",),
            "serving": ("futureagi/model_serving",),
            "executor": ("futureagi/code-executor",),
            "peerdb": (
                "futureagi/config/peerdb",
                "futureagi/docker-compose.peerdb.yml",
                "futureagi/scripts/peerdb-setup-mirrors.sh",
            ),
            "observability": (
                "futureagi/tracer",
                "futureagi/docker-compose.observability.yml",
            ),
        }
        candidate = worktree or self.worktree
        changed = self.git_runner(
            ("git", "diff", "--name-only", base_ref, "--", *paths[group]), candidate
        )
        untracked = self.git_runner(
            ("git", "ls-files", "--others", "--exclude-standard", "--", *paths[group]),
            candidate,
        )
        if changed.strip() or untracked.strip():
            raise ValueError(
                f"cannot create shared {group} default: source differs from {base_ref}"
            )

    def _route_path(self, record: SlotRecord) -> Path:
        return self.store.state_dir / "routes" / f"slot-{record.slot:02d}.yaml"

    def _slot_env_path(self, record: SlotRecord) -> Path:
        return self.store.state_dir / "slots" / f"{record.slot:02d}" / "slot.env"

    def _manifest_record(self, slot: int) -> SlotRecord | None:
        path = self.store.state_dir / "slots" / f"{slot:02d}" / "manifest.json"
        if not path.exists():
            return None
        with path.open(encoding="utf-8") as handle:
            return SlotRecord.from_dict(json.load(handle))

    def _recovery_journal_path(self) -> Path:
        return self.store.state_dir / "recovery.json"

    def _write_recovery_journal(self, record: SlotRecord, action: str) -> None:
        """Leave an explicit marker if a process dies after runtime changes.

        The registry cannot be atomically committed with Docker state.  The marker
        is intentionally retained on a registry-save failure so `slots-doctor`
        can tell an operator that reconciliation is required.
        """
        if self._recovery_journal_path().exists():
            raise RuntimeError(
                "slot recovery journal exists; run slots-doctor and reconcile before mutating"
            )
        self._write_private(
            self._recovery_journal_path(),
            json.dumps(
                {"action": action, "slot": record.slot, "record": record.to_dict()},
                sort_keys=True,
            )
            + "\n",
        )

    def _remove_recovery_journal(self) -> None:
        path = self._recovery_journal_path()
        if path.exists():
            path.unlink()

    def _require_clean_recovery(self) -> None:
        path = self._recovery_journal_path()
        if path.exists():
            details = path.read_text(encoding="utf-8").strip()
            raise RuntimeError(
                "slot recovery journal blocks mutation; reconcile runtime state first"
                + (f": {details}" if details else "")
            )

    def _route_contents(self, record: SlotRecord) -> str:
        template = (
            Path(__file__).parent / "traefik" / "routes.template.yaml"
        ).read_text(encoding="utf-8")
        values = self.environment_for(record)
        replacements = {
            "__SLOT_ID__": f"{record.slot:02d}",
            "__SLOT__": str(record.slot),
            "__FRONTEND_NAME__": values["SLOT_FRONTEND_NAME"],
            "__BACKEND_NAME__": values["SLOT_BACKEND_NAME"],
            "__TEMPORAL_UI_NAME__": values["SLOT_TEMPORAL_UI_NAME"],
            "__MINIO_NAME__": (
                values["SLOT_ISOLATED_MINIO_NAME"]
                if "minio" in record.isolated_infra
                else values["SLOTS_MINIO_NAME"]
            ),
            "__RABBITMQ_NAME__": (
                values["SLOT_ISOLATED_RABBITMQ_NAME"]
                if "rabbitmq" in record.isolated_infra
                else values["SLOTS_RABBITMQ_NAME"]
            ),
            "__GATEWAY_NAME__": values["SLOT_GATEWAY_NAME"],
            "__SERVING_NAME__": values["SLOT_SERVING_NAME"],
            "__COLLECTOR_NAME__": values["SLOT_COLLECTOR_NAME"],
            "__EXECUTOR_NAME__": values["SLOT_EXECUTOR_NAME"],
            "__OBSERVABILITY_NAME__": values["SLOT_OBSERVABILITY_NAME"],
            "__PEERDB_UI_NAME__": values["SLOT_PEERDB_UI_NAME"],
        }
        for token, value in replacements.items():
            template = template.replace(token, value)
        return template

    def _stage_artifacts(self, record: SlotRecord) -> dict[Path, bytes | None]:
        directory = self.store.state_dir / "slots" / f"{record.slot:02d}"
        env_path, manifest_path = directory / "slot.env", directory / "manifest.json"
        route_path = self._route_path(record)
        control_dir = self.store.state_dir / "control-plane"
        control_files = {
            control_dir / "traefik.yaml": Path(__file__).parent
            / "traefik"
            / "traefik.yaml",
            control_dir / "clickhouse-storage-policy.xml": Path(__file__).parent.parent
            / "futureagi"
            / ".ci"
            / "clickhouse-storage-policy.xml",
            control_dir / "clickhouse-test-config.xml": Path(__file__).parent.parent
            / "futureagi"
            / ".ci"
            / "clickhouse-test-config.xml",
        }
        snapshots = {
            path: path.read_bytes() if path.exists() else None
            for path in (env_path, manifest_path, route_path, *control_files)
        }
        try:
            for target, source in control_files.items():
                self._write_private(target, source.read_text(encoding="utf-8"))
            self.write_generated_files(record)
            self._write_private(route_path, self._route_contents(record))
        except Exception:
            # A failure after publishing one generated file must not leave a
            # mixed old/new environment behind, even before Compose is reached.
            self._restore_artifacts(snapshots)
            raise
        return snapshots

    def _restore_artifacts(self, snapshots: dict[Path, bytes | None]) -> None:
        for path, contents in snapshots.items():
            if contents is None:
                if path.exists():
                    path.unlink()
            else:
                self._write_private(path, contents.decode())

    def _remove_route(self, record: SlotRecord) -> bool:
        path = self._route_path(record)
        if path.exists():
            path.unlink()
            return True
        return False

    def _reload_routes_for_survivors(
        self,
        registry: Registry,
        executor: CommandExecutor,
        route_removed: bool,
        *,
        force: bool = False,
    ) -> tuple[Command, ...]:
        """Force Traefik to observe a route deletion on VM-backed fileshares."""
        if (not route_removed and not force) or not registry.slots:
            return ()
        owner = registry.slots[sorted(registry.slots, key=int)[0]]
        command = _traefik_restart_command(owner, self.store.state_dir)
        execute_commands((command,), executor)
        return (command,)

    def _record(
        self,
        registry: Registry,
        slot: int,
        requested: tuple[str, ...],
        private: tuple[str, ...],
        services: tuple[str, ...],
        isolated: tuple[str, ...],
        revision: str,
        environment: dict[str, str],
        retired_isolated: tuple[str, ...] = (),
        retained_private_backend_state: bool = False,
    ) -> SlotRecord:
        providers: dict[str, str] = {"frontend": provider_name(slot, "frontend", True)}
        backend_name = provider_name(slot, "backend", "backend" in private)
        backend = registry.providers.get(backend_name)
        state = (
            backend.state
            if backend
            else StateIdentity.for_slot(slot if "backend" in private else 0)
        )
        providers["backend"] = backend_name
        # Every slot gets a complete provider map.  Requested groups are private;
        # omitted groups share one validated default stack, so advertised routes
        # do not point at providers that were never started.
        required = tuple(CATALOG)
        for service in required:
            providers[service] = provider_name(slot, service, service in private)
        ports = private_ports(slot, private, isolated)
        digest = hashlib.sha256(
            json.dumps(environment, sort_keys=True).encode()
        ).hexdigest()
        effective_services = tuple(
            group for group in CATALOG if group in set(services) | set(private)
        )
        estimated_services = ("frontend", *private)
        return SlotRecord(
            slot,
            str(self.worktree),
            requested,
            effective_services,
            providers,
            state,
            routes_for_slot(slot, environment.get("SLOTS_HTTP_PORT", "80")),
            ports,
            ResourceEstimate(
                estimate_resources(private)
                + sum(ISOLATED_MEMORY_MIB[engine] for engine in isolated),
                estimated_services,
            ),
            digest,
            revision,
            isolated,
            environment.get("SLOTS_HTTP_PORT", "80"),
            retired_isolated,
            retained_private_backend_state,
            environment.get("BASE_REF", "origin/dev"),
        )

    def _admit(
        self,
        registry: Registry,
        record: SlotRecord,
        environment: dict[str, str],
        overhead_mib: int = 0,
    ) -> None:
        if environment.get("FORCE") == "1":
            return
        cap = int(environment.get("SLOTS_MEMORY_CAP_MIB", DEFAULT_MEMORY_CAP_MIB))
        existing = sum(
            item.resources.memory_mib
            for item in registry.slots.values()
            if item.slot != record.slot
        )
        allowed = int(cap * ADMISSION_RATIO)
        projected = existing + record.resources.memory_mib + overhead_mib
        if projected > allowed:
            raise ResourceAdmissionError(
                f"slot {record.slot} needs {record.resources.memory_mib} MiB; projected {projected} MiB exceeds {allowed} MiB admission limit"
            )

    def _state_plan_for(self, record: SlotRecord):
        return build_state_plan(
            record.slot,
            provider_slot=(
                record.slot
                if record.providers["backend"]
                == provider_name(record.slot, "backend", True)
                else None
            ),
            isolate_infra=record.isolated_infra,
        )

    def _purge_state_plan(self, record: SlotRecord):
        """Preserve the physical ownership history of every state engine.

        A backend may move an engine from isolated to shared during replacement.
        Its old logical data still belongs only to the retired private volume,
        so purge must not issue a same-named delete against the shared engine.
        """
        isolated = tuple(
            sorted(set(record.isolated_infra) | set(record.retired_isolated_infra))
        )
        return build_state_plan(
            record.slot,
            provider_slot=record.slot,
            isolate_infra=isolated,
        )

    def _handoff_shared_owners(
        self, registry: Registry, departing_worktree: str, executor: CommandExecutor
    ) -> tuple[Registry, tuple[Command, ...]]:
        """Recreate a shared project from a surviving consumer before rebinding it.

        Shared projects deliberately keep a provider-specific environment.  A
        handoff therefore writes that environment for the successor before its
        Compose project is recreated.  If either writing or execution fails,
        restore every previous file and leave the registry unchanged; callers
        retain their recovery journal for the interrupted external topology.
        """
        if not departing_worktree:
            return registry, ()
        providers = dict(registry.providers)
        commands: list[Command] = []
        snapshots: dict[Path, bytes | None] = {}
        try:
            for name, provider in tuple(providers.items()):
                if provider.private or provider.worktree != departing_worktree:
                    continue
                active_consumers = tuple(
                    registry.slots[str(slot)]
                    for slot in provider.references
                    if str(slot) in registry.slots
                )
                # A same-worktree replacement is not an owner departure. Keep
                # the stable provider instead of needlessly recreating it.
                if any(
                    candidate.worktree == provider.worktree
                    for candidate in active_consumers
                ):
                    continue
                consumers = active_consumers
                if not consumers:
                    continue
                owner = None
                for candidate in sorted(consumers, key=lambda item: item.slot):
                    try:
                        self._validate_shared_candidate(
                            provider.group,
                            provider.base_ref,
                            Path(candidate.worktree),
                        )
                    except ValueError:
                        continue
                    owner = candidate
                    break
                if owner is None:
                    raise ValueError(
                        f"cannot hand off shared {provider.group}: no surviving "
                        f"consumer matches {provider.base_ref}; keep the owner "
                        "worktree or make that provider private before retrying"
                    )
                env_path = self._shared_env_path(provider.group)
                snapshots.setdefault(
                    env_path, env_path.read_bytes() if env_path.exists() else None
                )
                self._write_shared_environment(
                    provider.group, owner, Path(owner.worktree)
                )
                commands.append(
                    _shared_provider_command(
                        provider.group,
                        owner,
                        self.store.state_dir,
                        "up",
                        Path(owner.worktree),
                        force_recreate=True,
                    )
                )
                providers[name] = ProviderRecord(
                    provider.name,
                    provider.group,
                    owner.worktree,
                    provider.state,
                    provider.references,
                    False,
                    provider.base_ref,
                )
            execute_commands(commands, executor)
        except Exception:
            self._restore_artifacts(snapshots)
            raise
        return Registry(registry.version, dict(registry.slots), providers), tuple(
            commands
        )

    @staticmethod
    def _retired_private_volume_commands(record: SlotRecord) -> tuple[Command, ...]:
        """Remove the complete, allowlisted set of volumes owned by one slot.

        Purge is explicitly confirmed for the slot, so trying every exact
        slot-scoped name is safer than relying on the current topology to
        remember providers that may have been retired several replacements
        ago. Missing volumes are an idempotent success; shared names can never
        be produced by this planner.
        """
        prefix = f"futureagi-slot-{record.slot:02d}"
        names = (
            f"{prefix}-frontend-node-modules",
            f"{prefix}-backend-media",
            f"{prefix}-collector-data",
            f"{prefix}-peerdb-catalog-data",
            f"{prefix}-peerdb-minio-data",
        )
        return tuple(
            Command(
                ("docker", "volume", "rm", name),
                Path(record.worktree),
                ("no such volume",),
            )
            for name in names
        )

    def _attach(self, registry: Registry, record: SlotRecord) -> Registry:
        slots = dict(registry.slots)
        providers = dict(registry.providers)
        previous = slots.get(str(record.slot))
        if previous:
            detached, _ = self._detach(
                Registry(registry.version, slots, providers), previous
            )
            slots, providers = dict(detached.slots), dict(detached.providers)
        for service, name in record.providers.items():
            existing = providers.get(name)
            references = tuple(
                sorted(set((existing.references if existing else ()) + (record.slot,)))
            )
            is_private = name.startswith(f"slot-{record.slot:02d}-")
            state = record.state if is_private else StateIdentity.for_slot(0)
            # Shared project/env are created by the first reference and remain
            # rooted in that owner worktree until its final reference is gone.
            owner_worktree = (
                record.worktree if is_private or existing is None else existing.worktree
            )
            providers[name] = ProviderRecord(
                name,
                service,
                owner_worktree,
                state,
                references,
                is_private,
                (existing.base_ref if existing is not None else record.shared_base_ref),
            )
        slots[str(record.slot)] = record
        return Registry(registry.version, slots, providers)

    def _detach(
        self, registry: Registry, record: SlotRecord
    ) -> tuple[Registry, tuple[str, ...]]:
        slots = dict(registry.slots)
        providers = dict(registry.providers)
        stopped: list[str] = []
        for name in set(record.providers.values()):
            provider = providers.get(name)
            if provider is None:
                continue
            references = tuple(
                reference
                for reference in provider.references
                if reference != record.slot
            )
            if references:
                providers[name] = ProviderRecord(
                    provider.name,
                    provider.group,
                    provider.worktree,
                    provider.state,
                    references,
                    provider.private,
                    provider.base_ref,
                )
            else:
                del providers[name]
                stopped.append(name)
        slots.pop(str(record.slot), None)
        return Registry(registry.version, slots, providers), tuple(sorted(stopped))

    @staticmethod
    def _write_private(path: Path, contents: str) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
