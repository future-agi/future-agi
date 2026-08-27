"""Offline provisioning and exact-target purge command planners.

The plans in this module are inert. They only run when an integration passes an
executor, and every command names both its Compose project/file and cwd.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
from typing import Final

from .state import (
    INFRA_ENGINES,
    StatePlan,
    StateValidationError,
    build_state_plan,
    project_name,
    validate_isolated_infra,
    validate_slot,
    volume_name,
)

REPOSITORY_ROOT: Final = Path(__file__).resolve().parent.parent
CONTROL_PLANE_PROJECT: Final = "futureagi-slots"
CONTROL_PLANE_COMPOSE_FILE: Final = "slots/compose/control-plane.yaml"
ISOLATED_COMPOSE_FILE: Final = "slots/compose/isolated-infra.yaml"
_TARGET_IDENTIFIER: Final = re.compile(r"[a-z0-9][a-z0-9_-]{0,127}\Z")
_PROJECT_TARGET: Final = re.compile(
    r"futureagi-slot-(\d{2})-(postgres|clickhouse|redis|rabbitmq|minio|temporal)\Z"
)
_VOLUME_TARGET: Final = re.compile(
    r"futureagi_slot_(\d{2})_(postgres|clickhouse|redis|rabbitmq|minio|temporal)_data\Z"
)
_REDIS_TARGET: Final = re.compile(r"redis-db-(\d{1,2})\Z")
_DATABASE_TARGET: Final = re.compile(r"futureagi_slot_(?:0\d|1\d|20)\Z")
_SERVICE_TARGET: Final = re.compile(r"futureagi-slot-(?:0\d|1\d|20)\Z")


class PurgeConfirmationError(StateValidationError):
    """Raised when a destructive plan is not confirmed for its exact owner."""


class StateAlreadyExistsError(RuntimeError):
    """Executor signal that an idempotent create found an existing resource."""


class StateCommandExecutionError(RuntimeError):
    """A production command failed without an approved idempotency translation."""


def _shared_prefix(engine: str) -> tuple[str, ...]:
    return (
        "docker",
        "compose",
        "--project-name",
        CONTROL_PLANE_PROJECT,
        "-f",
        CONTROL_PLANE_COMPOSE_FILE,
        "exec",
        "-T",
        engine,
    )


def _minio_script(action: str) -> str:
    alias = (
        'mc alias set local http://127.0.0.1:9000 "$MINIO_ROOT_USER" '
        '"$MINIO_ROOT_PASSWORD" >/dev/null\n'
    )
    if action == "ensure":
        return alias + 'exec mc mb --ignore-existing "local/$1"\n'
    if action == "delete":
        return alias + 'exec mc rm --recursive --force "local/$1"\n'
    raise StateValidationError("unsupported MinIO state action")


def _postgres_exec(*args: str) -> tuple[str, ...]:
    return (
        *_shared_prefix("postgres"),
        "sh",
        "-ec",
        'exec psql --username="$POSTGRES_USER" "$@"\n',
        "slots-postgres-psql",
        *args,
    )


def _isolated_prefix(project: str, engine: str) -> tuple[str, ...]:
    service = f"isolated-{engine}"
    return (
        "docker",
        "compose",
        "--project-name",
        project,
        "-f",
        ISOLATED_COMPOSE_FILE,
        "--profile",
        service,
    )


def _project_engine(target: str) -> str:
    match = _PROJECT_TARGET.fullmatch(target)
    if match is None:
        raise StateValidationError("isolated project target is invalid")
    return match.group(2)


def _project_slot(target: str) -> int:
    match = _PROJECT_TARGET.fullmatch(target)
    if match is None:
        raise StateValidationError("isolated project target is invalid")
    return int(match.group(1))


def _volume_engine(target: str) -> str:
    match = _VOLUME_TARGET.fullmatch(target)
    if match is None:
        raise StateValidationError("isolated volume target is invalid")
    return match.group(2)


def _volume_slot(target: str) -> int:
    match = _VOLUME_TARGET.fullmatch(target)
    if match is None:
        raise StateValidationError("isolated volume target is invalid")
    return int(match.group(1))


def generated_slot_env_file(state_dir: Path, slot: int) -> Path:
    """Return the absolute generated environment file for an isolated provider."""
    validate_slot(slot)
    return state_dir.resolve() / "slots" / f"{slot:02d}" / "slot.env"


def _isolated_env_file(operation: str, target: str, env_file: Path | None) -> Path:
    if env_file is None:
        raise StateValidationError(
            "isolated state commands require a generated slot env file"
        )
    if not isinstance(env_file, Path):
        raise StateValidationError("isolated state command env file must be a Path")
    slot = (
        _project_slot(target)
        if operation
        in {
            "start_isolated_engine",
            "stop_isolated_engine",
            "suspend_isolated_engine",
            "run_isolated_minio_init",
            "run_isolated_temporal_init",
        }
        else _volume_slot(target)
    )
    if (
        not env_file.is_absolute()
        or env_file.name != "slot.env"
        or env_file.parent.name != f"{slot:02d}"
        or env_file.parent.parent.name != "slots"
    ):
        raise StateValidationError(
            "isolated state command env file must be slots/<slot>/slot.env"
        )
    return env_file


def _logical_target(target: str, *, database: bool) -> None:
    pattern = _DATABASE_TARGET if database else _SERVICE_TARGET
    if pattern.fullmatch(target) is None:
        raise StateValidationError("logical state target is invalid")


def _postgres_ensure_script(database: str) -> str:
    return (
        "SELECT format('CREATE DATABASE %I', :'slot_database')\n"
        "WHERE NOT EXISTS (\n"
        "  SELECT FROM pg_database WHERE datname = :'slot_database'\n"
        ")\n"
        "\\gexec\n"
    )


def _expected_argv(
    operation: str, target: str, env_file: Path | None = None
) -> tuple[str, ...]:
    """Return the complete approved argv schema for one operation and target."""
    if operation == "create_isolated_volume":
        _volume_engine(target)
        _isolated_env_file(operation, target, env_file)
        return ("docker", "volume", "create", target)
    if operation == "start_isolated_engine":
        engine = _project_engine(target)
        services = (
            ("isolated-temporal", "isolated-temporal-ui")
            if engine == "temporal"
            else (f"isolated-{engine}",)
        )
        return (
            *_isolated_prefix(target, engine),
            "--env-file",
            str(_isolated_env_file(operation, target, env_file)),
            "up",
            "--detach",
            "--wait",
            *services,
        )
    if operation == "run_isolated_minio_init":
        if _project_engine(target) != "minio":
            raise StateValidationError("MinIO initializer target is invalid")
        return (
            *_isolated_prefix(target, "minio"),
            "--env-file",
            str(_isolated_env_file(operation, target, env_file)),
            "run",
            "--rm",
            "isolated-minio-init",
        )
    if operation == "run_isolated_temporal_init":
        if _project_engine(target) != "temporal":
            raise StateValidationError("Temporal initializer target is invalid")
        return (
            *_isolated_prefix(target, "temporal"),
            "--env-file",
            str(_isolated_env_file(operation, target, env_file)),
            "run",
            "--rm",
            "isolated-temporal-init",
        )
    if operation in {"stop_isolated_engine", "suspend_isolated_engine"}:
        engine = _project_engine(target)
        return (
            *_isolated_prefix(target, engine),
            "--env-file",
            str(_isolated_env_file(operation, target, env_file)),
            "down",
            "--remove-orphans",
        )
    if operation == "delete_isolated_volume":
        _volume_engine(target)
        _isolated_env_file(operation, target, env_file)
        return ("docker", "volume", "rm", target)

    if env_file is not None:
        raise StateValidationError(
            "logical state commands cannot receive an isolated env file"
        )

    if operation == "ensure_postgres_database":
        _logical_target(target, database=True)
        return _postgres_exec(
            "--dbname=postgres",
            "--set=ON_ERROR_STOP=1",
            f"--set=slot_database={target}",
            "--file=-",
        )
    if operation == "ensure_clickhouse_database":
        _logical_target(target, database=True)
        return (
            *_shared_prefix("clickhouse"),
            "clickhouse-client",
            "--query",
            f"CREATE DATABASE IF NOT EXISTS {target}",
        )
    if operation == "ensure_rabbitmq_vhost":
        _logical_target(target, database=False)
        return (*_shared_prefix("rabbitmq"), "rabbitmqctl", "add_vhost", target)
    if operation == "ensure_minio_bucket":
        _logical_target(target, database=False)
        return (
            *_shared_prefix("minio"),
            "sh",
            "-ec",
            _minio_script("ensure"),
            "slots-minio-bucket",
            target,
        )
    if operation == "ensure_temporal_namespace":
        _logical_target(target, database=False)
        return (
            *_shared_prefix("temporal"),
            "temporal",
            "operator",
            "namespace",
            "create",
            "--namespace",
            target,
            "--address",
            "temporal:7233",
        )
    if operation == "drop_postgres_database":
        _logical_target(target, database=True)
        return _postgres_exec(
            "--dbname=postgres",
            "--command",
            f'DROP DATABASE IF EXISTS "{target}"',
        )
    if operation == "drop_clickhouse_database":
        _logical_target(target, database=True)
        return (
            *_shared_prefix("clickhouse"),
            "clickhouse-client",
            "--query",
            f"DROP DATABASE IF EXISTS {target}",
        )
    if operation == "flush_redis_database":
        match = _REDIS_TARGET.fullmatch(target)
        if match is None:
            raise StateValidationError("Redis purge target is invalid")
        return (
            *_shared_prefix("redis"),
            "redis-cli",
            "-n",
            match.group(1),
            "FLUSHDB",
        )
    if operation == "delete_rabbitmq_vhost":
        _logical_target(target, database=False)
        return (*_shared_prefix("rabbitmq"), "rabbitmqctl", "delete_vhost", target)
    if operation == "delete_minio_bucket":
        _logical_target(target, database=False)
        return (
            *_shared_prefix("minio"),
            "sh",
            "-ec",
            _minio_script("delete"),
            "slots-minio-bucket",
            target,
        )
    if operation == "delete_temporal_namespace":
        _logical_target(target, database=False)
        return (
            *_shared_prefix("temporal"),
            "temporal",
            "operator",
            "namespace",
            "delete",
            "--namespace",
            target,
            "--address",
            "temporal:7233",
        )
    raise StateValidationError(f"unsupported state command operation: {operation!r}")


def _expected_stdin(operation: str, target: str) -> str | None:
    if operation == "ensure_postgres_database":
        _logical_target(target, database=True)
        return _postgres_ensure_script(target)
    return None


def _expected_already_exists_markers(operation: str) -> tuple[str, ...]:
    if operation in {"ensure_rabbitmq_vhost", "ensure_temporal_namespace"}:
        return ("already exists",)
    if operation == "delete_isolated_volume":
        return ("no such volume",)
    if operation in {
        "delete_rabbitmq_vhost",
        "delete_minio_bucket",
        "delete_temporal_namespace",
    }:
        return ("does not exist", "not found")
    return ()


def _matches_idempotency_signal(command: StateCommand, message: str) -> bool:
    normalized = message.casefold()
    if not any(marker in normalized for marker in command.already_exists_markers):
        return False
    # Missing-target failures are safe only when they name the exact reviewed
    # destructive target. Never hide an unrelated missing service/container.
    return not command.destructive or command.target.casefold() in normalized


@dataclass(frozen=True, slots=True)
class StateCommand:
    """One exact, shell-free infrastructure command."""

    operation: str
    target: str
    argv: tuple[str, ...]
    destructive: bool = False
    cwd: Path = REPOSITORY_ROOT
    idempotent: bool = True
    already_exists_markers: tuple[str, ...] = ()
    stdin: str | None = None
    env_file: Path | None = None

    def __post_init__(self) -> None:
        if _TARGET_IDENTIFIER.fullmatch(self.target) is None:
            raise StateValidationError(
                "state command target must be a strict identifier"
            )
        if self.cwd != REPOSITORY_ROOT:
            raise StateValidationError(
                "state commands must run from the repository root"
            )
        if self.argv != _expected_argv(self.operation, self.target, self.env_file):
            raise StateValidationError(
                "state command argv does not match its exact operation schema"
            )
        if self.stdin != _expected_stdin(self.operation, self.target):
            raise StateValidationError(
                "state command stdin does not match its exact operation schema"
            )
        if not self.idempotent:
            raise StateValidationError("provisioning commands must be idempotent")
        if self.already_exists_markers != _expected_already_exists_markers(
            self.operation
        ):
            raise StateValidationError(
                "state command already-existing markers do not match its operation"
            )


CommandExecutor = Callable[[StateCommand], object]
SubprocessRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class ProvisionPlan:
    state: StatePlan
    commands: tuple[StateCommand, ...]


@dataclass(frozen=True, slots=True)
class SuspendPlan:
    """Non-destructive isolated-engine shutdown commands for slot-down/replacement."""

    state: StatePlan
    commands: tuple[StateCommand, ...]


@dataclass(frozen=True, slots=True)
class PurgePlan:
    state: StatePlan
    commands: tuple[StateCommand, ...]
    confirmation: str

    def __post_init__(self) -> None:
        if not self.commands or not all(
            command.destructive for command in self.commands
        ):
            raise StateValidationError(
                "a purge plan must contain only destructive commands"
            )
        assert_exact_destructive_targets(self.commands)


def _startup_command(
    plan: StatePlan, engine: str, env_file: Path | None
) -> tuple[StateCommand, ...]:
    project = plan.project_for(engine)
    volume = plan.volume_for(engine)
    if project is None or volume is None:
        return ()
    commands = [
        StateCommand(
            "create_isolated_volume",
            volume,
            _expected_argv("create_isolated_volume", volume, env_file),
            env_file=env_file,
        ),
        StateCommand(
            "start_isolated_engine",
            project,
            _expected_argv("start_isolated_engine", project, env_file),
            env_file=env_file,
        ),
    ]
    initializer_by_engine = {
        "minio": "run_isolated_minio_init",
        "temporal": "run_isolated_temporal_init",
    }
    initializer = initializer_by_engine.get(engine)
    if initializer is not None:
        commands.append(
            StateCommand(
                initializer,
                project,
                _expected_argv(initializer, project, env_file),
                env_file=env_file,
            )
        )
    return tuple(commands)


def _provision_command(plan: StatePlan, engine: str) -> StateCommand | None:
    """Plan only shared logical state; isolated volumes are the state boundary."""
    if plan.is_isolated(engine):
        return None
    identity = plan.identity
    target_by_engine = {
        "postgres": identity.postgres_database,
        "clickhouse": identity.clickhouse_database,
        "rabbitmq": identity.rabbitmq_vhost,
        "minio": identity.minio_bucket,
        "temporal": identity.temporal_namespace,
    }
    operation_by_engine = {
        "postgres": "ensure_postgres_database",
        "clickhouse": "ensure_clickhouse_database",
        "rabbitmq": "ensure_rabbitmq_vhost",
        "minio": "ensure_minio_bucket",
        "temporal": "ensure_temporal_namespace",
    }
    if engine == "redis":
        return None
    target = target_by_engine[engine]
    operation = operation_by_engine[engine]
    markers = ("already exists",) if engine in {"rabbitmq", "temporal"} else ()
    return StateCommand(
        operation,
        target,
        _expected_argv(operation, target),
        already_exists_markers=markers,
        stdin=_expected_stdin(operation, target),
    )


def _plan_env_file(state: StatePlan, state_dir: Path | None) -> Path | None:
    """Derive the only acceptable isolated Compose env file from state storage."""
    if state.isolated_infra:
        return _provider_env_file(state, state_dir)
    return None


def _provider_env_file(state: StatePlan, state_dir: Path | None) -> Path:
    provider_slot = state.identity.provider_slot
    if provider_slot is None:
        raise StateValidationError("isolated state commands require a private provider")
    if state_dir is None:
        raise StateValidationError(
            "isolated state plan requires an authoritative state directory"
        )
    if not isinstance(state_dir, Path) or not state_dir.is_absolute():
        raise StateValidationError(
            "isolated state plan state directory must be an absolute Path"
        )
    return generated_slot_env_file(state_dir, provider_slot)


def build_provision_plan(
    state: StatePlan, *, state_dir: Path | None = None
) -> ProvisionPlan:
    """Return idempotent commands needed to provision ``state``; do not run them."""
    env_file = _plan_env_file(state, state_dir)
    commands: list[StateCommand] = []
    for engine in sorted(INFRA_ENGINES):
        commands.extend(_startup_command(state, engine, env_file))
        command = _provision_command(state, engine)
        if command is not None:
            commands.append(command)
    return ProvisionPlan(state=state, commands=tuple(commands))


def build_suspend_plan(
    state: StatePlan, *, state_dir: Path | None = None
) -> SuspendPlan:
    """Stop isolated containers while preserving every logical and volume resource."""
    env_file = _plan_env_file(state, state_dir)
    commands: list[StateCommand] = []
    for engine in sorted(state.isolated_infra):
        project = state.project_for(engine)
        assert project is not None
        commands.append(
            StateCommand(
                "suspend_isolated_engine",
                project,
                _expected_argv("suspend_isolated_engine", project, env_file),
                env_file=env_file,
            )
        )
    return SuspendPlan(state=state, commands=tuple(commands))


def provision_state(
    slot: int,
    *,
    provider_slot: int | None = None,
    isolate_infra: str | Iterable[str] | None = None,
    state_dir: Path | None = None,
    executor: CommandExecutor | None = None,
) -> ProvisionPlan:
    """Build an idempotent provision plan and optionally pass it to an executor."""
    plan = build_provision_plan(
        build_state_plan(
            slot, provider_slot=provider_slot, isolate_infra=isolate_infra
        ),
        state_dir=state_dir,
    )
    if executor is not None:
        apply_provision_plan(plan, executor)
    return plan


def suspend_state(
    slot: int,
    *,
    provider_slot: int | None = None,
    isolate_infra: str | Iterable[str] | None = None,
    state_dir: Path | None = None,
    executor: CommandExecutor | None = None,
) -> SuspendPlan:
    """Build a state-preserving isolated shutdown plan and optionally execute it."""
    plan = build_suspend_plan(
        build_state_plan(
            slot, provider_slot=provider_slot, isolate_infra=isolate_infra
        ),
        state_dir=state_dir,
    )
    if executor is not None:
        apply_suspend_plan(plan, executor)
    return plan


def apply_provision_plan(plan: ProvisionPlan, executor: CommandExecutor) -> None:
    """Execute a previously reviewed plan through the caller's command boundary."""
    for command in plan.commands:
        try:
            executor(command)
        except StateAlreadyExistsError as error:
            message = str(error).casefold()
            if not any(marker in message for marker in command.already_exists_markers):
                raise


