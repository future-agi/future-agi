import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parents[2] / "entrypoint.sh"


def _guard_source() -> str:
    source = ENTRYPOINT.read_text()
    start = source.index("# Hosted application startup is mutation-free by default.")
    end = source.index("# Disable bytecode compilation")
    return source[start:end]


def _startup_source() -> str:
    source = ENTRYPOINT.read_text()
    start = source.index("# Run startup checks for services that need them")
    end = source.index("should_register_temporal_schedules()", start)
    return source[start:end]


def _run_guard(
    value: str | None,
    *,
    env_type: str = "development",
    service_type: str = "backend",
    mutation_mode: str | None = None,
    cloud_deployment: str | None = None,
    fast_startup: str = "false",
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["ENV_TYPE"] = env_type
    env["SERVICE_TYPE"] = service_type
    if value is None:
        env.pop("NO_STARTUP_DB_MUTATIONS", None)
    else:
        env["NO_STARTUP_DB_MUTATIONS"] = value
    if mutation_mode is None:
        env.pop("STARTUP_DB_MUTATION_MODE", None)
    else:
        env["STARTUP_DB_MUTATION_MODE"] = mutation_mode
    if cloud_deployment is None:
        env.pop("CLOUD_DEPLOYMENT", None)
    else:
        env["CLOUD_DEPLOYMENT"] = cloud_deployment
    return subprocess.run(
        ["bash"],
        input=(
            f"FAST_STARTUP={fast_startup}\n"
            f"{_guard_source()}\n"
            'printf "%s:%s:%s" "$NO_STARTUP_DB_MUTATIONS" "$FAST_STARTUP" '
            '"$STARTUP_DB_MUTATION_MODE"\n'
        ),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "false:false:disabled"),
        ("false", "false:false:disabled"),
        ("true", "true:false:disabled"),
    ],
)
def test_entrypoint_guard_development_default_and_explicit_modes(value, expected):
    completed = _run_guard(value)

    assert completed.returncode == 0
    assert completed.stdout.endswith(expected)


@pytest.mark.parametrize("value", ["", "TRUE", "False", " true ", "1", "yes"])
def test_entrypoint_guard_rejects_ambiguous_values(value):
    completed = _run_guard(value)

    assert completed.returncode == 64
    assert "must be exactly 'true' or 'false'" in completed.stdout


@pytest.mark.parametrize("env_type", ["prod", "production", "staging"])
def test_entrypoint_hosted_startup_defaults_to_mutation_free(env_type):
    completed = _run_guard(None, env_type=env_type)

    assert completed.returncode == 0
    assert completed.stdout.endswith("true:false:disabled")


@pytest.mark.parametrize("cloud_deployment", ["US", "EU", "DEV"])
def test_entrypoint_cloud_deployment_defaults_to_mutation_free(cloud_deployment):
    completed = _run_guard(None, cloud_deployment=cloud_deployment)

    assert completed.returncode == 0
    assert completed.stdout.endswith("true:false:disabled")


@pytest.mark.parametrize(
    ("service_type", "mutation_mode"),
    [
        ("backend", "disabled"),
        ("backend", "operator"),
        ("bootstrap", "disabled"),
        ("temporal-schedules", "disabled"),
    ],
)
def test_entrypoint_hosted_false_requires_dedicated_operator_job(
    service_type, mutation_mode
):
    completed = _run_guard(
        "false",
        env_type="production",
        service_type=service_type,
        mutation_mode=mutation_mode,
    )

    assert completed.returncode == 64
    assert "hosted database mutations require" in completed.stdout


def test_entrypoint_hosted_operator_bootstrap_is_explicitly_allowed():
    completed = _run_guard(
        None,
        env_type="production",
        service_type="bootstrap",
        mutation_mode="operator",
        fast_startup="true",
    )

    assert completed.returncode == 0
    assert completed.stdout.endswith("false:false:operator")


def test_entrypoint_hosted_operator_bootstrap_rejects_explicit_mutation_guard():
    completed = _run_guard(
        "true",
        env_type="production",
        service_type="bootstrap",
        mutation_mode="operator",
    )

    assert completed.returncode == 64
    assert (
        "operator bootstrap requires NO_STARTUP_DB_MUTATIONS=false" in completed.stdout
    )


@pytest.mark.parametrize("mutation_mode", [None, "disabled"])
def test_entrypoint_hosted_bootstrap_without_operator_mode_fails_instead_of_noop(
    mutation_mode,
):
    completed = _run_guard(
        None,
        env_type="production",
        service_type="bootstrap",
        mutation_mode=mutation_mode,
    )

    assert completed.returncode == 64
    assert (
        "hosted bootstrap requires STARTUP_DB_MUTATION_MODE=operator"
        in completed.stdout
    )


def test_entrypoint_rejects_unknown_mutation_mode():
    completed = _run_guard(None, mutation_mode="enabled")

    assert completed.returncode == 64
    assert "STARTUP_DB_MUTATION_MODE must be exactly" in completed.stdout


