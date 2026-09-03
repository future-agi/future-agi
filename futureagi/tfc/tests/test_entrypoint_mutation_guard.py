import os
import subprocess
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
    [("backend", "disabled"), ("backend", "operator"), ("bootstrap", "disabled")],
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
