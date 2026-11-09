"""Exercise the real installer secret writers on synthetic .env files only.

No Docker, services, database, or credentials. Both installer implementations
run the same cases; PowerShell cases skip on hosts without pwsh.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from dotenv import dotenv_values
from dotenv.parser import parse_stream

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "bin/lib"
CURRENT = "FI_API_SECRET_PEPPER"
RETIRED = "FI_API_SECRET_PEPPERS_RETIRED"
PUBLISHED = "local-dev-only-api-secret-pepper-replace-me"
NOTICE = "Rotated FI_API_SECRET_PEPPER (published default moved to retired)"
# Keep unrelated secrets populated so no-op tests can require byte-for-byte
# stability, including the file timestamp. All values here are synthetic.
BASE = """# Keep this comment and unrelated settings.
SECRET_KEY=synthetic-login-secret
PG_PASSWORD=synthetic-postgres-password
MINIO_ROOT_PASSWORD=synthetic-minio-password
AGENTCC_INTERNAL_API_KEY=synthetic-gateway-key
CH_PASSWORD=synthetic-clickhouse-password
INTEGRATION_ENCRYPTION_KEY=synthetic-existing-encryption-key
AGENTCC_WEBHOOK_SECRET=synthetic-webhook-secret
AGENTCC_ADMIN_TOKEN=synthetic-admin-token
OMEGA_HUB_CHECKER_TOKEN=synthetic-checker-token
REDIS_PASSWORD=synthetic-redis-password
"""


@pytest.fixture(params=["bash", "powershell"])
def writer(request):
    if request.param == "powershell" and shutil.which("pwsh") is None:
        pytest.skip("pwsh is unavailable")
    return request.param


def seed(directory, current=None, retired=None):
    content = BASE
    if current is not None:
        content += f"{CURRENT}={current}\n"
    if retired is not None:
        content += f"{RETIRED}={retired}\n"
    path = directory / ".env"
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    return path


def values(path):
    # Check effective dotenv values, not raw assignment text: appending after a
    # comment/closing quote can look correct in text while dropping the pepper.
    with path.open(encoding="utf-8") as stream:
        assert not any(binding.error for binding in parse_stream(stream))
    return dict(dotenv_values(path, interpolate=False))


def run_writer(directory, writer, *, fresh=False, fail_generator=False, inherited=None):
    if writer == "bash":
        script = f"""
set -eEuo pipefail
umask 022
. '{LIB / "env.sh"}'
. '{LIB / "secrets.sh"}'
exec 3>install.log
ok() {{ printf '%s\\n' "$*"; printf '%s\\n' "$*" >&3; }}
warn() {{ printf '%s\\n' "$*" >&2; printf '%s\\n' "$*" >&3; }}
preflight_fail() {{ exit 90; }}
docker() {{ exit 91; }}
clickhouse_running_password() {{ return 1; }}
"""
        if fail_generator:
            script += "gen_secret() { return 42; }\n"
        script += f"write_secrets {int(fresh)} 0 futureagi\n"
        command = ["bash", "-c", script]
    else:
        script = f"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3.0
. '{LIB / "env.ps1"}'
. '{LIB / "secrets.ps1"}'
function Ok {{ param($m) Write-Output $m; Add-Content install.log $m }}
function Warn {{ param($m) Write-Output $m; Add-Content install.log $m }}
function Fail-Preflight {{ throw 'Unexpected preflight' }}
function docker {{ throw 'Docker must not run' }}
function Get-ClickHouseRunningPassword {{ return $null }}
"""
        if fail_generator:
            script += "function New-HexSecret { throw 'Generator failed' }\n"
        script += f"Write-Secrets ${str(fresh).lower()} $false futureagi\n"
        command = ["pwsh", "-NoProfile", "-NonInteractive", "-Command", script]
    return subprocess.run(
        command,
        cwd=directory,
        env={
            **{k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "TMPDIR")},
            **(inherited or {}),
        },
        capture_output=True,
        text=True,
        timeout=15,
    )


def assert_rotated(directory, result, *, retired=PUBLISHED):
    assert result.returncode == 0, result.stderr
    path = directory / ".env"
    after = values(path)
    assert re.fullmatch(r"[0-9a-f]{64}", after.get(CURRENT, "")), (
        "new 32-byte pepper missing"
    )
    assert after[RETIRED] == retired
    assert path.read_text(encoding="utf-8").startswith(BASE)
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    output = result.stdout + result.stderr + (directory / "install.log").read_text()
    assert NOTICE in result.stdout
    for value in [after[CURRENT], *retired.split(","), *values_from_base()]:
        assert value not in output, "secret value was logged"
    return after


def values_from_base():
    return [line.split("=", 1)[1] for line in BASE.splitlines() if "=" in line]


def test_existing_unset_pepper_is_rotated(tmp_path, writer):
    seed(tmp_path)
    assert_rotated(tmp_path, run_writer(tmp_path, writer))


def test_existing_empty_pepper_is_rotated(tmp_path, writer):
    seed(tmp_path, "")
    assert_rotated(tmp_path, run_writer(tmp_path, writer))