def test_entrypoint_mutation_guard_does_not_force_fast_startup():
    assert 'elif [ "$NO_STARTUP_DB_MUTATIONS" = "true" ]; then' not in _guard_source()
    assert "\n    FAST_STARTUP=true\n" not in _guard_source()


def test_entrypoint_mutation_free_full_startup_executes_only_read_only_setup():
    completed = subprocess.run(
        ["bash"],
        input=(
            "FAST_STARTUP=false\n"
            "NO_STARTUP_DB_MUTATIONS=true\n"
            "SERVICE_TYPE=backend\n"
            "ENV_TYPE=prod\n"
            'wait_for_db() { echo "READ:wait_for_db"; }\n'
            'collect_static() { echo "LOCAL:collect_static"; }\n'
            'validate_django() { echo "READ:validate_django"; }\n'
            'create_cache_table() { echo "MUTATION:create_cache_table"; }\n'
            'run_migrations() { echo "MUTATION:run_migrations"; }\n'
            'python() { echo "MUTATION:python $*"; }\n'
            f"{_startup_source()}\n"
        ),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "READ:wait_for_db" in completed.stdout
    assert "LOCAL:collect_static" in completed.stdout
    assert "READ:validate_django" in completed.stdout
    assert "MUTATION:" not in completed.stdout


def test_entrypoint_mutation_free_setup_and_schedule_registration_are_guarded():
    source = ENTRYPOINT.read_text()
    startup_boundary = source.index('if [ "$FAST_STARTUP" != "true" ]; then')
    schedule_function = source.index(
        "should_register_temporal_schedules()", startup_boundary
    )
    schedule_boundary = source.index(
        'if [ "$NO_STARTUP_DB_MUTATIONS" = "true" ]; then', schedule_function
    )

    assert startup_boundary < schedule_function < schedule_boundary
    startup_block = source[startup_boundary:schedule_function]
    assert "wait_for_db" in startup_block
    mutation_free_branch = startup_block.index(
        'if [ "$NO_STARTUP_DB_MUTATIONS" = "true" ]; then'
    )
    mutating_branch = startup_block.index("    else\n", mutation_free_branch)
    for mutation in ("create_cache_table", "run_migrations", "seed_system_evals"):
        assert mutation not in startup_block[mutation_free_branch:mutating_branch]
        assert mutation in startup_block[mutating_branch:]
    schedule_block = source[schedule_boundary : source.index("# Start the appropriate")]
    assert "skipping Temporal schedule registration" in schedule_block
    assert "python manage.py register_temporal_schedules" in schedule_block


def test_entrypoint_guard_defaults_by_environment_without_loose_expansion():
    source = ENTRYPOINT.read_text()

    assert "NO_STARTUP_DB_MUTATIONS:-true" not in source
    assert "CLOUD_STARTUP" in _guard_source()
    assert "NO_STARTUP_DB_MUTATIONS=true" in _guard_source()
    assert "NO_STARTUP_DB_MUTATIONS=false" in _guard_source()


def test_entrypoint_exposes_one_shot_bootstrap_service():
    source = ENTRYPOINT.read_text()

    assert '"backend"|"worker"|"beat"|"grpc"|"bootstrap")' in source
    assert (
        'if [ "$SERVICE_TYPE" = "backend" ] || [ "$SERVICE_TYPE" = "bootstrap" ]; then'
        in source
    )
    assert (
        '"bootstrap")\n        echo "One-shot database bootstrap completed successfully"'
        in source
    )
    assert "python manage.py seed_system_evals" in source


def test_entrypoint_mutation_free_backend_still_collects_static_assets():
    source = ENTRYPOINT.read_text()
    static_guard = source.index(
        'if [ "$FAST_STARTUP" = "true" ] && [ "$NO_STARTUP_DB_MUTATIONS" = "true" ] && [ "$SERVICE_TYPE" = "backend" ]; then'
    )

    assert "collect_static" in source[static_guard : static_guard + 220]


@pytest.mark.parametrize(
    ("service", "guard", "register", "result", "exit_code", "calls"),
    [
        ("bootstrap", "false", "true", 7, 1, 1),
        ("bootstrap", "false", "true", 0, 0, 1),
        ("backend", "false", "true", 7, 0, 1),
        ("bootstrap", "true", "true", 7, 0, 0),
        ("backend", "true", "true", 7, 0, 0),
        ("bootstrap", "false", "false", 7, 0, 0),
    ],
)
def test_single_bootstrap_registrar_cannot_succeed_without_schedules(
    service, guard, register, result, exit_code, calls
):
    source = ENTRYPOINT.read_text()
    block = source[
        source.index("should_register_temporal_schedules()") : source.index(
            "# Start the appropriate service"
        )
    ]
    completed = subprocess.run(
        ["bash"],
        input=(
            f"set -e\nSERVICE_TYPE={service}\nNO_STARTUP_DB_MUTATIONS={guard}\n"
            f"REGISTER_TEMPORAL_SCHEDULES={register}\n"
            f'python() {{ echo "SCHEDULE_CALL:$*"; return {result}; }}\n'
            f'{block}\necho "CONTINUED"\n'
        ),
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert completed.returncode == exit_code
    assert (
        completed.stdout.count("SCHEDULE_CALL:manage.py register_temporal_schedules")
        == calls
    )
    assert ("CONTINUED" in completed.stdout) == (exit_code == 0)


def _run_schedule_registrar(
    *, guard: str, result: int = 0, register: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run registration and the service dispatch exactly as the image does."""
    source = ENTRYPOINT.read_text()
    block = source[source.index("should_register_temporal_schedules()") :]
    register_line = (
        "" if register is None else f"REGISTER_TEMPORAL_SCHEDULES={register}\n"
    )
    return subprocess.run(
        ["bash"],
        input=(
            f"set -e\nSERVICE_TYPE=temporal-schedules\nENV_TYPE=prod\n"
            f"NO_STARTUP_DB_MUTATIONS={guard}\n{register_line}"
            f'python() {{ echo "SCHEDULE_CALL:$*"; return {result}; }}\n'
            f"{block}\n"
        ),
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )


@pytest.mark.parametrize("guard", ["true", "false"])
@pytest.mark.parametrize("register", [None, "true", "false"])
def test_schedule_registrar_registers_every_deploy_without_db_mutations(
    guard, register
):
    # Production pods run with NO_STARTUP_DB_MUTATIONS=true, which is why
    # sweep-stranded-eval-tasks (v1.41.2) was never created there. The
    # one-shot registrar must register regardless, and exactly once.
    completed = _run_schedule_registrar(guard=guard, register=register)

    assert completed.returncode == 0, completed.stdout
    assert completed.stdout.count("SCHEDULE_CALL:") == 1
    assert "SCHEDULE_CALL:manage.py register_temporal_schedules\n" in completed.stdout
    assert "Temporal schedule registration completed" in completed.stdout
    assert "Unknown SERVICE_TYPE" not in completed.stdout


def test_schedule_registrar_fails_the_job_when_registration_fails():
    completed = _run_schedule_registrar(guard="true", result=7)

    assert completed.returncode == 1
    assert "Temporal schedule registration failed" in completed.stdout
    assert "Temporal schedule registration completed" not in completed.stdout


@pytest.mark.parametrize("guard", ["true", "false"])
def test_schedule_registrar_startup_touches_no_database(guard):
    completed = subprocess.run(
        ["bash"],
        input=(
            "FAST_STARTUP=false\n"
            f"NO_STARTUP_DB_MUTATIONS={guard}\n"
            "SERVICE_TYPE=temporal-schedules\n"
            "ENV_TYPE=prod\n"
            'wait_for_db() { echo "DB:wait_for_db"; }\n'
            'collect_static() { echo "DB:collect_static"; }\n'
            'validate_django() { echo "DB:validate_django"; }\n'
            'create_cache_table() { echo "DB:create_cache_table"; }\n'
            'run_migrations() { echo "DB:run_migrations"; }\n'
            'python() { echo "DB:python $*"; }\n'
            f"{_startup_source()}\n"
        ),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "DB:" not in completed.stdout


def test_backend_forwards_sigterm_so_granian_drains_in_flight_requests(tmp_path):
    # The runtime signals only PID 1 (this script). A foreground granian never
    # saw SIGTERM: bash defers its trap until the child exits, so the pod was
    # SIGKILLed with requests in flight at the end of the grace period.
    stub = tmp_path / "granian"
    stub.write_text(
        "#!/bin/bash\n"
        "trap 'echo GRANIAN_TERM; exit 0' TERM\n"
        "echo GRANIAN_UP\n"
        "for _ in $(seq 1 50); do sleep 0.1; done\n"
        "echo GRANIAN_EXITED_UNSIGNALLED\n"
    )
    stub.chmod(0o755)
    source = ENTRYPOINT.read_text()
    script = tmp_path / "backend.sh"
    script.write_text(
        "set -e\nSERVICE_TYPE=backend\nENV_TYPE=prod\nNO_STARTUP_DB_MUTATIONS=true\n"
        "ENABLE_HTTP=true\nENABLE_GRPC=false\nGRANIAN_WORKERS=1\nGRANIAN_THREADS=1\n"
        f"{source[source.index('should_register_temporal_schedules()') :]}"
    )
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"}
    process = subprocess.Popen(
        ["bash", str(script)], stdout=subprocess.PIPE, text=True, env=env
    )
    try:
        for line in process.stdout:
            if line.strip() == "GRANIAN_UP":
                break
        time.sleep(0.3)  # let the script reach its wait, as a pod would
        process.send_signal(signal.SIGTERM)
        output, _ = process.communicate(timeout=10)
    finally:
        process.kill()

    assert "GRANIAN_TERM" in output
    assert "GRANIAN_EXITED_UNSIGNALLED" not in output
    assert process.returncode == 0
