from __future__ import annotations

from pathlib import Path

import pytest

from slots.provisioning import (
    CONTROL_PLANE_COMPOSE_FILE,
    CONTROL_PLANE_PROJECT,
    REPOSITORY_ROOT,
    PurgeConfirmationError,
    StateAlreadyExistsError,
    StateCommand,
    StateCommandExecutionError,
    apply_purge_plan,
    apply_suspend_plan,
    apply_provision_plan,
    build_provision_plan,
    build_purge_plan,
    build_retired_isolated_purge_plan,
    build_suspend_plan,
    execute_state_command,
    generated_slot_env_file,
    provision_state,
    purge_state,
    suspend_state,
)
from slots.state import INFRA_ENGINES, StateValidationError, build_state_plan


def _generated_env(tmp_path: Path, slot: int = 3) -> Path:
    return generated_slot_env_file(_state_dir(tmp_path), slot)


def _state_dir(tmp_path: Path) -> Path:
    return (tmp_path / ".slots").resolve()


def test_provision_plan_is_inert_until_an_executor_is_injected() -> None:
    plan = provision_state(3, provider_slot=3)

    assert any(
        command.operation == "ensure_postgres_database" for command in plan.commands
    )
    executed: list[StateCommand] = []
    apply_provision_plan(plan, executed.append)
    assert executed == list(plan.commands)


def test_shared_commands_explicitly_name_control_plane_compose_project_and_cwd() -> (
    None
):
    plan = provision_state(3, provider_slot=3)

    assert all(command.cwd == REPOSITORY_ROOT for command in plan.commands)
    assert all(
        command.argv[:6]
        == (
            "docker",
            "compose",
            "--project-name",
            CONTROL_PLANE_PROJECT,
            "-f",
            CONTROL_PLANE_COMPOSE_FILE,
        )
        for command in plan.commands
    )


def test_provision_plan_is_idempotent_for_repeated_preserved_state_starts(
    tmp_path: Path,
) -> None:
    state = build_state_plan(3, provider_slot=3, isolate_infra="minio")
    state_dir = _state_dir(tmp_path)

    first = build_provision_plan(state, state_dir=state_dir)
    second = build_provision_plan(state, state_dir=state_dir)

    assert first == second
    assert all(command.idempotent for command in first.commands)
    volume = next(
        command
        for command in first.commands
        if command.operation == "create_isolated_volume"
    )
    assert volume.argv == ("docker", "volume", "create", volume.target)
    assert any(
        command.stdin is not None and "\\gexec" in command.stdin
        for command in first.commands
        if command.operation == "ensure_postgres_database"
    )


def test_minio_state_commands_authenticate_alias_from_container_environment() -> None:
    provision = next(
        command
        for command in provision_state(3, provider_slot=3).commands
        if command.operation == "ensure_minio_bucket"
    )
    purge = next(
        command
        for command in purge_state(3, provider_slot=3, confirm="3").commands
        if command.operation == "delete_minio_bucket"
    )

    for command in (provision, purge):
        script = command.argv[-3]
        assert "mc alias set local http://127.0.0.1:9000" in script
        assert '"$MINIO_ROOT_USER"' in script
        assert '"$MINIO_ROOT_PASSWORD"' in script
        assert command.argv[-1] == "futureagi-slot-03"


def test_temporal_state_commands_use_control_plane_service_address() -> None:
    provision = next(
        command
        for command in provision_state(3, provider_slot=3).commands
        if command.operation == "ensure_temporal_namespace"
    )
    purge = next(
        command
        for command in purge_state(3, provider_slot=3, confirm="3").commands
        if command.operation == "delete_temporal_namespace"
    )

    for command in (provision, purge):
        address = command.argv.index("--address")
        assert command.argv[address + 1] == "temporal:7233"


def test_provision_executor_accepts_only_expected_existing_state_signals() -> None:
    plan = provision_state(3, provider_slot=3)
    executed: list[StateCommand] = []

    def executor(command: StateCommand) -> None:
        executed.append(command)
        if command.operation == "ensure_temporal_namespace":
            raise StateAlreadyExistsError("namespace already exists")

    apply_provision_plan(plan, executor)

    assert executed == list(plan.commands)


def test_production_executor_passes_safe_postgres_stdin_to_mocked_subprocess() -> None:
    command = next(
        command
        for command in provision_state(3, provider_slot=3).commands
        if command.operation == "ensure_postgres_database"
    )
    calls: list[tuple[tuple[str, ...], object, str | None]] = []

    def runner(argv: tuple[str, ...], **kwargs: object) -> object:
        calls.append((argv, kwargs["cwd"], kwargs["input"]))
        return type("Result", (), {"returncode": 0, "stderr": ""})()

    execute_state_command(command, runner)  # type: ignore[arg-type]

    assert calls == [(command.argv, REPOSITORY_ROOT, command.stdin)]
    assert "--command" not in command.argv
    assert command.argv[-1] == "--file=-"
    assert any(
        'exec psql --username="$POSTGRES_USER" "$@"' in argument
        for argument in command.argv
    )
    assert command.stdin is not None
    assert "slot_database" in command.stdin