def test_existing_published_default_is_rotated(tmp_path, writer):
    seed(tmp_path, PUBLISHED)
    assert_rotated(tmp_path, run_writer(tmp_path, writer))


@pytest.mark.parametrize(
    "custom",
    [
        "a" * 64,
        "CHANGEME-operator-pepper",
        PUBLISHED.upper(),
        PUBLISHED + "-custom",
        "' " + PUBLISHED + "'",
        '"' + PUBLISHED + '-custom"',
        "#operator-pepper",
    ],
    ids=[
        "random",
        "placeholder-prefix",
        "different-case",
        "suffix",
        "leading-space",
        "quoted",
        "leading-hash",
    ],
)
def test_existing_custom_pepper_is_never_touched(tmp_path, writer, custom):
    path = seed(tmp_path, custom, "existing-retired,existing-retired")
    before, modified = path.read_bytes(), path.stat().st_mtime_ns
    result = run_writer(tmp_path, writer)
    assert result.returncode == 0, result.stderr
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == modified
    assert NOTICE not in result.stdout + result.stderr
    assert custom not in result.stdout + result.stderr


def test_rerun_changes_nothing(tmp_path, writer):
    path = seed(tmp_path, PUBLISHED)
    assert_rotated(tmp_path, run_writer(tmp_path, writer))
    before, modified = path.read_bytes(), path.stat().st_mtime_ns
    result = run_writer(tmp_path, writer)
    assert result.returncode == 0, result.stderr
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == modified
    assert NOTICE not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "retired,expected",
    [
        ("", PUBLISHED),
        ("first,second", f"first,second,{PUBLISHED}"),
        (f"first,{PUBLISHED},second", f"first,{PUBLISHED},second"),
        (f"first,{PUBLISHED},first,{PUBLISHED},second", f"first,{PUBLISHED},second"),
        (f" first , , {PUBLISHED} , second ,", f"first,{PUBLISHED},second"),
        (f"{PUBLISHED.upper()},first", f"{PUBLISHED.upper()},first,{PUBLISHED}"),
        (
            r"old&pepper|with\literal,second",
            rf"old&pepper|with\literal,second,{PUBLISHED}",
        ),
    ],
    ids=[
        "empty",
        "append",
        "already-retired",
        "dedupe",
        "whitespace",
        "case-sensitive",
        "literal",
    ],
)
def test_retired_peppers_keep_order_and_are_deduped(
    tmp_path, writer, retired, expected
):
    seed(tmp_path, PUBLISHED, retired)
    assert_rotated(tmp_path, run_writer(tmp_path, writer), retired=expected)


@pytest.mark.parametrize("current", [None, "", "CHANGEME-set-by-bin-install"])
def test_fresh_install_generates_without_retiring_default(tmp_path, writer, current):
    path = seed(tmp_path, current)
    result = run_writer(tmp_path, writer, fresh=True)
    assert result.returncode == 0, result.stderr
    after = values(path)
    assert re.fullmatch(r"[0-9a-f]{64}", after.get(CURRENT, ""))
    assert RETIRED not in after
    assert NOTICE not in result.stdout + result.stderr
    assert after[CURRENT] not in result.stdout + result.stderr


def test_fresh_custom_pepper_is_unchanged(tmp_path, writer):
    path = seed(tmp_path, "a" * 64, "retired-one")
    before = path.read_bytes()
    result = run_writer(tmp_path, writer, fresh=True)
    assert result.returncode == 0, result.stderr
    assert path.read_bytes() == before


def test_generator_failure_does_not_modify_env(tmp_path, writer):
    path = seed(tmp_path, PUBLISHED, "retired-one")
    before = path.read_bytes()
    result = run_writer(tmp_path, writer, fail_generator=True)
    assert result.returncode != 0
    assert path.read_bytes() == before
    assert NOTICE not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "raw",
    [
        f"'{PUBLISHED}'",
        f'"{PUBLISHED}"',
        f"{PUBLISHED} # old default",
        f" {PUBLISHED} ",
    ],
    ids=["single-quoted", "double-quoted", "commented", "padding"],
)
def test_effective_published_active_value_is_rotated(tmp_path, writer, raw):
    seed(tmp_path, raw)
    assert_rotated(tmp_path, run_writer(tmp_path, writer))


