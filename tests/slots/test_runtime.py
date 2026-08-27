from pathlib import Path
import subprocess

import pytest

from slots.models import ProviderRecord, Registry, StateIdentity
from slots.registry import RegistryStore
from slots.runtime import (
    ResourceAdmissionError,
    SlotRuntime,
    allocate_slot,
    execute_commands,
)


def runtime(tmp_path: Path) -> SlotRuntime:
    return SlotRuntime(
        RegistryStore(tmp_path / ".slots"),
        tmp_path / "worktree",
        lambda _argv, _cwd: "",
    )


def apply(runtime: SlotRuntime, slot: str, services: str, isolate: str = ""):
    return runtime.apply_up(
        slot,
        services,
        isolate,
        "",
        {"SLOTS_MEMORY_CAP_MIB": "12000"},
        lambda _argv, _cwd: None,
        lambda _command: None,
    )


def test_auto_allocation_generated_files_and_frontend_private(tmp_path: Path):
    current = runtime(tmp_path)
    assert current.plan_up("auto", "collector").record is not None
    assert current.status() == ()
    result = apply(current, "auto", "collector")
    record = result.record
    assert record is not None
    assert record.slot == 1
    assert record.services == ("backend", "collector")
    assert record.providers["frontend"] == "slot-01-frontend"
    assert record.providers["backend"] == "shared-backend"
    assert record.providers["collector"] == "slot-01-collector"
    assert record.routes["backend"] == "http://api.1.localhost"
    assert record.ports["frontend"] == 20110
    env = tmp_path / ".slots" / "slots" / "01" / "slot.env"
    assert "SLOT_FRONTEND_NAME=slot-01-frontend" in env.read_text()
    assert "SLOT_PG_DB=futureagi_slot_00" in env.read_text()
    assert env.stat().st_mode & 0o777 == 0o600
    assert (
        tmp_path / ".slots" / "slots" / "01" / "manifest.json"
    ).stat().st_mode & 0o777 == 0o600


def test_generated_environment_inherits_worktree_env_before_slot_topology(
    tmp_path: Path,
):
    current = runtime(tmp_path)
    current.worktree.mkdir(parents=True)
    (current.worktree / ".env").write_text(
        "CUSTOM_APPLICATION_SETTING=from-worktree\n"
        "VITE_EXPERIMENTAL_VIEW=true\n"
        "SLOT=99\n"
        "SLOT_PG_DB=wrong_database\n",
        encoding="utf-8",
    )

    apply(current, "1", "none")

    contents = (tmp_path / ".slots" / "slots" / "01" / "slot.env").read_text(
        encoding="utf-8"
    )
    assert "CUSTOM_APPLICATION_SETTING=from-worktree\n" in contents
    assert "VITE_EXPERIMENTAL_VIEW=true\n" in contents
    assert contents.index("SLOT=99\n") < contents.index("SLOT=1\n")
    assert contents.index("SLOT_PG_DB=wrong_database\n") < contents.index(
        "SLOT_PG_DB=futureagi_slot_00\n"
    )


def test_shared_environment_inherits_its_owner_worktree_env(tmp_path: Path):
    current = runtime(tmp_path)
    current.worktree.mkdir(parents=True)
    (current.worktree / ".env").write_text(
        "CUSTOM_SHARED_SETTING=owner-value\n", encoding="utf-8"
    )

    apply(current, "1", "none")

    shared = (tmp_path / ".slots" / "shared" / "backend.env").read_text(
        encoding="utf-8"
    )
    assert "CUSTOM_SHARED_SETTING=owner-value\n" in shared
    assert "SLOT=0\n" in shared


def test_doctor_accepts_missing_network_before_first_slot(tmp_path: Path):
    current = runtime(tmp_path)

    def executor(argv, _cwd):
        if argv[:3] == ("docker", "network", "inspect"):
            raise subprocess.CalledProcessError(
                1, argv, stderr="network futureagi-slots not found"
            )

    report = current.doctor(executor, {"SLOTS_MEMORY_CAP_MIB": "10240"})
    assert report["slots"] == 0
    assert report["admission_limit_mib"] == 7680


def test_replacement_and_down_update_reference_counts_without_deleting_data(
    tmp_path: Path,
):
    current = runtime(tmp_path)
    apply(current, "1", "backend")
    apply(current, "2", "none")
    replacement = apply(current, "1", "collector")
    assert replacement.record.providers["backend"] == "shared-backend"
    down = current.apply_down("1", lambda _argv, _cwd: None)
    assert "slot-01-collector" in down.stopped_providers
    assert (tmp_path / ".slots" / "slots" / "01" / "slot.env").exists()


def test_down_reloads_traefik_after_removing_route_with_surviving_slots(
    tmp_path: Path,
):
    current = runtime(tmp_path)
    apply(current, "1", "none")
    apply(current, "2", "none")
    commands: list[tuple[str, ...]] = []

    current.apply_down("1", lambda argv, _cwd: commands.append(tuple(argv)))

    assert commands[-1][-2:] == ("restart", "traefik")
    assert "futureagi-slots" in commands[-1]
    assert not (tmp_path / ".slots" / "routes" / "slot-01.yaml").exists()


