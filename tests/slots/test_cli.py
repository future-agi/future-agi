from pathlib import Path
import subprocess

from slots.cli import _error_message, main
from slots.registry import RegistryStore


def _primary(cwd: Path):
    def runner(argv, _worktree):
        if tuple(argv[:3]) == ("git", "worktree", "list"):
            return f"worktree {cwd}\n"
        return ""

    return runner


def test_cli_denies_before_any_registry_or_executor_mutation(
    tmp_path: Path, monkeypatch
):
    monkeypatch.delenv("SLOTS_RUNTIME_APPROVED", raising=False)
    compose_calls: list[tuple[str, ...]] = []
    state_calls: list[object] = []

    result = main(
        ["up", "--slot", "1", "--services", "none"],
        tmp_path,
        _primary(tmp_path),
        lambda argv, _cwd: compose_calls.append(tuple(argv)),
        state_calls.append,
    )

    assert result == 2
    assert compose_calls == []
    assert state_calls == []
    assert RegistryStore(tmp_path / ".slots").load().slots == {}


def test_cli_error_message_includes_captured_subprocess_stderr():
    error = subprocess.CalledProcessError(
        1, ("docker", "compose", "up"), stderr="build failed\n"
    )
    assert _error_message(error).endswith(": build failed")


def test_cli_error_message_truncates_large_build_output_from_the_front():
    error = subprocess.CalledProcessError(
        1,
        ("docker", "compose", "up"),
        stderr="old progress\n" * 2_000 + "decisive failure\n",
    )
    message = _error_message(error)
    assert "earlier Docker output truncated" in message
    assert message.endswith("decisive failure")
    assert len(message) < 13_000


def test_cli_routes_all_approved_execution_through_injected_adapters(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("SLOTS_RUNTIME_APPROVED", "1")
    monkeypatch.setenv("SLOTS_MEMORY_CAP_MIB", "12000")
    compose_calls: list[tuple[str, ...]] = []
    state_calls: list[object] = []

    result = main(
        ["up", "--slot", "1", "--services", "none"],
        tmp_path,
        _primary(tmp_path),
        lambda argv, _cwd: compose_calls.append(tuple(argv)),
        state_calls.append,
    )

    assert result == 0
    assert compose_calls[0] == ("docker", "info", "--format", "{{.MemTotal}}")
    assert ("docker", "network", "create", "futureagi-slots") in compose_calls
    assert state_calls


def test_cli_only_lowers_configured_memory_cap_from_injected_docker_info(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("SLOTS_RUNTIME_APPROVED", "1")
    monkeypatch.setenv("SLOTS_MEMORY_CAP_MIB", "20000")
    commands: list[tuple[str, ...]] = []

    def executor(argv, _cwd):
        commands.append(tuple(argv))
        return "17179869184\n" if argv[:2] == ("docker", "info") else None

    result = main(
        ["up", "--slot", "1", "--services", "none"],
        tmp_path,
        _primary(tmp_path),
        executor,
        lambda _command: None,
    )

    assert result == 0
    # Sixteen GiB lowers the configured 20000 MiB cap without invoking Docker
    # outside the injected executor.
    assert commands[0] == ("docker", "info", "--format", "{{.MemTotal}}")


def test_cli_doctor_lowers_cap_and_accepts_missing_clean_state_network(
    tmp_path: Path, monkeypatch, capsys
):
    monkeypatch.setenv("SLOTS_RUNTIME_APPROVED", "1")
    monkeypatch.setenv("SLOTS_MEMORY_CAP_MIB", "20000")

    def executor(argv, _cwd):
        if argv[:2] == ("docker", "info"):
            return str(10 * 1024 * 1024 * 1024)
        if argv[:3] == ("docker", "network", "inspect"):
            raise subprocess.CalledProcessError(
                1, argv, stderr="network futureagi-slots not found"
            )
        return None

    assert main(["doctor"], tmp_path, _primary(tmp_path), executor) == 0
    output = capsys.readouterr().out
    assert '"configured_memory_cap_mib": 10240' in output
    assert '"slots": 0' in output