@pytest.mark.parametrize(
    "raw,decoded",
    [
        ("previous-pepper # keep until rehashed", "previous-pepper"),
        ("'first,second,first'", "first,second"),
        ('"first,second,first"', "first,second"),
        ("'first,second' # keep until rehashed", "first,second"),
        ('"first,second" # keep until rehashed', "first,second"),
        ("'old # literal,first,old # literal'", "old # literal,first"),
        ("'old$LITERAL,${UNCHANGED}'", "old$LITERAL,${UNCHANGED}"),
        (r"'old\literal,second'", r"old\literal,second"),
        ("old#literal,second", "old#literal,second"),
        (" # leading hash is a literal value", "# leading hash is a literal value"),
        ("'' # no retired peppers yet", ""),
    ],
    ids=[
        "inline-comment",
        "single-quoted",
        "double-quoted",
        "single-with-comment",
        "double-with-comment",
        "literal-hash",
        "literal-dollar",
        "literal-backslash",
        "unquoted-hash",
        "leading-hash",
        "empty-quoted",
    ],
)
def test_retired_dotenv_syntax_preserves_effective_peppers(
    tmp_path, writer, raw, decoded
):
    path = seed(tmp_path, PUBLISHED, raw)
    before_entries = [
        part.strip() for part in values(path)[RETIRED].split(",") if part.strip()
    ]
    expected = f"{decoded},{PUBLISHED}" if decoded else PUBLISHED
    after = assert_rotated(tmp_path, run_writer(tmp_path, writer), retired=expected)
    assert all(part in after[RETIRED].split(",") for part in before_entries)
    # Every effective pepper remains available, including the previously active
    # default. Both verifier implementations split this comma-separated value.
    assert PUBLISHED in after[RETIRED].split(",")
    before, modified = path.read_bytes(), path.stat().st_mtime_ns
    assert run_writer(tmp_path, writer).returncode == 0
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == modified


@pytest.mark.parametrize(
    "raw",
    [
        "${EXTERNAL_RETIRED}",
        '"$EXTERNAL_RETIRED"',
        r'"old\npepper"',
        "'unterminated",
        "'first\nsecond'",
        "'first' unexpected-tail",
        r"'old\'pepper'",
        "old'pepper",
        "old\\",
    ],
    ids=[
        "interpolation",
        "quoted-interpolation",
        "escape",
        "unterminated",
        "multiline",
        "invalid-tail",
        "escaped-quote",
        "literal-quote",
        "trailing-backslash",
    ],
)
def test_unsafe_retired_syntax_fails_before_rotation(tmp_path, writer, raw):
    path = seed(tmp_path, PUBLISHED, raw)
    before, modified = path.read_bytes(), path.stat().st_mtime_ns
    result = run_writer(tmp_path, writer)
    assert result.returncode != 0
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == modified
    assert RETIRED in result.stdout + result.stderr
    assert NOTICE not in result.stdout + result.stderr
    assert raw not in result.stdout + result.stderr


@pytest.mark.parametrize("var", [CURRENT, RETIRED])
@pytest.mark.parametrize("prefix", ["export ", " "])
def test_noncanonical_pepper_assignment_refuses_instead_of_shadowing(
    tmp_path, writer, var, prefix
):
    path = seed(tmp_path, PUBLISHED, "first")
    path.write_text(path.read_text().replace(f"{var}=", f"{prefix}{var}="))
    before = path.read_bytes()
    result = run_writer(tmp_path, writer)
    assert result.returncode != 0
    assert path.read_bytes() == before
    assert var in result.stdout + result.stderr


@pytest.mark.parametrize(
    "var,override",
    [
        (CURRENT, ""),
        (CURRENT, PUBLISHED),
        (RETIRED, ""),
        (RETIRED, PUBLISHED),
        (RETIRED, "other-retired"),
        (RETIRED, f"first,{PUBLISHED}"),
    ],
)
def test_inherited_override_refuses_rotation_without_writing(
    tmp_path, writer, var, override
):
    path = seed(tmp_path, PUBLISHED, "first")
    before, modified = path.read_bytes(), path.stat().st_mtime_ns
    result = run_writer(tmp_path, writer, inherited={var: override})
    assert result.returncode != 0
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == modified
    output = result.stdout + result.stderr
    assert var in output
    assert "shell" in output.lower()
    assert NOTICE not in output
    assert PUBLISHED not in output
    assert "other-retired" not in output


@pytest.mark.parametrize("current", [None, PUBLISHED, "file-custom"])
def test_inherited_custom_active_is_never_changed(tmp_path, writer, current):
    path = seed(tmp_path, current, "first")
    before, modified = path.read_bytes(), path.stat().st_mtime_ns
    override = "synthetic-exported-custom"
    result = run_writer(
        tmp_path, writer, inherited={CURRENT: override, RETIRED: "shell-retired"}
    )
    assert result.returncode == 0, result.stderr
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == modified
    assert NOTICE not in result.stdout + result.stderr
    assert override not in result.stdout + result.stderr


@pytest.mark.parametrize("override", ["", PUBLISHED])
def test_inherited_default_cannot_hide_file_custom_active(tmp_path, writer, override):
    path = seed(tmp_path, "file-custom", "first")
    before = path.read_bytes()
    result = run_writer(tmp_path, writer, inherited={CURRENT: override})
    assert result.returncode != 0
    assert path.read_bytes() == before
    assert CURRENT in result.stdout + result.stderr
    assert "file-custom" not in result.stdout + result.stderr


def test_powershell_generates_the_same_fresh_install_pepper():
    # Also catches missing membership on CI hosts without PowerShell.
    script = (LIB / "secrets.ps1").read_text(encoding="utf-8")
    array = re.search(r"\$installSecrets = @\(([^)]*)\)", script)
    assert array and f"'{CURRENT}'" in array.group(1)