def test_post_commit_down_recovery_retries_route_reload_after_route_is_gone(
    tmp_path: Path,
):
    current = runtime(tmp_path)
    apply(current, "1", "none")
    apply(current, "2", "none")
    record = current.status("1")[0]
    current.apply_down("1", lambda _argv, _cwd: None)
    current._write_recovery_journal(record, "down")
    commands: list[tuple[str, ...]] = []

    result = current.apply_recover(
        lambda argv, _cwd: commands.append(tuple(argv)), lambda _command: None
    )

    assert result.action == "recover-down-complete"
    assert commands[-1][-2:] == ("restart", "traefik")
    assert current.status("1") == ()
    assert len(current.status()) == 1


def test_resource_admission_force_and_purge_confirmation(tmp_path: Path):
    current = runtime(tmp_path)
    with pytest.raises(ResourceAdmissionError):
        current.plan_up("1", "backend", environment={"SLOTS_MEMORY_CAP_MIB": "1000"})
    current.apply_up(
        "1",
        "backend",
        "",
        "",
        {"SLOTS_MEMORY_CAP_MIB": "1000", "FORCE": "1"},
        lambda _argv, _cwd: None,
        lambda _command: None,
    )
    with pytest.raises(ValueError, match="CONFIRM=1"):
        current.plan_purge("1", "2")
    assert current.plan_purge("1", "1").action == "purge"


def test_isolated_engines_require_private_backend_and_receive_a_port(tmp_path: Path):
    current = runtime(tmp_path)
    with pytest.raises(ValueError, match="SERVICES=backend"):
        current.plan_up("1", "collector", "postgres")
    plan = apply(current, "1", "backend", "postgres")
    assert plan.record.ports["postgres"] == 20101
    assert any(command.argv[-2:] == ("build", "backend") for command in plan.commands)
    assert (
        "SLOT_POSTGRES_PORT=20101"
        in (tmp_path / ".slots" / "slots" / "01" / "slot.env").read_text()
    )


def test_isolated_environment_matches_volume_names_and_route_targets(tmp_path: Path):
    current = runtime(tmp_path)
    plan = current.plan_up(
        "1",
        "backend",
        "minio,rabbitmq,temporal",
        environment={"SLOTS_MEMORY_CAP_MIB": "16000", "SLOTS_HTTP_PORT": "8088"},
    )
    assert plan.record is not None
    values = current.environment_for(plan.record)
    route = current._route_contents(plan.record)

    assert values["SLOT_MINIO_VOLUME"] == "futureagi_slot_01_minio_data"
    assert values["SLOT_RABBITMQ_VOLUME"] == "futureagi_slot_01_rabbitmq_data"
    assert values["SLOT_TEMPORAL_VOLUME"] == "futureagi_slot_01_temporal_data"
    assert values["SLOT_ISOLATED_MINIO_NAME"] == "futureagi-slot-01-minio"
    assert values["SLOT_ISOLATED_RABBITMQ_NAME"] == "futureagi-slot-01-rabbitmq"
    assert values["SLOT_ISOLATED_TEMPORAL_NAME"] == "futureagi-slot-01-temporal"
    assert (
        values["SLOT_ISOLATED_TEMPORAL_POSTGRES_NAME"]
        == "futureagi-slot-01-temporal-postgres"
    )
    assert values["SLOT_TEMPORAL_UI_NAME"] == "futureagi-slot-01-temporal-ui"
    assert plan.record.routes["frontend"] == "http://1.localhost:8088"
    assert (
        plan.record.routes["minio-console"] == "http://minio-console.1.localhost:8088"
    )
    assert "futureagi-slot-01-minio:9000" in route
    assert "futureagi-slot-01-rabbitmq:15672" in route
    assert "futureagi-slot-01-temporal-ui:8080" in route
    assert "shared-observability:16686" in route
    assert plan.record.routes["jaeger"] == "http://jaeger.1.localhost:8088"


def test_command_execution_is_explicitly_injected(tmp_path: Path):
    current = runtime(tmp_path)
    plan = current.plan_up("1", "none", environment={"SLOTS_MEMORY_CAP_MIB": "12000"})
    executed = []
    execute_commands(
        plan.commands, lambda argv, cwd: executed.append((tuple(argv), cwd))
    )
    assert executed[0][0] == ("docker", "network", "create", "futureagi-slots")
    current.apply_up(
        "1",
        "none",
        "",
        "",
        {"SLOTS_MEMORY_CAP_MIB": "12000"},
        lambda _argv, _cwd: None,
        lambda _command: None,
    )
    assert allocate_slot("auto", current.store.load()) == 2


def test_failed_external_execution_preserves_recovery_artifacts_without_commit(
    tmp_path: Path,
):
    current = runtime(tmp_path)

    with pytest.raises(RuntimeError, match="compose failed"):
        current.apply_up(
            "1",
            "none",
            "",
            "",
            {"SLOTS_MEMORY_CAP_MIB": "12000"},
            lambda _argv, _cwd: (_ for _ in ()).throw(RuntimeError("compose failed")),
            lambda _command: None,
        )

    assert current.status() == ()
    assert (tmp_path / ".slots" / "slots" / "01" / "slot.env").exists()
    assert (tmp_path / ".slots" / "routes" / "slot-01.yaml").exists()
    journal = (tmp_path / ".slots" / "recovery.json").read_text()
    assert '"record"' in journal