def apply_suspend_plan(plan: SuspendPlan, executor: CommandExecutor) -> None:
    """Execute a state-preserving shutdown plan through an injected executor."""
    for command in plan.commands:
        if command.destructive:
            raise StateValidationError(
                "suspend plans cannot include destructive commands"
            )
        executor(command)


def execute_state_command(
    command: StateCommand, runner: SubprocessRunner = subprocess.run
) -> subprocess.CompletedProcess[str]:
    """Production adapter for a reviewed command; injectable for unit tests.

    Only operations which declare an approved idempotency marker can convert a
    non-zero subprocess result into ``StateAlreadyExistsError``. Every other
    command failure remains a hard failure.
    """
    result = runner(
        command.argv,
        cwd=command.cwd,
        input=command.stdin,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        return result
    stderr = result.stderr or ""
    if _matches_idempotency_signal(command, stderr):
        raise StateAlreadyExistsError(stderr)
    raise StateCommandExecutionError(
        f"{command.operation} failed for {command.target} (exit {result.returncode}): {stderr.strip()}"
    )


def _purge_command(plan: StatePlan, engine: str) -> StateCommand | None:
    """Plan shared logical cleanup only; isolated volumes are purged physically."""
    if plan.is_isolated(engine):
        return None
    identity = plan.identity
    if engine == "postgres":
        operation, target = "drop_postgres_database", identity.postgres_database
    elif engine == "clickhouse":
        operation, target = "drop_clickhouse_database", identity.clickhouse_database
    elif engine == "redis":
        return None
    elif engine == "rabbitmq":
        operation, target = "delete_rabbitmq_vhost", identity.rabbitmq_vhost
    elif engine == "minio":
        operation, target = "delete_minio_bucket", identity.minio_bucket
    else:
        operation, target = "delete_temporal_namespace", identity.temporal_namespace
    return StateCommand(
        operation,
        target,
        _expected_argv(operation, target),
        destructive=True,
        already_exists_markers=_expected_already_exists_markers(operation),
    )


def _redis_purge_commands(plan: StatePlan) -> tuple[StateCommand, ...]:
    if plan.is_isolated("redis"):
        return ()
    commands: list[StateCommand] = []
    for database in plan.identity.redis_databases:
        target = f"redis-db-{database}"
        commands.append(
            StateCommand(
                "flush_redis_database",
                target,
                _expected_argv("flush_redis_database", target),
                destructive=True,
            )
        )
    return tuple(commands)


def _isolated_purge_commands(
    plan: StatePlan, engine: str, env_file: Path | None
) -> tuple[StateCommand, ...]:
    project = plan.project_for(engine)
    volume = plan.volume_for(engine)
    if project is None or volume is None:
        return ()
    return (
        StateCommand(
            "stop_isolated_engine",
            project,
            _expected_argv("stop_isolated_engine", project, env_file),
            destructive=True,
            env_file=env_file,
        ),
        StateCommand(
            "delete_isolated_volume",
            volume,
            _expected_argv("delete_isolated_volume", volume, env_file),
            destructive=True,
            already_exists_markers=_expected_already_exists_markers(
                "delete_isolated_volume"
            ),
            env_file=env_file,
        ),
    )


def build_purge_plan(
    state: StatePlan, *, confirm: str | int, state_dir: Path | None = None
) -> PurgePlan:
    """Build an exact destructive plan only for the owning private provider slot."""
    provider_slot = state.identity.provider_slot
    if provider_slot is None or provider_slot != state.slot:
        raise PurgeConfirmationError(
            "only a private provider's owning slot can purge its state"
        )
    if str(confirm) != str(state.slot):
        raise PurgeConfirmationError(
            f"CONFIRM must equal the exact purge slot ({state.slot})"
        )

    env_file = _plan_env_file(state, state_dir)

    commands: list[StateCommand] = []
    for engine in sorted(INFRA_ENGINES):
        commands.extend(_isolated_purge_commands(state, engine, env_file))
        if engine == "redis":
            commands.extend(_redis_purge_commands(state))
        else:
            command = _purge_command(state, engine)
            if command is not None:
                commands.append(command)
    return PurgePlan(state=state, commands=tuple(commands), confirmation=str(confirm))


def build_retired_isolated_purge_plan(
    state: StatePlan,
    *,
    retired_infra: str | Iterable[str],
    confirm: str | int,
    state_dir: Path | None = None,
) -> PurgePlan:
    """Purge only physical state left by engines isolated in a retired topology.

    Current isolated engines are owned by ``build_purge_plan``. Current shared
    engines remain logical-purge targets there as well, so this planner cannot
    accidentally suppress either part of the normal topology cleanup.
    """
    provider_slot = state.identity.provider_slot
    if provider_slot is None or provider_slot != state.slot:
        raise PurgeConfirmationError(
            "only a private provider's owning slot can purge its state"
        )
    if str(confirm) != str(state.slot):
        raise PurgeConfirmationError(
            f"CONFIRM must equal the exact purge slot ({state.slot})"
        )
    retired = validate_isolated_infra(retired_infra)
    if not retired:
        raise StateValidationError(
            "retired isolated purge requires at least one engine"
        )
    overlap = retired & state.isolated_infra
    if overlap:
        raise StateValidationError(
            "retired isolated engines are still isolated in the current topology: "
            + ", ".join(sorted(overlap))
        )

    env_file = _provider_env_file(state, state_dir)
    commands: list[StateCommand] = []
    for engine in sorted(retired):
        project = project_name(provider_slot, engine)
        volume = volume_name(provider_slot, engine)
        commands.extend(
            (
                StateCommand(
                    "stop_isolated_engine",
                    project,
                    _expected_argv("stop_isolated_engine", project, env_file),
                    destructive=True,
                    env_file=env_file,
                ),
                StateCommand(
                    "delete_isolated_volume",
                    volume,
                    _expected_argv("delete_isolated_volume", volume, env_file),
                    destructive=True,
                    already_exists_markers=_expected_already_exists_markers(
                        "delete_isolated_volume"
                    ),
                    env_file=env_file,
                ),
            )
        )
    return PurgePlan(state=state, commands=tuple(commands), confirmation=str(confirm))


def purge_state(
    slot: int,
    *,
    provider_slot: int | None = None,
    isolate_infra: str | Iterable[str] | None = None,
    state_dir: Path | None = None,
    confirm: str | int | None = None,
    executor: CommandExecutor | None = None,
) -> PurgePlan:
    """Build a purge plan and execute only through an injected executor."""
    state = build_state_plan(
        slot, provider_slot=provider_slot, isolate_infra=isolate_infra
    )
    plan = build_purge_plan(
        state, confirm="" if confirm is None else confirm, state_dir=state_dir
    )
    if executor is not None:
        apply_purge_plan(plan, executor)
    return plan


def apply_purge_plan(plan: PurgePlan, executor: CommandExecutor) -> None:
    """Execute an exact-target purge plan through the caller's command boundary."""
    assert_exact_destructive_targets(plan.commands)
    for command in plan.commands:
        try:
            executor(command)
        except StateAlreadyExistsError as error:
            if not _matches_idempotency_signal(command, str(error)):
                raise


def assert_exact_destructive_targets(commands: Iterable[StateCommand]) -> None:
    """Reject any destructive command that does not match its exact schema."""
    for command in commands:
        if not command.destructive:
            raise StateValidationError(
                "purge plans cannot include non-destructive commands"
            )
        if command.argv != _expected_argv(
            command.operation, command.target, command.env_file
        ):
            raise StateValidationError("purge plan contains an inexact command")