def test_production_executor_translates_only_approved_existing_state_failures() -> None:
    rabbitmq = next(
        command
        for command in provision_state(3, provider_slot=3).commands
        if command.operation == "ensure_rabbitmq_vhost"
    )
    postgres = next(
        command
        for command in provision_state(3, provider_slot=3).commands
        if command.operation == "ensure_postgres_database"
    )

    def exists_runner(*args: object, **kwargs: object) -> object:
        return type("Result", (), {"returncode": 1, "stderr": "vhost already exists"})()

    with pytest.raises(StateAlreadyExistsError):
        execute_state_command(rabbitmq, exists_runner)  # type: ignore[arg-type]
    with pytest.raises(StateCommandExecutionError):
        execute_state_command(postgres, exists_runner)  # type: ignore[arg-type]


def test_purge_retry_accepts_only_expected_missing_state_signals() -> None:
    plan = purge_state(3, provider_slot=3, confirm="3")
    executed: list[StateCommand] = []

    def executor(command: StateCommand) -> None:
        executed.append(command)
        if command.operation in {
            "delete_rabbitmq_vhost",
            "delete_minio_bucket",
            "delete_temporal_namespace",
        }:
            raise StateAlreadyExistsError(f"{command.target} does not exist")

    apply_purge_plan(plan, executor)

    assert executed == list(plan.commands)


def test_production_executor_translates_approved_missing_volume_failure() -> None:
    command = next(
        command
        for command in purge_state(
            3,
            provider_slot=3,
            isolate_infra="postgres",
            state_dir=Path("/tmp/.slots"),
            confirm="3",
        ).commands
        if command.operation == "delete_isolated_volume"
    )

    def missing_runner(*args: object, **kwargs: object) -> object:
        return type(
            "Result",
            (),
            {"returncode": 1, "stderr": f"No such volume: {command.target}"},
        )()

    with pytest.raises(StateAlreadyExistsError):
        execute_state_command(command, missing_runner)  # type: ignore[arg-type]


def test_purge_retry_does_not_hide_unrelated_missing_runtime_errors() -> None:
    plan = purge_state(3, provider_slot=3, confirm="3")

    def executor(command: StateCommand) -> None:
        if command.operation == "delete_rabbitmq_vhost":
            raise StateAlreadyExistsError("control-plane container not found")

    with pytest.raises(StateAlreadyExistsError, match="control-plane"):
        apply_purge_plan(plan, executor)


def test_isolated_engine_plan_uses_only_deterministic_project_and_volume(
    tmp_path: Path,
) -> None:
    state = build_state_plan(3, provider_slot=3, isolate_infra="postgres,redis")
    state_dir = _state_dir(tmp_path)
    env_file = _generated_env(tmp_path)
    commands = build_provision_plan(state, state_dir=state_dir).commands

    assert ("docker", "volume", "create", "futureagi_slot_03_postgres_data") in [
        c.argv for c in commands
    ]
    assert ("docker", "volume", "create", "futureagi_slot_03_redis_data") in [
        c.argv for c in commands
    ]
    starts = [
        command for command in commands if command.operation == "start_isolated_engine"
    ]
    assert {command.target for command in starts} == {
        "futureagi-slot-03-postgres",
        "futureagi-slot-03-redis",
    }
    assert all(
        any(argument.startswith("isolated-") for argument in command.argv)
        for command in starts
    )
    assert all(command.env_file == env_file for command in starts)
    assert all("--env-file" in command.argv for command in starts)
    assert all(str(env_file) in command.argv for command in starts)


def test_every_isolated_engine_omits_logical_provisioning_and_cleanup(
    tmp_path: Path,
) -> None:
    isolated = ",".join(sorted(INFRA_ENGINES))
    state = build_state_plan(3, provider_slot=3, isolate_infra=isolated)

    state_dir = _state_dir(tmp_path)
    provision = build_provision_plan(state, state_dir=state_dir)
    purge = build_purge_plan(state, confirm="3", state_dir=state_dir)

    assert {command.operation for command in provision.commands} == {
        "create_isolated_volume",
        "start_isolated_engine",
        "run_isolated_minio_init",
        "run_isolated_temporal_init",
    }
    assert {command.operation for command in purge.commands} == {
        "stop_isolated_engine",
        "delete_isolated_volume",
    }
    assert len(provision.commands) == len(INFRA_ENGINES) * 2 + 2
    assert len(purge.commands) == len(INFRA_ENGINES) * 2