def test_recover_cleans_failed_new_slot_without_deleting_volumes(tmp_path: Path):
    current = runtime(tmp_path)
    with pytest.raises(RuntimeError, match="compose failed"):
        current.apply_up(
            "1",
            "none",
            "",
            "",
            {"SLOTS_MEMORY_CAP_MIB": "12000"},
            lambda _argv, _cwd: (_ for _ in ()).throw(RuntimeError("compose failed")),
            lambda _command: None,
        )

    commands: list[tuple[str, ...]] = []
    result = current.apply_recover(
        lambda argv, _cwd: commands.append(tuple(argv)), lambda _command: None
    )

    assert result.action == "recover"
    assert not (tmp_path / ".slots" / "recovery.json").exists()
    assert current.status() == ()
    assert any("futureagi-slots" in command for command in commands)
    assert ("docker", "network", "rm", "futureagi-slots") in commands
    assert not any("--volumes" in command for command in commands)


def test_recover_supports_legacy_journal_with_provider_environment(tmp_path: Path):
    current = runtime(tmp_path)
    current._write_private(
        tmp_path / ".slots" / "recovery.json", '{"action":"up","slot":1}\n'
    )
    current._write_private(
        tmp_path / ".slots" / "shared" / "executor.env", "SLOT_ID=00\n"
    )
    commands: list[tuple[str, ...]] = []

    current.apply_recover(lambda argv, _cwd: commands.append(tuple(argv)))

    assert any("futureagi-slots-default-executor" in command for command in commands)
    assert any("slots/compose/control-plane.yaml" in command for command in commands)
    assert not (tmp_path / ".slots" / "recovery.json").exists()


def test_recover_interrupted_replacement_cleans_only_private_project(tmp_path: Path):
    current = runtime(tmp_path)
    apply(current, "1", "backend")
    record = current.status("1")[0]
    current._write_recovery_journal(record, "up")
    commands: list[tuple[str, ...]] = []

    result = current.apply_recover(
        lambda argv, _cwd: commands.append(tuple(argv)), lambda _command: None
    )

    assert result.action == "recover-replacement"
    assert len(commands) == 1
    assert "futureagi-slot-01" in commands[0]
    assert commands[0][-1] == "down"
    assert "slots/compose/control-plane.yaml" not in commands[0]
    assert commands[0][:3] != ("docker", "network", "rm")
    assert current.status("1") == (record,)
    assert not (tmp_path / ".slots" / "recovery.json").exists()


def test_recover_interrupted_replacement_suspends_new_isolated_infra(
    tmp_path: Path,
):
    current = runtime(tmp_path)
    apply(current, "1", "none")
    previous = current.status("1")[0]
    replacement = current.plan_up(
        "1",
        "backend",
        "postgres",
        environment={"SLOTS_MEMORY_CAP_MIB": "12000"},
    ).record
    assert replacement is not None
    current._stage_artifacts(replacement)
    current._write_recovery_journal(replacement, "up")
    state_commands: list[object] = []

    result = current.apply_recover(lambda _argv, _cwd: None, state_commands.append)

    assert result.action == "recover-replacement"
    assert current.status("1") == (previous,)
    assert [command.operation for command in state_commands] == [
        "suspend_isolated_engine"
    ]
    assert state_commands[0].target == "futureagi-slot-01-postgres"
    assert not any(
        command.operation == "delete_isolated_volume" for command in state_commands
    )
    assert not (tmp_path / ".slots" / "recovery.json").exists()


@pytest.mark.parametrize("action", ["down", "purge"])
def test_recover_unblocks_interrupted_cleanup_for_explicit_retry(
    tmp_path: Path, action: str
):
    current = runtime(tmp_path)
    apply(current, "1", "backend")
    record = current.status("1")[0]
    current._write_recovery_journal(record, action)

    result = current.apply_recover(lambda _argv, _cwd: None, lambda _command: None)

    assert result.action == f"recover-{action}-retry"
    assert current.status("1") == (record,)
    assert not (tmp_path / ".slots" / "recovery.json").exists()

    if action == "down":
        current.apply_down("1", lambda _argv, _cwd: None)
    else:
        current.apply_purge("1", "1", lambda _argv, _cwd: None, lambda _command: None)
    assert current.status("1") == ()


def test_staging_failure_rolls_back_partially_published_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    current = runtime(tmp_path)
    monkeypatch.setattr(
        current,
        "_route_contents",
        lambda _record: (_ for _ in ()).throw(RuntimeError("route render failed")),
    )
    calls: list[tuple[str, ...]] = []

    with pytest.raises(RuntimeError, match="route render failed"):
        current.apply_up(
            "1",
            "none",
            "",
            "",
            {"SLOTS_MEMORY_CAP_MIB": "12000"},
            lambda argv, _cwd: calls.append(tuple(argv)),
            lambda _command: None,
        )

    assert calls == []
    assert not (tmp_path / ".slots" / "slots" / "01" / "slot.env").exists()
    assert not (tmp_path / ".slots" / "routes" / "slot-01.yaml").exists()


