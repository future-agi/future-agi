import os
import subprocess
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parents[2] / "entrypoint.sh"


def _guard_source() -> str:
    source = ENTRYPOINT.read_text()
    start = source.index(
        "# Production keeps the existing startup path unless an isolated read-only job"
    )
    end = source.index("# Disable bytecode compilation")
    return source[start:end]


def _run_guard(value: str | None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if value is None:
        env.pop("NO_STARTUP_DB_MUTATIONS", None)
    else:
        env["NO_STARTUP_DB_MUTATIONS"] = value
    return subprocess.run(
        ["bash"],
        input=(
            "FAST_STARTUP=false\n"
            f"{_guard_source()}\n"
            'printf "%s:%s" "$NO_STARTUP_DB_MUTATIONS" "$FAST_STARTUP"\n'
        ),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, "false:false"), ("false", "false:false"), ("true", "true:true")],
)
def test_entrypoint_guard_default_and_explicit_modes(value, expected):
    completed = _run_guard(value)

    assert completed.returncode == 0
    assert completed.stdout.endswith(expected)


@pytest.mark.parametrize("value", ["", "TRUE", "False", " true ", "1", "yes"])
def test_entrypoint_guard_rejects_ambiguous_values(value):
    completed = _run_guard(value)

    assert completed.returncode == 64
    assert "must be exactly 'true' or 'false'" in completed.stdout


def test_entrypoint_true_bypasses_all_mutating_setup_and_schedule_registration():
    source = ENTRYPOINT.read_text()
    guard = source.index('if [ "$NO_STARTUP_DB_MUTATIONS" = "true" ]; then')
    startup_boundary = source.index('if [ "$FAST_STARTUP" != "true" ]; then', guard)
    schedule_boundary = source.index(
        'if [ "$NO_STARTUP_DB_MUTATIONS" = "true" ]; then', startup_boundary
    )

    assert guard < startup_boundary < schedule_boundary
    startup_block = source[startup_boundary:schedule_boundary]
    for mutation in (
        "wait_for_db",
        "create_cache_table",
        "run_migrations",
        "collect_static",
    ):
        assert mutation in startup_block
    schedule_block = source[schedule_boundary : source.index("# Start the appropriate")]
    assert "skipping Temporal schedule registration" in schedule_block
    assert "python manage.py register_temporal_schedules" in schedule_block


def test_entrypoint_guard_is_opt_in_not_default_true():
    source = ENTRYPOINT.read_text()

    assert "NO_STARTUP_DB_MUTATIONS:-true" not in source
    assert "NO_STARTUP_DB_MUTATIONS=false" in _guard_source()