def test_suspend_plan_stops_every_isolated_engine_without_deleting_state(
    tmp_path: Path,
) -> None:
    state = build_state_plan(
        3,
        provider_slot=3,
        isolate_infra=",".join(sorted(INFRA_ENGINES)),
    )

    plan = build_suspend_plan(state, state_dir=_state_dir(tmp_path))

    assert [command.operation for command in plan.commands] == [
        "suspend_isolated_engine"
    ] * len(INFRA_ENGINES)
    assert all(not command.destructive for command in plan.commands)
    assert all(
        command.env_file == _generated_env(tmp_path) for command in plan.commands
    )
    assert all(
        command.argv[-2:] == ("down", "--remove-orphans") for command in plan.commands
    )
    assert not any("volume" in command.operation for command in plan.commands)
    assert not any("delete" in command.operation for command in plan.commands)


def test_suspend_state_is_injected_and_preserves_the_purge_boundary(
    tmp_path: Path,
) -> None:
    executed: list[StateCommand] = []

    plan = suspend_state(
        3,
        provider_slot=3,
        isolate_infra="postgres",
        state_dir=_state_dir(tmp_path),
        executor=executed.append,
    )

    assert executed == list(plan.commands)
    assert all(not command.destructive for command in plan.commands)
    assert all(
        command.operation != "delete_isolated_volume" for command in plan.commands
    )


def test_suspend_executor_rejects_a_destructive_command(tmp_path: Path) -> None:
    state = build_state_plan(3, provider_slot=3, isolate_infra="postgres")
    purge = build_purge_plan(state, confirm="3", state_dir=_state_dir(tmp_path))

    with pytest.raises(StateValidationError, match="cannot include destructive"):
        apply_suspend_plan(purge, lambda command: None)  # type: ignore[arg-type]


def test_retired_isolated_purge_targets_only_retired_engine_projects_and_volumes(
    tmp_path: Path,
) -> None:
    state = build_state_plan(3, provider_slot=3, isolate_infra="postgres")

    retired = build_retired_isolated_purge_plan(
        state,
        retired_infra="minio,redis",
        confirm="3",
        state_dir=_state_dir(tmp_path),
    )
    current = build_purge_plan(state, confirm="3", state_dir=_state_dir(tmp_path))

    assert [command.operation for command in retired.commands] == [
        "stop_isolated_engine",
        "delete_isolated_volume",
        "stop_isolated_engine",
        "delete_isolated_volume",
    ]
    assert {command.target for command in retired.commands} == {
        "futureagi-slot-03-minio",
        "futureagi_slot_03_minio_data",
        "futureagi-slot-03-redis",
        "futureagi_slot_03_redis_data",
    }
    assert all(command.destructive for command in retired.commands)
    assert not any("flush_redis" in command.operation for command in retired.commands)
    assert not any(
        "delete_minio_bucket" in command.operation for command in retired.commands
    )
    assert any(
        command.operation == "flush_redis_database" for command in current.commands
    )
    assert any(
        command.operation == "delete_minio_bucket" for command in current.commands
    )


def test_retired_isolated_purge_requires_exact_confirmation_and_valid_retired_subset(
    tmp_path: Path,
) -> None:
    state = build_state_plan(3, provider_slot=3, isolate_infra="postgres")

    with pytest.raises(PurgeConfirmationError):
        build_retired_isolated_purge_plan(
            state,
            retired_infra="redis",
            confirm="2",
            state_dir=_state_dir(tmp_path),
        )
    with pytest.raises(StateValidationError, match="still isolated"):
        build_retired_isolated_purge_plan(
            state,
            retired_infra="postgres",
            confirm="3",
            state_dir=_state_dir(tmp_path),
        )
    with pytest.raises(StateValidationError, match="at least one"):
        build_retired_isolated_purge_plan(
            state,
            retired_infra=None,  # type: ignore[arg-type]
            confirm="3",
            state_dir=_state_dir(tmp_path),
        )
    with pytest.raises(StateValidationError, match="authoritative state directory"):
        build_retired_isolated_purge_plan(state, retired_infra="redis", confirm="3")


def test_purge_requires_confirmation_for_the_exact_private_provider_slot() -> None:
    state = build_state_plan(3, provider_slot=3)

    with pytest.raises(PurgeConfirmationError):
        build_purge_plan(state, confirm="2")
    with pytest.raises(PurgeConfirmationError):
        build_purge_plan(build_state_plan(3), confirm="3")
    with pytest.raises(PurgeConfirmationError):
        build_purge_plan(build_state_plan(3, provider_slot=4), confirm="3")