def test_shared_default_is_refused_when_source_changed_from_base_ref(tmp_path: Path):
    current = SlotRuntime(
        RegistryStore(tmp_path / ".slots"),
        tmp_path / "worktree",
        lambda _argv, _cwd: "futureagi/tfc/settings.py\n",
    )

    with pytest.raises(ValueError, match="source differs"):
        current.plan_up("1", "none")


def test_private_backend_skips_shared_source_validation_and_starts_worker(
    tmp_path: Path,
):
    def changed_backend_only(argv, _cwd):
        return "futureagi/tfc/settings.py\n" if argv[-1] == "futureagi" else ""

    current = SlotRuntime(
        RegistryStore(tmp_path / ".slots"),
        tmp_path / "worktree",
        changed_backend_only,
    )

    plan = current.plan_up(
        "1", "backend", environment={"SLOTS_MEMORY_CAP_MIB": "12000"}
    )

    assert plan.record is not None
    assert plan.record.providers["backend"] == "slot-01-backend"
    up = next(
        command.argv
        for command in plan.commands
        if "futureagi-slot-01" in command.argv and "up" in command.argv
    )
    assert up[-6:] == (
        "backend",
        "worker",
        "simulation",
        "collector",
        "peerdb",
        "frontend",
    )


def test_network_then_control_then_state_then_compose_are_injected_in_order(
    tmp_path: Path,
):
    current = runtime(tmp_path)
    calls: list[tuple[str, str]] = []

    current.apply_up(
        "1",
        "none",
        "",
        "",
        {"SLOTS_MEMORY_CAP_MIB": "12000"},
        lambda argv, _cwd: calls.append(("compose", " ".join(argv))),
        lambda command: calls.append(("state", command.operation)),
    )

    assert calls[0] == ("compose", "docker network create futureagi-slots")
    assert "control-plane.yaml" in calls[1][1]
    first_state = next(index for index, call in enumerate(calls) if call[0] == "state")
    assert first_state == 2
    assert any(
        "slots/compose/backend.yaml" in call[1] for call in calls[first_state + 1 :]
    )
    assert any("restart traefik" in call[1] for call in calls[first_state + 1 :])


def test_runtime_never_falls_back_to_a_subprocess_state_executor(tmp_path: Path):
    current = runtime(tmp_path)
    compose_calls: list[tuple[str, ...]] = []

    with pytest.raises(ValueError, match="StateCommand executor"):
        current.apply_up(
            "1",
            "none",
            "",
            "",
            {"SLOTS_MEMORY_CAP_MIB": "12000"},
            lambda argv, _cwd: compose_calls.append(tuple(argv)),
        )

    assert compose_calls == []
    assert current.status() == ()


def test_replacement_stops_the_old_full_project_before_new_control_plane(
    tmp_path: Path,
):
    current = runtime(tmp_path)
    apply(current, "1", "collector")
    calls: list[tuple[str, ...]] = []

    apply_result = current.apply_up(
        "1",
        "backend",
        "",
        "",
        {"SLOTS_MEMORY_CAP_MIB": "12000"},
        lambda argv, _cwd: calls.append(tuple(argv)),
        lambda _command: None,
    )

    assert apply_result.record is not None
    assert calls[0][-1] == "down"
    assert "slots/compose/collector.yaml" in calls[0]
    assert calls[1] == ("docker", "network", "create", "futureagi-slots")


def test_last_shared_reference_stops_all_implicit_shared_defaults(tmp_path: Path):
    current = runtime(tmp_path)
    apply(current, "1", "none")
    apply(current, "2", "none")
    first: list[tuple[str, ...]] = []
    current.apply_down("1", lambda argv, _cwd: first.append(tuple(argv)))
    assert not any("futureagi-slots-default-backend" in call for call in first)

    second: list[tuple[str, ...]] = []
    current.apply_down("2", lambda argv, _cwd: second.append(tuple(argv)))
    projects = {
        command[command.index("--project-name") + 1]
        for command in second
        if "--project-name" in command
    }
    assert {
        "futureagi-slots-default-backend",
        "futureagi-slots-default-simulation",
        "futureagi-slots-default-gateway",
        "futureagi-slots-default-collector",
        "futureagi-slots-default-serving",
        "futureagi-slots-default-executor",
        "futureagi-slots-default-peerdb",
        "futureagi-slots-default-observability",
    } <= projects


def test_purge_keeps_files_and_registry_when_private_state_cleanup_fails(
    tmp_path: Path,
):
    current = runtime(tmp_path)
    apply(current, "1", "backend", "postgres")
    env = tmp_path / ".slots" / "slots" / "01" / "slot.env"

    with pytest.raises(RuntimeError, match="purge failed"):
        current.apply_purge(
            "1",
            "1",
            lambda _argv, _cwd: None,
            lambda _command: (_ for _ in ()).throw(RuntimeError("purge failed")),
        )

    assert current.status("1")
    assert env.exists()


