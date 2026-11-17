"""How bin/install and bin/install.ps1 start the Distributed stack
(bin/lib/startup.sh and bin/lib/startup.ps1): ONE `compose up`, in the
background, while the installer watches the schema migrations in the one-shot
postgres-schema-bootstrap service. The PowerShell cases skip where pwsh is not
installed.

An upgrade of a large database applies migrations for longer than any fixed
Compose wait (one upgrade applied 185 migrations over about 40 minutes, against
the old `--wait-timeout 1200`), and the app services wait for the bootstrap. So the
installer prints a progress line at least every 60 s while it runs, and fails
only when it exits non-zero (naming it and its logs command), when
INSTALL_MIGRATION_TIMEOUT_SECONDS passes, or when the rest of the stack misses
its own 1200 s bound after the migrations finished. A second `compose up`
would start the exited bootstrap again, so every case checks there is exactly
one, and that none outlives the installer.

Each case runs the installer's own lines, extracted as written: the bounded
integer helper, the INSTALL_MIGRATION_TIMEOUT_SECONDS read and the start call
with its failure message. `docker` is a stub on PATH whose bootstrap container
follows a fake clock; the library's poll sleep, replaced here, advances that
clock, so a 600 s limit passes in about a second. 600 is the setting's floor:
a smaller value is refused (tested), so the limit is tested at its floor on
fake time rather than with a tiny value.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = "postgres-schema-bootstrap"
# The installers' own message when `compose up` fails for any other reason.
GENERIC_FAILURE = "docker compose startup failed or timed out"
TIMEOUT = 300

# The installers' lines each case runs, as written.
SHELL_PIECES = {
    "BOUNDED": r"(?ms)^bounded_env_int\(\) \{$.*?^\}$",
    "MIGRATION": r"(?m)^  bounded_env_int INSTALL_MIGRATION_TIMEOUT_SECONDS .*\n  migration_timeout=.*$",
    "START": r"(?ms)^  if ! start_distributed_stack .*?^  fi$",
}
POWERSHELL_PIECES = {
    "BOUNDED": r"(?ms)^function Get-BoundedEnvInt \{$.*?^\}$",
    "MIGRATION": r"(?m)^  \$migrationTimeout = Get-BoundedEnvInt .*$",
    "START": r"(?ms)^  \$started = Start-DistributedStack .*?^  \}$",
}

# The stub `docker`. The clock is the number in $FAKE_CLOCK. Before BOOT_CREATED
# the bootstrap has no container, or with OLD_BOOT a previous run's one that
# exited OLD_EXIT; then it is created, runs from BOOT_START, and exits BOOT_EXIT
# at BOOT_END (never, when unset). `up` records its arguments and PID, then
# returns UP_RC once the clock reaches UP_RETURNS_AT; without that it waits to
# be stopped, up to a real-time ceiling after which it leaves a .leaked marker,
# so that a leak fails the case instead of hanging it.
STUB_DOCKER = r"""#!/usr/bin/env bash
set -u
clock() { local now; now=$(cat "$FAKE_CLOCK" 2>/dev/null); printf '%s' "${now:-0}"; }
boot_state() {
  local now
  now=$(clock)
  if (( now < ${BOOT_CREATED:-0} )); then
    if [[ -n "${OLD_BOOT:-}" ]]; then printf 'old0000|exited|%s|2026-11-01T00:00:00Z' "${OLD_EXIT:-0}"; fi
    return 0
  fi
  if (( now < ${BOOT_START:-0} )); then printf 'new1111|created|0|0001-01-01T00:00:00Z'; return 0; fi
  if [[ -n "${BOOT_END:-}" ]] && (( now >= BOOT_END )); then
    printf 'new1111|exited|%s|started-%s' "${BOOT_EXIT:-0}" "${BOOT_START:-0}"
    return 0
  fi
  printf 'new1111|running|0|started-%s' "${BOOT_START:-0}"
}
args="$*"
if [[ "${1:-}" == compose ]]; then
  shift
  if [[ "${1:-}" == --ansi ]]; then shift 2; fi
  case "${1:-}" in
    up)
      printf '%s\n' "$args" >> "$UP_LOG"
      printf '%s\n' "$$" >> "$UP_PIDS"
      ceiling=$(( $(date +%s) + ${UP_REAL_CEILING:-120} ))
      while (( $(date +%s) < ceiling )); do
        if [[ -n "${UP_RETURNS_AT:-}" ]] && (( $(clock) >= UP_RETURNS_AT )); then exit "${UP_RC:-0}"; fi
        sleep 0.02
      done
      : > "$UP_LOG.leaked"
      exit 99 ;;
    ps)
      if [[ " $* " == *" -q "* ]]; then
        state=$(boot_state)
        if [[ -n "$state" && "${!#}" == postgres-schema-bootstrap ]]; then printf '%s\n' "${state%%|*}"; fi
      else
        printf 'NAME STATUS (stub ps)\n'
      fi
      exit 0 ;;
    logs) printf 'stub log line for %s\n' "${!#}"; exit 0 ;;
    *) exit 0 ;;
  esac