def test_purge_commands_are_precise_and_never_target_all_state(tmp_path: Path) -> None:
    state_dir = _state_dir(tmp_path)
    plan = purge_state(
        20,
        provider_slot=20,
        isolate_infra="postgres",
        confirm=20,
        state_dir=state_dir,
    )

    assert all(command.destructive for command in plan.commands)
    assert all(command.target not in {"", "*", "all", "/"} for command in plan.commands)
    assert all(
        "--all" not in command.argv and "--volumes" not in command.argv
        for command in plan.commands
    )
    redis = [
        command
        for command in plan.commands
        if command.operation == "flush_redis_database"
    ]
    assert [command.argv[-2:] for command in redis] == [
        ("60", "FLUSHDB"),
        ("61", "FLUSHDB"),
        ("62", "FLUSHDB"),
    ]
    assert not any("FLUSHALL" in command.argv for command in plan.commands)


def test_purge_execution_is_injected_and_mockable() -> None:
    executed: list[StateCommand] = []

    plan = purge_state(3, provider_slot=3, confirm="3", executor=executed.append)

    assert executed == list(plan.commands)


def test_isolated_plans_require_an_authoritative_absolute_state_directory(
    tmp_path: Path,
) -> None:
    state = build_state_plan(3, provider_slot=3, isolate_infra="postgres")

    with pytest.raises(StateValidationError, match="authoritative state directory"):
        build_provision_plan(state)
    with pytest.raises(StateValidationError, match="authoritative state directory"):
        build_purge_plan(state, confirm="3")
    with pytest.raises(StateValidationError, match="absolute Path"):
        build_provision_plan(state, state_dir=Path(".slots"))
    with pytest.raises(TypeError):
        build_provision_plan(state, env_file=_generated_env(tmp_path, 4))  # type: ignore[call-arg]


def test_shared_logical_commands_do_not_receive_a_generated_env_file(
    tmp_path: Path,
) -> None:
    state = build_state_plan(3, provider_slot=3)

    plan = build_provision_plan(state, state_dir=_state_dir(tmp_path))

    assert all(command.env_file is None for command in plan.commands)


def test_isolated_temporal_start_includes_init_and_ui_dependents(
    tmp_path: Path,
) -> None:
    state = build_state_plan(3, provider_slot=3, isolate_infra="temporal")

    commands = build_provision_plan(state, state_dir=_state_dir(tmp_path)).commands
    command = next(
        command for command in commands if command.operation == "start_isolated_engine"
    )

    assert command.argv[-2:] == (
        "isolated-temporal",
        "isolated-temporal-ui",
    )
    assert "--wait" in command.argv
    initializer = commands[commands.index(command) + 1]
    assert initializer.operation == "run_isolated_temporal_init"
    assert initializer.argv[-3:] == (
        "run",
        "--rm",
        "isolated-temporal-init",
    )


def test_isolated_minio_waits_then_runs_its_bucket_initializer(tmp_path: Path) -> None:
    state = build_state_plan(3, provider_slot=3, isolate_infra="minio")
    commands = build_provision_plan(state, state_dir=_state_dir(tmp_path)).commands

    start = next(
        command for command in commands if command.operation == "start_isolated_engine"
    )
    initializer = commands[commands.index(start) + 1]

    assert start.argv[-4:] == ("up", "--detach", "--wait", "isolated-minio")
    assert initializer.operation == "run_isolated_minio_init"
    assert initializer.argv[-3:] == ("run", "--rm", "isolated-minio-init")


def test_destructive_command_constructor_rejects_broad_targets_and_arguments() -> None:
    with pytest.raises(StateValidationError):
        StateCommand("bad", "futureagi;rm", ("echo", "no"))
    with pytest.raises(StateValidationError):
        StateCommand(
            "delete_isolated_volume",
            "futureagi_slot_03_postgres_data",
            ("docker", "system", "prune"),
            destructive=True,
        )
    with pytest.raises(StateValidationError):
        StateCommand(
            "drop_postgres_database",
            "postgres",
            (
                "docker",
                "compose",
                "--project-name",
                CONTROL_PLANE_PROJECT,
                "-f",
                CONTROL_PLANE_COMPOSE_FILE,
                "exec",
                "-T",
                "postgres",
                "psql",
                "--dbname=postgres",
                "--command",
                'DROP DATABASE IF EXISTS "postgres"',
            ),
            destructive=True,
        )
    with pytest.raises(StateValidationError):
        StateCommand(
            "stop_isolated_engine",
            "futureagi-slot-03-postgres",
            (
                "docker",
                "compose",
                "--project-name",
                "futureagi-slot-03-postgres",
                "-f",
                "slots/compose/isolated-infra.yaml",
                "--profile",
                "isolated-postgres",
                "down",
                "--rmi",
                "all",
            ),
            destructive=True,
        )