def test_shared_service_commands_use_the_provider_owner_environment(tmp_path: Path):
    current = runtime(tmp_path)
    apply(current, "1", "none")
    apply(current, "2", "none")
    second = current.status("2")[0]

    command = current.service_command(second, "logs", "backend")

    assert "futureagi-slots-default-backend" in command.argv
    assert str(tmp_path / ".slots" / "shared" / "backend.env") in command.argv


@pytest.mark.parametrize("value", ["0", "65536", "abc", "80.5", " 80"])
def test_invalid_http_port_fails_during_pure_planning(tmp_path: Path, value: str):
    current = runtime(tmp_path)

    with pytest.raises(ValueError, match="SLOTS_HTTP_PORT"):
        current.plan_up("1", "none", environment={"SLOTS_HTTP_PORT": value})

    assert current.status() == ()
    assert not (tmp_path / ".slots" / "slots" / "01" / "slot.env").exists()


def test_every_new_shared_default_is_clean_source_validated(tmp_path: Path):
    calls: list[tuple[str, ...]] = []

    def git_runner(argv, _cwd):
        calls.append(tuple(argv))
        return "" if "agentcc-gateway" not in argv else "agentcc-gateway/main.go\n"

    current = SlotRuntime(
        RegistryStore(tmp_path / ".slots"), tmp_path / "worktree", git_runner
    )

    with pytest.raises(ValueError, match="shared gateway"):
        current.plan_up("1", "none")

    checked = {argument for command in calls for argument in command}
    assert "futureagi" in checked
    assert "agentcc-gateway" in checked


def test_prune_is_recovery_cleanup_for_a_stale_zero_reference_provider(tmp_path: Path):
    current = runtime(tmp_path)
    stale = ProviderRecord(
        "shared-observability",
        "observability",
        str(tmp_path / "worktree"),
        StateIdentity.for_slot(0),
        (),
        False,
    )
    with current.store.locked() as registry:
        current.store.save(
            Registry(registry.version, dict(registry.slots), {stale.name: stale})
        )
    commands: list[tuple[str, ...]] = []

    result = current.apply_prune(lambda argv, _cwd: commands.append(tuple(argv)))

    assert result.action == "prune"
    assert commands[0][-1] == "down"
    assert "futureagi-slots-default-observability" in commands[0]
    assert current.store.load().providers == {}


def test_peerdb_uses_its_exact_compose_members_and_ui_for_interactive_commands(
    tmp_path: Path,
):
    current = runtime(tmp_path)
    shared = current.plan_up("1", "none", environment={"SLOTS_MEMORY_CAP_MIB": "12000"})
    shared_peerdb = next(
        command.argv
        for command in shared.commands
        if "slots/compose/peerdb.yaml" in command.argv
    )
    assert shared_peerdb[-1:] == ("peerdb",)
    assert not any(
        command.argv[-2:] == ("wait", "peerdb") for command in shared.commands
    )
    plan = current.plan_up("1", "peerdb", environment={"SLOTS_MEMORY_CAP_MIB": "12000"})
    private_up = plan.commands[-1].argv

    assert private_up[-2:] == ("peerdb", "frontend")
    assert "--build" not in private_up
    private_builds = [
        command.argv[-1]
        for command in plan.commands
        if command.argv[-2:-1] == ("build",) and "futureagi-slot-01" in command.argv
    ]
    assert private_builds == ["frontend"]
    assert "slots/compose/frontend.yaml" in private_up
    assert "slots/compose/backend.yaml" not in private_up
    apply(current, "1", "peerdb")
    command = current.service_command(current.status("1")[0], "shell", "peerdb")
    assert command.argv[-2:] == ("peerdb-ui", "sh")


def test_private_peerdb_relies_on_compose_dependency_gate_without_racy_wait(
    tmp_path: Path,
):
    current = runtime(tmp_path)
    plan = current.plan_up(
        "1", "backend", environment={"SLOTS_MEMORY_CAP_MIB": "12000"}
    )
    private_up = plan.commands[-1].argv

    assert private_up[-2:] == ("peerdb", "frontend")
    assert not any(command.argv[-2:] == ("wait", "peerdb") for command in plan.commands)


def test_private_source_images_build_sequentially_before_compose_up(tmp_path: Path):
    current = runtime(tmp_path)
    plan = current.plan_up(
        "1", "backend", environment={"SLOTS_MEMORY_CAP_MIB": "12000"}
    )
    builds = [
        command.argv[-1]
        for command in plan.commands
        if command.argv[-2:-1] == ("build",) and "futureagi-slot-01" in command.argv
    ]

    assert builds == ["backend", "simulation", "collector", "frontend"]
    assert all("--build" not in command.argv for command in plan.commands)


