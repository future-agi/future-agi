"""The thin, daemon-free command surface used by root Make targets."""

from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import json
import os
from pathlib import Path
import subprocess
import sys

from .registry import (
    CommandRunner,
    RegistryStore,
    _subprocess_runner,
    discover_state_dir,
)
from .provisioning import execute_state_command
from .runtime import CommandExecutor, SlotRuntime, StateCommandExecutor


def _subprocess_executor(argv: list[str] | tuple[str, ...], cwd: Path) -> str:
    return subprocess.run(
        argv, cwd=cwd, check=True, text=True, capture_output=True
    ).stdout


def _apply_docker_memory_cap(
    environment: dict[str, str], executor: CommandExecutor, cwd: Path
) -> None:
    """Safely lower (never raise) admission from approved Docker memory data."""
    result = executor(("docker", "info", "--format", "{{.MemTotal}}"), cwd)
    if not isinstance(result, (str, bytes)):
        return
    try:
        bytes_total = int(
            result.decode() if isinstance(result, bytes) else result.strip()
        )
    except ValueError:
        return
    if bytes_total <= 0:
        return
    docker_mib = bytes_total // (1024 * 1024)
    configured = int(environment.get("SLOTS_MEMORY_CAP_MIB", 16 * 1024))
    environment["SLOTS_MEMORY_CAP_MIB"] = str(min(configured, docker_mib))


def _require_runtime_approval(environment: dict[str, str]) -> None:
    if environment.get("SLOTS_RUNTIME_APPROVED") != "1":
        raise ValueError("runtime execution requires SLOTS_RUNTIME_APPROVED=1")


def _error_message(error: BaseException) -> str:
    message = str(error)
    details = getattr(error, "stderr", None) or getattr(error, "output", None)
    if isinstance(details, bytes):
        details = details.decode(errors="replace")
    if isinstance(details, str) and details.strip():
        cleaned = details.strip()
        if len(cleaned) > 12_000:
            cleaned = "...[earlier Docker output truncated]...\n" + cleaned[-12_000:]
        return f"{message}: {cleaned}"
    return message


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="futureagi-slots")
    commands = parser.add_subparsers(dest="action", required=True)
    up = commands.add_parser("up")
    up.add_argument("--slot", required=True)
    up.add_argument("--services", default="none")
    up.add_argument("--isolate-infra", default="")
    up.add_argument("--revision", default="")
    for action in ("down", "status", "urls", "purge"):
        command = commands.add_parser(action)
        command.add_argument("--slot", required=action != "status")
        if action == "purge":
            command.add_argument("--confirm", required=True)
    for action in ("logs", "shell", "run"):
        command = commands.add_parser(action)
        command.add_argument("--slot", required=True)
        command.add_argument("--service", required=True)
        if action == "run":
            command.add_argument("--command", required=True)
    commands.add_parser("doctor")
    commands.add_parser("recover")
    commands.add_parser(
        "prune", help="recovery cleanup for stale zero-reference shared providers"
    )
    return parser


def _serialise(value: object) -> object:
    if is_dataclass(value):
        return _serialise(asdict(value))
    if hasattr(value, "to_dict"):
        return value.to_dict()  # type: ignore[no-any-return]
    if hasattr(value, "argv") and hasattr(value, "cwd"):
        return {"argv": list(value.argv), "cwd": str(value.cwd)}  # type: ignore[attr-defined]
    if hasattr(value, "__dict__"):
        return {key: _serialise(item) for key, item in value.__dict__.items()}
    if isinstance(value, tuple):
        return [_serialise(item) for item in value]
    if isinstance(value, list):
        return [_serialise(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serialise(item) for key, item in value.items()}
    if isinstance(value, Path):
        return str(value)
    return value


def main(
    argv: list[str] | None = None,
    cwd: Path | None = None,
    runner: CommandRunner = _subprocess_runner,
    executor: CommandExecutor | None = None,
    state_executor: StateCommandExecutor | None = None,
) -> int:
    args = _parser().parse_args(argv)
    state_dir = discover_state_dir(cwd, dict(os.environ), runner)
    runtime = SlotRuntime(RegistryStore(state_dir), cwd or Path.cwd(), runner)
    environment = dict(os.environ)
    command_executor = _subprocess_executor if executor is None else executor
    try:
        if args.action == "up":
            _require_runtime_approval(environment)
            _apply_docker_memory_cap(environment, command_executor, cwd or Path.cwd())
            result = runtime.apply_up(
                args.slot,
                args.services,
                args.isolate_infra,
                args.revision,
                environment,
                command_executor,
                state_executor if state_executor is not None else execute_state_command,
            )
        elif args.action == "down":
            _require_runtime_approval(environment)
            result = runtime.apply_down(
                args.slot,
                command_executor,
                state_executor if state_executor is not None else execute_state_command,
            )
        elif args.action == "purge":
            _require_runtime_approval(environment)
            result = runtime.apply_purge(
                args.slot,
                args.confirm,
                command_executor,
                state_executor if state_executor is not None else execute_state_command,
            )
        elif args.action in {"status", "urls"}:
            records = runtime.status(args.slot if args.slot else None)
            result = tuple(
                record.routes if args.action == "urls" else record for record in records
            )
        elif args.action in {"logs", "shell", "run"}:
            _require_runtime_approval(environment)
            record = runtime.status(args.slot)[0]
            result = runtime.service_command(
                record, args.action, args.service, getattr(args, "command", None)
            )
            command_executor(result.argv, result.cwd)
        elif args.action == "doctor":
            _require_runtime_approval(environment)
            _apply_docker_memory_cap(environment, command_executor, cwd or Path.cwd())
            result = runtime.doctor(command_executor, environment)
        elif args.action == "recover":
            _require_runtime_approval(environment)
            result = runtime.apply_recover(
                command_executor,
                state_executor if state_executor is not None else execute_state_command,
            )
        else:  # prune
            _require_runtime_approval(environment)
            result = runtime.apply_prune(command_executor)
    except (ValueError, IndexError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"error: {_error_message(error)}", file=sys.stderr)
        return 2
    print(json.dumps(_serialise(result), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
