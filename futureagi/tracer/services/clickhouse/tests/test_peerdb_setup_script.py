"""Execute the real setup shell with psql intercepted; no services or SQL writes."""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[4] / "scripts/peerdb-setup-mirrors.sh"
STUB = r"""
psql() {
    local sql="${@: -1}" stop=0 target=probe
    case " $* " in *" ON_ERROR_STOP=1 "*) stop=1 ;; esac
    case "$sql" in
        *"CREATE PEER IF NOT EXISTS pg_source"*) target=source ;;
        *"CREATE PEER IF NOT EXISTS ch_dest"*) target=destination ;;
        *"CREATE MIRROR IF NOT EXISTS mirror_tracer_trace"*) target=trace ;;
        *"CREATE MIRROR IF NOT EXISTS mirror_tracer_enduser"*) target=last ;;
        *"CREATE MIRROR"*) target=mirror ;;
    esac
    printf 'CALL:%s:stop=%s\n' "$target" "$stop"
    if [ "$target" = "$FAIL_TARGET" ]; then
        printf 'ERROR: synthetic SQL refusal\n' >&2
        # Like psql: server SQL errors need ON_ERROR_STOP to be nonzero.
        if [ "$stop" = 1 ]; then return 3; fi
    fi
}
source "$1"
"""


def run_setup(fail="", retired="true"):
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", STUB, "offline-setup", str(SCRIPT)],
        env={
            "PATH": os.defpath,
            "FAIL_TARGET": fail,
            "CH25_DROP_LEGACY_CDC_CHAIN": retired,
        },
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )


@pytest.mark.parametrize("failure", ["source", "destination", "trace", "last"])
def test_sql_failure_stops_setup_without_success_or_cleanup(failure):
    result = run_setup(failure)
    output = result.stdout + result.stderr
    assert "synthetic SQL refusal" in output
    assert result.returncode != 0
    assert "==> Done!" not in output
    calls = [line for line in result.stdout.splitlines() if line.startswith("CALL:")]
    assert calls[-1] == f"CALL:{failure}:stop=1"
    assert all("stop=1" in call for call in calls)


@pytest.mark.parametrize("retired", ["true", "TRUE", "True", "1", "yes", "on"])
def test_success_requests_all_twenty_cdc_mirrors_without_snapshot(retired):
    result = run_setup(retired=retired)
    assert result.returncode == 0, result.stderr
    calls = [line for line in result.stdout.splitlines() if line.startswith("CALL:")]
    assert len(calls) == 22  # two peers and twenty mirrors; probe is redirected
    assert all("stop=1" in call for call in calls)
    assert "CDC-only, no initial snapshot" in result.stdout
    assert "==> Done!" in result.stdout


def test_explicit_legacy_mode_preserves_existing_setup_behavior():
    result = run_setup(retired="false")
    assert result.returncode == 0, result.stderr
    calls = [line for line in result.stdout.splitlines() if line.startswith("CALL:")]
    assert len(calls) == 23