def test_private_backend_closes_state_coupled_provider_groups(tmp_path: Path):
    current = runtime(tmp_path)
    plan = apply(current, "1", "backend")
    assert plan.record is not None
    assert all(
        plan.record.providers[group] == f"slot-01-{group}"
        for group in ("backend", "simulation", "collector", "peerdb")
    )
    values = current.environment_for(plan.record)
    assert values["SLOT_PEERDB_UI_NAME"] == "futureagi-slot-01-peerdb-ui"
    assert (
        values["SLOT_PEERDB_CATALOG_VOLUME"] == "futureagi-slot-01-peerdb-catalog-data"
    )


def test_missing_simulation_wheel_fails_before_runtime_mutation(tmp_path: Path):
    current = runtime(tmp_path)
    current.worktree.mkdir(parents=True)
    (current.worktree / "Dockerfile.simulation-runner.dev").write_text("FROM scratch\n")
    commands: list[tuple[str, ...]] = []

    with pytest.raises(ValueError, match="simulation build requires"):
        current.apply_up(
            "1",
            "backend",
            "",
            "",
            {"SLOTS_MEMORY_CAP_MIB": "12000"},
            lambda argv, _cwd: commands.append(tuple(argv)),
            lambda _command: None,
        )

    assert commands == []
    assert not (tmp_path / ".slots" / "recovery.json").exists()
    assert not (tmp_path / ".slots" / "slots" / "01" / "slot.env").exists()


def test_active_slots_require_one_global_public_port(tmp_path: Path):
    current = runtime(tmp_path)
    current.apply_up(
        "1",
        "none",
        "",
        "",
        {"SLOTS_MEMORY_CAP_MIB": "12000", "SLOTS_HTTP_PORT": "8088"},
        lambda _argv, _cwd: None,
        lambda _command: None,
    )
    with pytest.raises(ValueError, match="single public port"):
        current.plan_up(
            "2",
            "none",
            environment={"SLOTS_MEMORY_CAP_MIB": "12000", "SLOTS_HTTP_PORT": "8089"},
        )


def test_recovery_journal_blocks_mutation_without_overwrite(tmp_path: Path):
    current = runtime(tmp_path)
    journal = tmp_path / ".slots" / "recovery.json"
    journal.parent.mkdir()
    journal.write_text('{"action":"up","slot":1}\n')
    with pytest.raises(RuntimeError, match="recovery journal blocks"):
        current.apply_up(
            "1",
            "none",
            "",
            "",
            {"SLOTS_MEMORY_CAP_MIB": "12000"},
            lambda _argv, _cwd: None,
            lambda _command: None,
        )
    assert journal.read_text() == '{"action":"up","slot":1}\n'


def test_last_down_passes_authoritative_env_to_control_plane(tmp_path: Path):
    current = runtime(tmp_path)
    apply(current, "1", "none")
    commands: list[tuple[str, ...]] = []
    current.apply_down("1", lambda argv, _cwd: commands.append(tuple(argv)))
    control = next(
        command for command in commands if "slots/compose/control-plane.yaml" in command
    )
    assert "--env-file" in control
    assert str(tmp_path / ".slots" / "slots" / "01" / "slot.env") in control


def test_active_purge_stops_last_shared_control_and_network_before_commit(
    tmp_path: Path,
):
    current = runtime(tmp_path)
    apply(current, "1", "none")
    commands: list[tuple[str, ...]] = []
    result = current.apply_purge(
        "1", "1", lambda argv, _cwd: commands.append(tuple(argv))
    )
    assert result.stopped_providers
    assert any("futureagi-slots-default-backend" in command for command in commands)
    assert any("slots/compose/control-plane.yaml" in command for command in commands)
    assert ("docker", "network", "rm", "futureagi-slots") in commands


def test_shared_peerdb_up_uses_provider_stable_environment(tmp_path: Path):
    current = runtime(tmp_path)
    plan = current.plan_up("1", "none", environment={"SLOTS_MEMORY_CAP_MIB": "12000"})
    peerdb_up = next(
        command.argv
        for command in plan.commands
        if "futureagi-slots-default-peerdb" in command.argv
    )
    assert str(tmp_path / ".slots" / "shared" / "peerdb.env") in peerdb_up


def test_shared_owner_worktree_is_preserved_while_referenced(tmp_path: Path):
    store = RegistryStore(tmp_path / ".slots")
    first = SlotRuntime(store, tmp_path / "first", lambda _argv, _cwd: "")
    second = SlotRuntime(store, tmp_path / "second", lambda _argv, _cwd: "")
    first.apply_up(
        "1",
        "none",
        "",
        "",
        {"SLOTS_MEMORY_CAP_MIB": "12000"},
        lambda _argv, _cwd: None,
        lambda _command: None,
    )
    second.apply_up(
        "2",
        "none",
        "",
        "",
        {"SLOTS_MEMORY_CAP_MIB": "12000"},
        lambda _argv, _cwd: None,
        lambda _command: None,
    )
    assert store.load().providers["shared-backend"].worktree == str(
        (tmp_path / "first").resolve()
    )