fi
if [[ "${1:-}" == inspect ]]; then
  state=$(boot_state)
  if [[ -n "$state" && "${state%%|*}" == "${!#}" ]]; then printf '%s\n' "${state#*|}"; exit 0; fi
  printf 'Error: No such object: %s\n' "${!#}" >&2
  exit 1
fi
exit 1
"""

# bin/install's shell options, ERR trap and output helpers (which print no
# colours), then its own lines. The poll sleep advances the fake clock and
# pauses briefly in real time so the background stub sees it.
SHELL = r"""
set -eEuo pipefail
on_err() { local code=$?; printf 'err-trap: line %s\n' "$1" >&2; exit "$code"; }
trap 'on_err $LINENO' ERR
DIM='' RESET=''
say() { printf 'say: %s\n' "$*"; }
ok() { printf 'ok: %s\n' "$*"; }
warn() { printf 'warn: %s\n' "$*" >&2; }
die() { printf 'die: %s\n' "$*" >&2; exit 4; }
log_only() { printf '%s\n' "$*" >&3; }
exec 3>>"$INSTALL_LOG"
DC="docker compose"
. "$FI_ROOT/bin/lib/env.sh"
. "$FI_ROOT/bin/lib/startup.sh"
startup_now() { cat "$FAKE_CLOCK"; }
startup_sleep() {
  local next
  next=$(( $(cat "$FAKE_CLOCK") + $1 ))
  printf '%s\n' "$next" > "$FAKE_CLOCK.next"
  mv -f "$FAKE_CLOCK.next" "$FAKE_CLOCK"
  sleep 0.01
}
@BOUNDED@
build_flag=--build
@MIGRATION@
@START@
printf 'stack started\n'
"""

# bin/install.ps1's preferences, output helpers and Invoke-Probe, then its own
# lines, with the same fake clock.
POWERSHELL = r"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3.0
function Say  { param([string]$Msg) Write-Host "say: $Msg" }
function Ok   { param([string]$Msg) Write-Host "ok: $Msg" }
function Warn { param([string]$Msg) [Console]::Error.WriteLine("warn: $Msg") }
function Die  { param([string]$Msg) [Console]::Error.WriteLine("die: $Msg"); exit 4 }
function Append-Log { param([string[]]$Lines) Add-Content -LiteralPath $env:INSTALL_LOG -Value $Lines }
function Invoke-Probe {
  param([scriptblock]$Command)
  $saved = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try { & $Command 2>$null } finally { $ErrorActionPreference = $saved }
}
$DcCmd = 'docker'
$DcArgs = @('compose')
. (Join-Path $env:FI_ROOT 'bin/lib/env.ps1')
. (Join-Path $env:FI_ROOT 'bin/lib/startup.ps1')
function Get-StartupNow { [int64]((Get-Content -LiteralPath $env:FAKE_CLOCK -Raw).Trim()) }
function Invoke-StartupSleep {
  param([int]$Seconds)
  $next = (Get-StartupNow) + $Seconds
  Set-Content -LiteralPath "$($env:FAKE_CLOCK).next" -Value "$next" -NoNewline
  Move-Item -LiteralPath "$($env:FAKE_CLOCK).next" -Destination $env:FAKE_CLOCK -Force
  Start-Sleep -Milliseconds 10
}
@BOUNDED@
$buildFlag = '--build'
@MIGRATION@
@START@
Write-Host 'stack started'
"""


def piece(installer: str, pattern: str) -> str:
    matches = re.findall(pattern, (ROOT / installer).read_text(encoding="utf-8"))
    if len(matches) != 1:
        raise AssertionError(f"{installer}: {len(matches)} matches for {pattern}, expected 1")
    return matches[0]


def fill(template: str, installer: str, pieces: dict[str, str]) -> str:
    for name, pattern in pieces.items():
        template = template.replace(f"@{name}@", piece(installer, pattern))
    return template


def squashed(text: str) -> str:
    """Output without colour codes or whitespace: pwsh may wrap a message."""
    return re.sub(r"\s+", "", re.sub(r"\x1b\[[0-9;]*m", "", text))