def test_shared_owner_handoff_rebinds_environment_before_successor_up(
    tmp_path: Path,
):
    store = RegistryStore(tmp_path / ".slots")
    first_path = tmp_path / "first"
    second_path = tmp_path / "second"
    first = SlotRuntime(store, first_path, lambda _argv, _cwd: "")
    second = SlotRuntime(store, second_path, lambda _argv, _cwd: "")
    for current, slot in ((first, "1"), (second, "2")):
        current.apply_up(
            slot,
            "none",
            "",
            "",
            {"SLOTS_MEMORY_CAP_MIB": "12000"},
            lambda _argv, _cwd: None,
            lambda _command: None,
        )

    calls: list[tuple[tuple[str, ...], Path]] = []
    second.apply_down("1", lambda argv, cwd: calls.append((tuple(argv), cwd)))

    backend_calls = [
        (argv, cwd) for argv, cwd in calls if "futureagi-slots-default-backend" in argv
    ]
    assert len(backend_calls) == 1
    assert "--force-recreate" in backend_calls[0][0]
    assert backend_calls[0][0][-2:] == ("backend", "worker")
    assert backend_calls[0][1] == second_path.resolve()
    assert store.load().providers["shared-backend"].worktree == str(
        second_path.resolve()
    )
    shared_env = (tmp_path / ".slots" / "shared" / "backend.env").read_text()
    assert f"SLOT_WORKTREE={second_path.resolve()}" in shared_env


def test_shared_owner_handoff_failure_restores_environment_and_keeps_journal(
    tmp_path: Path,
):
    store = RegistryStore(tmp_path / ".slots")
    first_path = tmp_path / "first"
    second_path = tmp_path / "second"
    first = SlotRuntime(store, first_path, lambda _argv, _cwd: "")
    second = SlotRuntime(store, second_path, lambda _argv, _cwd: "")
    for current, slot in ((first, "1"), (second, "2")):
        current.apply_up(
            slot,
            "none",
            "",
            "",
            {"SLOTS_MEMORY_CAP_MIB": "12000"},
            lambda _argv, _cwd: None,
            lambda _command: None,
        )
    env_path = tmp_path / ".slots" / "shared" / "backend.env"
    old_env = env_path.read_bytes()

    def fail_successor_backend(argv, _cwd):
        if "futureagi-slots-default-backend" in argv and argv[-2:] == (
            "backend",
            "worker",
        ):
            raise RuntimeError("successor compose failure")

    with pytest.raises(RuntimeError, match="successor compose failure"):
        second.apply_down("1", fail_successor_backend)

    assert env_path.read_bytes() == old_env
    assert store.load().providers["shared-backend"].worktree == str(
        first_path.resolve()
    )
    assert (tmp_path / ".slots" / "recovery.json").exists()


def test_shared_owner_handoff_refuses_changed_successor_source(tmp_path: Path):
    store = RegistryStore(tmp_path / ".slots")
    first_path = tmp_path / "first"
    second_path = tmp_path / "second"
    first = SlotRuntime(store, first_path, lambda _argv, _cwd: "")

    def changed_backend(argv, cwd):
        if cwd == second_path.resolve() and argv[:3] == (
            "git",
            "diff",
            "--name-only",
        ):
            return "futureagi/changed.py\n"
        return ""

    second = SlotRuntime(store, second_path, changed_backend)
    for current, slot in ((first, "1"), (second, "2")):
        current.apply_up(
            slot,
            "none",
            "",
            "",
            {"SLOTS_MEMORY_CAP_MIB": "12000"},
            lambda _argv, _cwd: None,
            lambda _command: None,
        )
    env_path = tmp_path / ".slots" / "shared" / "backend.env"
    old_env = env_path.read_bytes()

    with pytest.raises(ValueError, match="cannot hand off shared backend"):
        second.apply_down("1", lambda _argv, _cwd: None)

    assert env_path.read_bytes() == old_env
    assert store.load().providers["shared-backend"].worktree == str(
        first_path.resolve()
    )
    assert (tmp_path / ".slots" / "recovery.json").exists()


def test_same_worktree_replacement_does_not_recreate_shared_owners(tmp_path: Path):
    current = runtime(tmp_path)
    apply(current, "1", "none")
    calls: list[tuple[str, ...]] = []
    current.apply_up(
        "1",
        "none",
        "",
        "",
        {"SLOTS_MEMORY_CAP_MIB": "12000"},
        lambda argv, _cwd: calls.append(tuple(argv)),
        lambda _command: None,
    )
    assert not any("--force-recreate" in command for command in calls)


def test_replacement_retains_retired_isolated_volume_for_confirmed_purge(
    tmp_path: Path,
):
    current = runtime(tmp_path)
    apply(current, "1", "backend", "postgres")
    replacement = current.apply_up(
        "1",
        "none",
        "",
        "",
        {"SLOTS_MEMORY_CAP_MIB": "12000"},
        lambda _argv, _cwd: None,
        lambda _command: None,
    )
    assert replacement.record is not None
    assert replacement.record.retired_isolated_infra == ("postgres",)
    state_commands: list[object] = []
    current.apply_purge("1", "1", lambda _argv, _cwd: None, state_commands.append)
    assert any(
        command.operation == "delete_isolated_volume"
        and command.target == "futureagi_slot_01_postgres_data"
        for command in state_commands
    )


def test_retired_isolated_engine_is_not_purged_through_shared_control_plane(
    tmp_path: Path,
):
    current = runtime(tmp_path)
    apply(current, "1", "backend", "rabbitmq")
    replacement = current.apply_up(
        "1",
        "none",
        "",
        "",
        {"SLOTS_MEMORY_CAP_MIB": "12000"},
        lambda _argv, _cwd: None,
        lambda _command: None,
    )
    assert replacement.record is not None
    assert replacement.record.retired_isolated_infra == ("rabbitmq",)

    commands = current.plan_purge("1", "1").state_commands

    assert any(
        command.operation == "delete_isolated_volume"
        and command.target == "futureagi_slot_01_rabbitmq_data"
        for command in commands
    )
    assert any(
        command.operation == "stop_isolated_engine"
        and command.target == "futureagi-slot-01-rabbitmq"
        for command in commands
    )
    assert not any(command.operation == "delete_rabbitmq_vhost" for command in commands)


def test_shared_env_strips_private_isolation_host_and_volume_overrides(tmp_path: Path):
    current = runtime(tmp_path)
    apply(current, "1", "backend", "redis")
    shared_gateway = (tmp_path / ".slots" / "shared" / "gateway.env").read_text()
    assert "SLOT_REDIS_HOST=" not in shared_gateway
    assert "SLOT_REDIS_VOLUME=" not in shared_gateway
    assert "SLOT_REDIS_DB=0" in shared_gateway


def test_simulation_receives_exact_private_or_shared_backend_image_reference(
    tmp_path: Path,
):
    current = runtime(tmp_path)
    private = current.plan_up(
        "1", "backend", environment={"SLOTS_MEMORY_CAP_MIB": "12000"}
    )
    shared = current.plan_up(
        "2", "simulation", environment={"SLOTS_MEMORY_CAP_MIB": "12000"}
    )
    assert current.environment_for(private.record)["SLOT_BACKEND_IMAGE_REF"] == (
        "futureagi/slot-backend:01"
    )
    assert current.environment_for(shared.record)["SLOT_BACKEND_IMAGE_REF"] == (
        "futureagi/slot-backend:00"
    )


def test_shared_replacement_retains_private_logical_state_for_confirmed_purge(
    tmp_path: Path,
):
    current = runtime(tmp_path)
    apply(current, "1", "backend", "postgres")
    replacement = current.apply_up(
        "1",
        "none",
        "",
        "",
        {"SLOTS_MEMORY_CAP_MIB": "12000"},
        lambda _argv, _cwd: None,
        lambda _command: None,
    )
    assert replacement.record is not None
    assert replacement.record.retained_private_backend_state
    commands: list[object] = []
    current.apply_purge("1", "1", lambda _argv, _cwd: None, commands.append)
    operations = {command.operation for command in commands}
    assert {
        "drop_clickhouse_database",
        "flush_redis_database",
        "delete_rabbitmq_vhost",
        "delete_minio_bucket",
        "delete_temporal_namespace",
        "delete_isolated_volume",
    } <= operations
    assert "drop_postgres_database" not in operations
    assert all("slot_00" not in command.target for command in commands)


def test_shared_replacement_purge_removes_retired_private_provider_volumes(
    tmp_path: Path,
):
    current = runtime(tmp_path)
    apply(current, "1", "backend")
    current.apply_up(
        "1",
        "none",
        "",
        "",
        {"SLOTS_MEMORY_CAP_MIB": "12000"},
        lambda _argv, _cwd: None,
        lambda _command: None,
    )
    commands: list[tuple[str, ...]] = []
    current.apply_purge(
        "1", "1", lambda argv, _cwd: commands.append(tuple(argv)), lambda _command: None
    )
    assert {
        ("docker", "volume", "rm", "futureagi-slot-01-frontend-node-modules"),
        ("docker", "volume", "rm", "futureagi-slot-01-backend-media"),
        ("docker", "volume", "rm", "futureagi-slot-01-collector-data"),
        ("docker", "volume", "rm", "futureagi-slot-01-peerdb-catalog-data"),
        ("docker", "volume", "rm", "futureagi-slot-01-peerdb-minio-data"),
    } <= set(commands)
    assert not any("futureagi-shared" in command[-1] for command in commands)


def test_restarted_inactive_slot_preserves_manifest_purge_provenance(tmp_path: Path):
    current = runtime(tmp_path)
    apply(current, "1", "backend", "postgres")
    current.apply_down("1", lambda _argv, _cwd: None, lambda _command: None)
    restarted = current.apply_up(
        "1",
        "none",
        "",
        "",
        {"SLOTS_MEMORY_CAP_MIB": "12000"},
        lambda _argv, _cwd: None,
        lambda _command: None,
    )
    assert restarted.record is not None
    assert restarted.record.retained_private_backend_state
    assert restarted.record.retired_isolated_infra == ("postgres",)
    commands: list[object] = []
    current.apply_purge("1", "1", lambda _argv, _cwd: None, commands.append)
    assert not any(
        command.operation == "drop_postgres_database" for command in commands
    )
    assert any(
        command.operation == "delete_isolated_volume"
        and command.target == "futureagi_slot_01_postgres_data"
        for command in commands
    )