def progress_minutes(stdout: str) -> list[int]:
    return [int(m) for m in re.findall(r"Migrationsstillrunning\((\d+)min\)", squashed(stdout))]


class StartupWaitCases:
    """The cases both installers must pass; subclasses run one of them."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.scratch = Path(temporary.name)
        self.project = self.scratch / "project"
        self.project.mkdir()
        self.stub_dir = self.scratch / "stub-bin"
        self.stub_dir.mkdir()
        stub = self.stub_dir / "docker"
        stub.write_text(STUB_DOCKER, encoding="utf-8")
        stub.chmod(0o755)
        self.clock = self.scratch / "clock"
        self.up_log = self.scratch / "up.log"
        self.up_pids = self.scratch / "up.pids"
        self.install_log = self.scratch / "install.log"

    def run_start(self, env: dict[str, str]) -> subprocess.CompletedProcess:
        raise NotImplementedError

    def start(self, dotenv: str = "", **scenario: int | str) -> subprocess.CompletedProcess:
        (self.project / ".env").write_text(dotenv, encoding="utf-8")
        self.clock.write_text("0", encoding="utf-8")
        for path in (self.up_log, self.up_pids, Path(f"{self.up_log}.leaked"), self.install_log):
            path.unlink(missing_ok=True)
        keep = ("HOME", "TMPDIR", "LANG")
        env = {key: value for key, value in os.environ.items() if key in keep}
        env.update({
            "PATH": f"{self.stub_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "FI_ROOT": str(ROOT),
            "FAKE_CLOCK": str(self.clock),
            "UP_LOG": str(self.up_log),
            "UP_PIDS": str(self.up_pids),
            "INSTALL_LOG": str(self.install_log),
            **{key: str(value) for key, value in scenario.items()},
        })
        return self.run_start(env)

    def ups(self) -> list[str]:
        return self.up_log.read_text(encoding="utf-8").splitlines() if self.up_log.exists() else []

    def assert_one_up_and_none_left(self) -> None:
        self.assertEqual(len(self.ups()), 1, f"compose up ran {len(self.ups())} times")
        self.assertFalse(Path(f"{self.up_log}.leaked").exists(), "a compose up outlived the installer")
        pids = self.up_pids.read_text(encoding="utf-8").split() if self.up_pids.exists() else []
        for pid in pids:
            with self.assertRaises(ProcessLookupError, msg=f"compose up {pid} is still running"):
                os.kill(int(pid), 0)

    def tail(self, result: subprocess.CompletedProcess) -> str:
        return f"exit {result.returncode}\n{result.stdout[-1500:]}\n{result.stderr[-1500:]}"

    def test_migrations_running_for_several_polls_then_finishing(self):
        # Built until 5, created, running 10..200 (190 s), and compose up returns at 230.
        result = self.start(BOOT_CREATED=5, BOOT_START=10, BOOT_END=200, UP_RETURNS_AT=230)
        self.assertEqual(result.returncode, 0, self.tail(result))
        out = squashed(result.stdout)
        self.assertIn("stackstarted", out)
        self.assertEqual(out.count(squashed(f"Schema migrations started ({BOOTSTRAP})")), 1)
        minutes = progress_minutes(result.stdout)
        self.assertGreaterEqual(len(minutes), 190 // 60, "fewer than one progress line a minute")
        self.assertEqual(minutes, sorted(minutes))
        self.assertIn(squashed(
            "Migrations still running (1 min). Large databases take longer; "
            "waiting up to 240 min (INSTALL_MIGRATION_TIMEOUT_SECONDS)."
        ), out)
        self.assertEqual(out.count(squashed(f"ok: Schema migrations finished ({BOOTSTRAP})")), 1)
        self.assertNotIn("die:", result.stderr)
        self.assert_one_up_and_none_left()
        # One up, with Compose's own wait beyond the migration limit (14400 + 1200).
        self.assertEqual(self.ups(), ["compose --ansi never up -d --build --wait --wait-timeout 15600"])
        self.assertIn(
            "running: docker compose --ansi never up -d --build --wait --wait-timeout 15600",
            self.install_log.read_text(encoding="utf-8"),
        )

    def test_a_failed_bootstrap_stops_the_install_naming_it_and_its_logs(self):
        # Seen by the installer while compose up still waits, or after compose
        # up has returned too.
        for case, extra in (("installer sees it", {}), ("compose returns too", {"UP_RETURNS_AT": 100, "UP_RC": 1})):
            with self.subTest(case=case):
                result = self.start(BOOT_CREATED=5, BOOT_START=10, BOOT_END=100, BOOT_EXIT=1, **extra)
                self.assertNotEqual(result.returncode, 0, self.tail(result))
                err = squashed(result.stderr)
                self.assertIn(squashed(f"{BOOTSTRAP} ended with status=exited exit_code=1"), err)
                self.assertIn(squashed(f"docker compose logs {BOOTSTRAP}"), err)
                self.assertNotIn(squashed(GENERIC_FAILURE), err)
                self.assertNotIn("stackstarted", squashed(result.stdout))
                self.assertIn(f"stub log line for {BOOTSTRAP}", self.install_log.read_text(encoding="utf-8"))
                self.assert_one_up_and_none_left()

    def test_the_migration_limit_stops_the_wait_naming_the_setting(self):
        # The setting's floor, 600 s, on fake time; the bootstrap runs from 10 and never ends.
        result = self.start("INSTALL_MIGRATION_TIMEOUT_SECONDS=600\n", BOOT_CREATED=5, BOOT_START=10)
        self.assertNotEqual(result.returncode, 0, self.tail(result))
        err = squashed(result.stderr)
        self.assertIn(squashed("the INSTALL_MIGRATION_TIMEOUT_SECONDS limit (600 s)"), err)
        self.assertIn(squashed(f"The schema migrations ({BOOTSTRAP}) were still running after 10 min"), err)
        # Re-run only once the bootstrap has exited; never while it is live.
        self.assertIn(squashed(f"once 'docker compose ps -a {BOOTSTRAP}' shows it exited (0)"), err)
        self.assertNotIn(squashed(GENERIC_FAILURE), err)
        self.assertGreaterEqual(len(progress_minutes(result.stdout)), 600 // 60)
        self.assertIn(squashed("waiting up to 10 min (INSTALL_MIGRATION_TIMEOUT_SECONDS)"), squashed(result.stdout))
        self.assert_one_up_and_none_left()
        self.assertEqual(self.ups(), ["compose --ansi never up -d --build --wait --wait-timeout 1800"])

    def test_the_rest_of_the_stack_keeps_its_own_bound_after_the_migrations(self):
        # The bootstrap finishes at 100; compose up never returns on its own.
        result = self.start(BOOT_CREATED=5, BOOT_START=10, BOOT_END=100)
        self.assertNotEqual(result.returncode, 0, self.tail(result))
        self.assertIn(squashed(f"ok: Schema migrations finished ({BOOTSTRAP})"), squashed(result.stdout))
        err = squashed(result.stderr)
        self.assertIn(squashed("did not report running/healthy within 1200 s"), err)
        self.assertIn(squashed(GENERIC_FAILURE), err)
        self.assertNotIn("INSTALL_MIGRATION_TIMEOUT_SECONDS", result.stderr)
        self.assert_one_up_and_none_left()

    def test_a_compose_failure_before_the_migrations_keeps_the_existing_message(self):
        # A build or create error: compose up returns 17 before any bootstrap container exists.
        result = self.start(BOOT_CREATED=100000, UP_RETURNS_AT=3, UP_RC=17)
        self.assertNotEqual(result.returncode, 0, self.tail(result))
        err = squashed(result.stderr)
        self.assertIn(squashed(GENERIC_FAILURE), err)
        self.assertNotIn(BOOTSTRAP, result.stderr)
        self.assert_one_up_and_none_left()

    def test_a_previous_runs_bootstrap_is_not_taken_for_this_one(self):
        # An upgrade: the old release's bootstrap container, exited, is all
        # there is until compose up replaces it at 50 (after its builds).
        for old_exit in (0, 1):
            with self.subTest(old_exit=old_exit):
                result = self.start(OLD_BOOT=1, OLD_EXIT=old_exit, BOOT_CREATED=50, BOOT_START=60,
                                    BOOT_END=130, UP_RETURNS_AT=150)
                self.assertEqual(result.returncode, 0, self.tail(result))
                out = squashed(result.stdout)
                started = out.find(squashed(f"Schema migrations started ({BOOTSTRAP})"))
                finished = out.find(squashed(f"Schema migrations finished ({BOOTSTRAP})"))
                self.assertGreaterEqual(started, 0, "the new run was not reported")
                self.assertGreater(finished, started, "the previous run's exit was taken for this one")
                self.assertEqual(out.count(squashed("Schema migrations finished")), 1)
                self.assert_one_up_and_none_left()

    def test_a_bootstrap_already_running_is_watched(self):
        # A re-run while an earlier run's migrations are still applying: Compose
        # leaves that running container alone, and the installer watches it.
        result = self.start(BOOT_START=0, BOOT_END=130, UP_RETURNS_AT=150)
        self.assertEqual(result.returncode, 0, self.tail(result))
        out = squashed(result.stdout)
        self.assertEqual(out.count(squashed(f"Schema migrations started ({BOOTSTRAP})")), 1)
        self.assertGreaterEqual(len(progress_minutes(result.stdout)), 130 // 60)
        self.assertEqual(out.count(squashed("Schema migrations finished")), 1)
        self.assert_one_up_and_none_left()

    def test_an_out_of_range_limit_stops_before_compose_starts(self):
        for value in ("5", "86401", "four-hours"):
            with self.subTest(value=value):
                result = self.start(f"INSTALL_MIGRATION_TIMEOUT_SECONDS={value}\n", UP_RETURNS_AT=0)
                self.assertNotEqual(result.returncode, 0, self.tail(result))
                self.assertIn(
                    squashed(f"INSTALL_MIGRATION_TIMEOUT_SECONDS must be an integer in [600,86400] (got '{value}')"),
                    squashed(result.stderr),
                )
                self.assertEqual(self.ups(), [])


class StartupWaitShellTests(StartupWaitCases, unittest.TestCase):
    def run_start(self, env):
        script = fill(SHELL, "bin/install", SHELL_PIECES)
        return subprocess.run(
            ["bash", "-c", script], cwd=self.project, env=env,
            text=True, capture_output=True, timeout=TIMEOUT,
        )


@unittest.skipUnless(shutil.which("pwsh"), "pwsh is not installed")
class StartupWaitPowerShellTests(StartupWaitCases, unittest.TestCase):
    def run_start(self, env):
        script = self.scratch / "start.ps1"
        script.write_text(fill(POWERSHELL, "bin/install.ps1", POWERSHELL_PIECES), encoding="utf-8")
        env = {**env, "POWERSHELL_TELEMETRY_OPTOUT": "1"}
        return subprocess.run(
            ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script)],
            cwd=self.project, env=env, text=True, capture_output=True, timeout=TIMEOUT,
        )


class InstallerTextTests(unittest.TestCase):
    """By text: the Distributed start's bounds, and one `compose up`."""

    def text(self, path: str) -> str:
        return (ROOT / path).read_text(encoding="utf-8")

    def test_the_fixed_1200_second_compose_wait_is_no_longer_the_only_bound(self):
        for installer in ("bin/install", "bin/install.ps1"):
            with self.subTest(installer=installer):
                self.assertNotIn("--wait-timeout 1200", self.text(installer))
                self.assertIn("One attempt, as in bin/e2e", self.text(installer))
        bash = self.text("bin/install")
        self.assertIn('. "$ROOT/bin/lib/startup.sh"', bash)
        self.assertIn("bounded_env_int INSTALL_MIGRATION_TIMEOUT_SECONDS 14400 600 86400", bash)
        self.assertIn('start_distributed_stack "$build_flag" "$migration_timeout" 1200', bash)
        self.assertEqual(bash.count("bounded_env_int() {"), 1)
        self.assertLess(bash.index("bounded_env_int() {"),
                        bash.index("bounded_env_int INSTALL_MIGRATION_TIMEOUT_SECONDS"))
        ps = self.text("bin/install.ps1")
        self.assertIn(". (Join-Path $PSScriptRoot 'lib/startup.ps1')", ps)
        self.assertIn("Get-BoundedEnvInt 'INSTALL_MIGRATION_TIMEOUT_SECONDS' 14400 600 86400", ps)
        self.assertIn(
            "Start-DistributedStack -BuildFlag $buildFlag -MigrationTimeout $migrationTimeout -ReadyTimeout 1200", ps
        )
        self.assertEqual(ps.count("function Get-BoundedEnvInt {"), 1)
        self.assertLess(ps.index("function Get-BoundedEnvInt {"),
                        ps.index("Get-BoundedEnvInt 'INSTALL_MIGRATION_TIMEOUT_SECONDS'"))

    def test_each_library_starts_compose_once(self):
        cases = (("bin/lib/startup.sh", r"\bup -d\b"), ("bin/lib/startup.ps1", r"'up',\s*'-d'"))
        for library, pattern in cases:
            with self.subTest(library=library):
                code = [line for line in self.text(library).splitlines() if not line.lstrip().startswith("#")]
                self.assertEqual(sum(1 for line in code if re.search(pattern, line)), 1)


if __name__ == "__main__":
    unittest.main()
