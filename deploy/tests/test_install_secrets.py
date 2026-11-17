"""What bin/install and bin/install.ps1 write to .env (bin/lib/secrets.sh and
bin/lib/secrets.ps1), run on a scratch .env with stub output helpers and a stub
`docker`. The PowerShell cases skip where pwsh is not installed.

The installer takes an install for new when it sees none of the project's
volumes (another Docker context, a remote daemon, a renamed project), so the
new-install path may run on an .env whose API keys were hashed under the pepper
Compose used: the published default when .env holds none, else .env's value.
On every path, then, an unset, empty, published or CHANGEME-* pepper changes
only with the value Compose used kept in FI_API_SECRET_PEPPERS_RETIRED first;
any other value is the operator's and never changes.

A Standalone container that finds no INTEGRATION_ENCRYPTION_KEY in .env keeps
its own in the app-data volume. The installer adopts that one into .env, and
stops, writing nothing, when .env or the shell holds a different one; --force
does not pass that stop. Only Docker's "no such volume" answer means there is
no volume; any other failure fills in no key. The volume probed is
<project>_app-data for the project Compose itself resolves, which each
installer computes with its own line (bin/install, bin/install.ps1), run here
as written.

Values are compared in memory and never printed (assertTrue, not assertEqual).
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PUBLISHED_PEPPER = "local-dev-only-api-secret-pepper-replace-me"
OPERATOR_PEPPER = "operator-pepper-from-the-secret-store-0123456789"
OPERATOR_RETIRED = "an-older-operator-pepper-0123456789abcdef"
PLACEHOLDER_PEPPER = "CHANGEME-api-secret-pepper"
OPERATOR_KEY = base64.urlsafe_b64encode(bytes(range(32))).decode()
KEPT_KEY = base64.urlsafe_b64encode(bytes(range(32, 64))).decode()
WATCHED = ("FI_API_SECRET_PEPPER", "FI_API_SECRET_PEPPERS_RETIRED", "INTEGRATION_ENCRYPTION_KEY")
# The stub's app-data volume: present, and holding KEPT_KEY.
KEPT_VOLUME = {"KEPT_VOLUME": "1", "KEPT_KEY": KEPT_KEY}
# What `docker volume inspect` prints for a missing volume: current Docker,
# then the older CLI's wording.
NO_SUCH_VOLUME = (
    "Error response from daemon: get secrets-test_app-data: no such volume",
    "Error: No such volume: secrets-test_app-data",
)
# Failures that say nothing about whether the volume exists.
INSPECT_FAILURES = (
    "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?",
    "permission denied while trying to connect to the Docker daemon socket at unix:///var/run/docker.sock",
)
# Each installer's own line that resolves the Compose project (run as written).
PROJECT_LINES = {
    "bin/install": (r"^project_name=.+$", 'write_secrets "$FRESH_INSTALL" "$DISTRIBUTED" "$project_name"'),
    "bin/install.ps1": (r"^\$projectName = .+$", "Write-Secrets $freshInstall $IsDistributed $projectName"),
}


def project_line(installer: str) -> str:
    match = re.search(PROJECT_LINES[installer][0], (ROOT / installer).read_text(encoding="utf-8"), re.M)
    if match is None:
        raise AssertionError(f"{installer} has no line that sets the project name")
    return match.group(0)


# bin/install's shell options and ERR trap (on_err); its output helpers print
# no values. The stub docker answers `volume inspect` and `run` for the
# app-data volume as the KEPT_* and INSPECT_ERROR variables say, records the
# volumes inspected in VOLUME_LOG, and finds no ClickHouse container.
SHELL = r"""
set -eEuo pipefail
on_err() { local code=$?; printf 'err-trap: line %s\n' "$1" >&2; exit "$code"; }
trap 'on_err $LINENO' ERR
ok() { printf 'ok: %s\n' "$*"; }
warn() { printf 'warn: %s\n' "$*" >&2; }
die() { printf 'die: %s\n' "$*" >&2; exit 4; }
preflight_fail() { printf 'preflight: %s\n' "$*" >&2; [[ "${FI_FORCE:-}" == 1 ]] || exit 3; }
docker() {
  case "$1" in
    volume)
      if [[ -n "${VOLUME_LOG:-}" ]]; then printf '%s\n' "$3" >> "$VOLUME_LOG"; fi
      if [[ -n "${KEPT_VOLUME:-}" ]]; then return 0; fi
      printf '%s\n' "${INSPECT_ERROR:-Error response from daemon: get $3: no such volume}" >&2
      return 1 ;;
    run) [[ "${KEPT_READ:-}" != fail ]] || return 1; [[ -z "${KEPT_KEY:-}" ]] || printf '%s\n' "$KEPT_KEY" ;;
    ps) return 0 ;;
    *) return 1 ;;
  esac
}
. "$FI_ROOT/bin/lib/env.sh"
. "$FI_ROOT/bin/lib/secrets.sh"
@PROJECT_LINE@
write_secrets "$FI_FRESH" 0 "$project_name"
"""

# Invoke-Probe as bin/install.ps1 defines it.
POWERSHELL = r"""
$ErrorActionPreference = 'Stop'
function Ok { param([string]$Msg) Write-Host "ok: $Msg" }
function Warn { param([string]$Msg) [Console]::Error.WriteLine("warn: $Msg") }
function Die { param([string]$Msg) [Console]::Error.WriteLine("die: $Msg"); exit 4 }
function Fail-Preflight {
  param([string]$Msg)
  [Console]::Error.WriteLine("preflight: $Msg")
  if ($env:FI_FORCE -ne '1') { exit 3 }
}
function Invoke-Probe {
  param([scriptblock]$Command)
  $saved = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try { & $Command 2>$null } finally { $ErrorActionPreference = $saved }
}
function docker {
  $global:LASTEXITCODE = 1
  if ($args[0] -eq 'volume') {
    if ($env:VOLUME_LOG) { Add-Content -LiteralPath $env:VOLUME_LOG -Value $args[2] }
    if ($env:KEPT_VOLUME) { $global:LASTEXITCODE = 0; return }
    $message = if ($env:INSPECT_ERROR) { $env:INSPECT_ERROR } else { "Error response from daemon: get $($args[2]): no such volume" }
    Write-Error -Message $message -ErrorAction Continue
    return
  }
  if ($args[0] -eq 'run' -and $env:KEPT_READ -ne 'fail') {
    $global:LASTEXITCODE = 0
    if ($env:KEPT_KEY) { $env:KEPT_KEY }
  }
}
. (Join-Path $env:FI_ROOT 'bin/lib/env.ps1')
. (Join-Path $env:FI_ROOT 'bin/lib/secrets.ps1')
@PROJECT_LINE@
Write-Secrets -Fresh ($env:FI_FRESH -eq '1') -Distributed $false -ProjectName $projectName
"""


def env_values(path: Path) -> dict[str, str]:
    """The watched keys' last KEY= lines, unquoted: what the tests compare."""
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, raw = line.partition("=")
        if sep and key in WATCHED:
            raw = raw.strip()
            if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
                raw = raw[1:-1]
            values[key] = raw
    return values


def retired(values: dict[str, str]) -> list[str]:
    return [part.strip() for part in values.get("FI_API_SECRET_PEPPERS_RETIRED", "").split(",")]


def is_fernet_key(value: str) -> bool:
    return len(value) == 44 and len(base64.urlsafe_b64decode(value)) == 32


def squashed(text: str) -> str:
    """Output without colour codes or whitespace: pwsh may wrap a message."""
    return re.sub(r"\s+", "", re.sub(r"\x1b\[[0-9;]*m", "", text))


class SecretsWriter:
    """The cases both installers must pass; subclasses run one of them."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name, "project")
        self.project.mkdir()
        self.scratch = Path(temporary.name)
        self.env_file = self.project / ".env"

    def environment(self, fresh: int, extra: dict[str, str | None]) -> dict[str, str]:
        # Nothing else: an exported pepper, even empty, changes what is decided.
        # The project is exported as secrets-test unless a test says otherwise;
        # None leaves a variable out.
        keep = ("PATH", "HOME", "TMPDIR", "LANG")
        env = {key: value for key, value in os.environ.items() if key in keep}
        env = {**env, "FI_ROOT": str(ROOT), "FI_FRESH": str(fresh),
               "COMPOSE_PROJECT_NAME": "secrets-test", **extra}
        return {key: value for key, value in env.items() if value is not None}

    def install(self, fresh: int, **extra: str | None) -> subprocess.CompletedProcess:
        raise NotImplementedError

    def run_install(self, fresh: int, **extra: str | None) -> subprocess.CompletedProcess:
        result = self.install(fresh, **extra)
        if result.returncode:
            stderr = result.stderr
            for value in (*env_values(self.env_file).values(), KEPT_KEY, OPERATOR_KEY):
                if len(value) >= 8:
                    stderr = stderr.replace(value, "<value>")
            self.fail(f"the install step exited {result.returncode}: {stderr[-2000:]}")
        return result

    def write_env(self, text: str) -> None:
        self.env_file.write_text(text, encoding="utf-8")

    def example_env(self) -> str:
        # bin/install copies .env.example to .env on a new install.
        return (ROOT / ".env.example").read_text(encoding="utf-8")

    def assert_no_key_printed(self, result: subprocess.CompletedProcess) -> None:
        output = result.stdout + result.stderr
        self.assertTrue(KEPT_KEY not in output and OPERATOR_KEY not in output, "a key was printed")

    def test_a_second_run_changes_no_pepper_or_key(self):
        # A new install, then the same install run again with its volumes seen
        # (0) or, from another Docker context, not seen (1).
        for second in (0, 1):
            with self.subTest(second_run_fresh=second):
                self.write_env(self.example_env())
                self.run_install(1)
                first = env_values(self.env_file)
                self.assertTrue(
                    re.fullmatch(r"[0-9a-f]{64}", first.get("FI_API_SECRET_PEPPER", "")),
                    "the first run wrote no pepper",
                )
                self.run_install(second)
                again = env_values(self.env_file)
                for key in WATCHED:
                    self.assertTrue(again.get(key) == first.get(key), f"{key} changed")

    def test_an_unset_or_published_pepper_on_a_new_install_is_replaced_with_the_default_retired(self):
        cases = {
            "the example file": self.example_env(),
            "an empty value": "SECRET_KEY=\nFI_API_SECRET_PEPPER=\n",
            "no line at all": "SECRET_KEY=\n",
            "the published default": f"SECRET_KEY=\nFI_API_SECRET_PEPPER={PUBLISHED_PEPPER}\n",
        }
        for case, text in cases.items():
            with self.subTest(case=case):
                self.write_env(text)
                result = self.run_install(1)
                values = env_values(self.env_file)
                self.assertTrue(
                    re.fullmatch(r"[0-9a-f]{64}", values.get("FI_API_SECRET_PEPPER", "")),
                    "the pepper was not replaced",
                )
                self.assertTrue(PUBLISHED_PEPPER in retired(values), "the published default is not retired")
                self.assertIn("ok: Rotated FI_API_SECRET_PEPPER", result.stdout)
                self.assertNotIn("ok: Generated FI_API_SECRET_PEPPER", result.stdout)

    def test_an_operator_pepper_and_its_retired_list_are_never_changed(self):
        text = (
            f"FI_API_SECRET_PEPPER={OPERATOR_PEPPER}\n"
            f"FI_API_SECRET_PEPPERS_RETIRED='{OPERATOR_RETIRED}'\n"
        )
        for fresh in (1, 0):
            with self.subTest(fresh=fresh):
                self.write_env(text)
                self.run_install(fresh)
                values = env_values(self.env_file)
                self.assertTrue(values.get("FI_API_SECRET_PEPPER") == OPERATOR_PEPPER, "the pepper changed")
                self.assertTrue(
                    values.get("FI_API_SECRET_PEPPERS_RETIRED") == OPERATOR_RETIRED,
                    "the retired list changed",
                )

    def test_a_placeholder_pepper_is_rotated_with_it_retired(self):
        # Compose used the placeholder itself, so that is the value kept for
        # verification; the published default, never used, is not added.
        for fresh in (1, 0):
            with self.subTest(fresh=fresh):
                self.write_env(f"FI_API_SECRET_PEPPER={PLACEHOLDER_PEPPER}\n")
                result = self.run_install(fresh)
                values = env_values(self.env_file)
                self.assertTrue(
                    re.fullmatch(r"[0-9a-f]{64}", values.get("FI_API_SECRET_PEPPER", "")),
                    "the placeholder pepper was not replaced",
                )
                self.assertTrue(PLACEHOLDER_PEPPER in retired(values), "the placeholder is not retired")
                self.assertTrue(PUBLISHED_PEPPER not in retired(values), "an unused default was retired")
                self.assertIn("ok: Rotated FI_API_SECRET_PEPPER", result.stdout)

    def test_a_new_install_stops_on_a_pepper_it_cannot_rotate(self):
        # Compose would keep using a pepper exported in the shell. That path
        # fills other secrets before the pepper, so compare the pepper lines.
        # An undecodable .env now stops earlier: MCP's absent-only fill must
        # establish safe dotenv boundaries before any credential is changed.
        cases = (
            ("exported empty", {"FI_API_SECRET_PEPPER": ""}, "", "UnsetFI_API_SECRET_PEPPERintheshell"),
            ("exported published", {"FI_API_SECRET_PEPPER": PUBLISHED_PEPPER}, "", "UnsetFI_API_SECRET_PEPPERintheshell"),
            ("exported placeholder", {"FI_API_SECRET_PEPPER": PLACEHOLDER_PEPPER}, "", "UnsetFI_API_SECRET_PEPPERintheshell"),
            ("undecodable .env", {}, 'BROKEN="unterminated\n',
             "CannotsafelyreadAGENTCC_MCP_STATE_KEYS;credentialsunchanged"),
        )
        for case, extra, prefix, message in cases:
            with self.subTest(case=case):
                self.write_env(prefix + "SECRET_KEY=\nFI_API_SECRET_PEPPER=\n")
                before = self.env_file.read_bytes()
                result = self.install(1, **extra)
                self.assertNotEqual(result.returncode, 0)
                values = env_values(self.env_file)
                self.assertTrue(values.get("FI_API_SECRET_PEPPER") == "", "the pepper changed")
                self.assertNotIn("FI_API_SECRET_PEPPERS_RETIRED", values)
                self.assertIn(message, squashed(result.stderr))
                if case == "undecodable .env":
                    self.assertTrue(self.env_file.read_bytes() == before, "refusal changed credentials")
                    self.assertTrue("unterminated" not in result.stdout + result.stderr,
                                    "a dotenv value was printed")

    def test_the_integration_key_is_filled_once_and_then_kept(self):
        for fresh in (1, 0):
            for case, text in (("absent", "SECRET_KEY=\n"), ("empty", "INTEGRATION_ENCRYPTION_KEY=\n")):
                with self.subTest(fresh=fresh, case=case):
                    self.write_env(text)
                    self.run_install(fresh)
                    key = env_values(self.env_file).get("INTEGRATION_ENCRYPTION_KEY", "")
                    self.assertTrue(is_fernet_key(key), "no Fernet key was written")
                    self.run_install(fresh)
                    again = env_values(self.env_file).get("INTEGRATION_ENCRYPTION_KEY")
                    self.assertTrue(again == key, "a second run changed the key")
            with self.subTest(fresh=fresh, case="set"):
                self.write_env(f"INTEGRATION_ENCRYPTION_KEY={OPERATOR_KEY}\n")
                self.run_install(fresh)
                kept = env_values(self.env_file).get("INTEGRATION_ENCRYPTION_KEY")
                self.assertTrue(kept == OPERATOR_KEY, "the key changed")

    def test_the_key_kept_in_the_app_data_volume_is_adopted(self):
        cases = (
            ("absent", "SECRET_KEY=\n"),
            ("empty", "INTEGRATION_ENCRYPTION_KEY=\n"),
            ("placeholder", "INTEGRATION_ENCRYPTION_KEY=CHANGEME-integration-key\n"),
        )
        for fresh in (0, 1):
            for case, text in cases:
                with self.subTest(fresh=fresh, case=case):
                    self.write_env(text)
                    result = self.run_install(fresh, **KEPT_VOLUME)
                    key = env_values(self.env_file).get("INTEGRATION_ENCRYPTION_KEY")
                    self.assertTrue(key == KEPT_KEY, "the kept key was not adopted")
                    self.assertIn("ok: Adopted the INTEGRATION_ENCRYPTION_KEY", result.stdout)
                    self.assert_no_key_printed(result)
                    # Next run: .env and the volume agree; nothing to do.
                    before = self.env_file.read_bytes()
                    again = self.run_install(fresh, **KEPT_VOLUME)
                    self.assertTrue(self.env_file.read_bytes() == before, "the next run changed .env")
                    self.assertNotIn("Adopted", again.stdout)

    def assert_stops_on_a_differing_key(self, **force: str) -> None:
        # The empty pepper would be rotated if anything ran: an unchanged file
        # shows the stop came first.
        cases = (
            (".env", f"FI_API_SECRET_PEPPER=\nINTEGRATION_ENCRYPTION_KEY={OPERATOR_KEY}\n", {}),
            ("the shell", "FI_API_SECRET_PEPPER=\n", {"INTEGRATION_ENCRYPTION_KEY": OPERATOR_KEY}),
        )
        for fresh in (0, 1):
            for case, text, extra in cases:
                with self.subTest(fresh=fresh, case=case, **force):
                    self.write_env(text)
                    before = self.env_file.read_bytes()
                    result = self.install(fresh, **KEPT_VOLUME, **extra, **force)
                    # The installer's die (exit 4 here), which --force never passes.
                    self.assertEqual(result.returncode, 4)
                    self.assertTrue(self.env_file.read_bytes() == before, "the stop wrote to .env")
                    self.assertIn("die:", result.stderr)
                    self.assertNotIn("preflight:", result.stderr)
                    stop = squashed(result.stderr)
                    for words in ("Twodifferentintegrationkeys", "in.envoryourshell",
                                  "thevolumesecrets-test_app-data", "readwithonlyoneofthem",
                                  "Putthekeythatwasinuseinto.env"):
                        self.assertIn(words, stop)
                    self.assertNotIn("Adopted", result.stdout)
                    self.assert_no_key_printed(result)

    def test_a_key_that_differs_from_the_kept_one_stops_before_anything_is_written(self):
        self.assert_stops_on_a_differing_key()

    def test_force_does_not_pass_a_differing_key(self):
        self.assert_stops_on_a_differing_key(FI_FORCE="1")

    def test_an_unreadable_volume_leaves_the_key_to_the_app(self):
        # The volume may hold a kept key the installer cannot see: filling one
        # in could make a second. A failed probe is no failed install.
        self.write_env("SECRET_KEY=\n")
        result = self.run_install(0, KEPT_VOLUME="1", KEPT_READ="fail")
        self.assertNotIn("INTEGRATION_ENCRYPTION_KEY", env_values(self.env_file))
        self.assertIn("Couldnotreadthevolumesecrets-test_app-data", squashed(result.stderr))
        self.assertNotIn("err-trap", result.stderr)

    def test_an_inspect_failure_other_than_no_such_volume_adds_no_key(self):
        # Docker down or out of reach says nothing about whether a kept key
        # exists: filling one in could make a second.
        for fresh in (0, 1):
            for failure in INSPECT_FAILURES:
                with self.subTest(fresh=fresh, failure=failure):
                    self.write_env("SECRET_KEY=\n")
                    result = self.run_install(fresh, INSPECT_ERROR=failure)
                    self.assertNotIn("INTEGRATION_ENCRYPTION_KEY", env_values(self.env_file))
                    self.assertIn("Couldnottellwhetherthevolumesecrets-test_app-dataexists",
                                  squashed(result.stderr))
                    self.assertNotIn("err-trap", result.stderr)

    def test_no_such_volume_still_fills_a_key(self):
        for fresh in (0, 1):
            for answer in NO_SUCH_VOLUME:
                with self.subTest(fresh=fresh, answer=answer):
                    self.write_env("SECRET_KEY=\n")
                    result = self.run_install(fresh, INSPECT_ERROR=answer)
                    key = env_values(self.env_file).get("INTEGRATION_ENCRYPTION_KEY", "")
                    self.assertTrue(is_fernet_key(key), "no key was filled in")
                    self.assertNotIn("Couldnottell", squashed(result.stderr))
                    self.assertNotIn("err-trap", result.stderr)

    def test_the_volume_probed_is_named_for_the_compose_project(self):
        # As Compose resolves it: an exported COMPOSE_PROJECT_NAME, else .env's,
        # else the compose files' `name: futureagi`. An exported empty one hides
        # .env's, as Compose's own environment wins over .env even when empty.
        cases = (
            ("not set", None, "", "futureagi"),
            (".env", None, "COMPOSE_PROJECT_NAME=fi-from-env\n", "fi-from-env"),
            ("shell over .env", "fi-from-shell", "COMPOSE_PROJECT_NAME=fi-from-env\n", "fi-from-shell"),
            ("exported empty", "", "COMPOSE_PROJECT_NAME=fi-from-env\n", "futureagi"),
        )
        for index, (case, exported, dotenv, project) in enumerate(cases):
            with self.subTest(case=case):
                log = self.scratch / f"volumes-{index}.log"
                self.write_env(dotenv + "SECRET_KEY=\n")
                self.run_install(0, COMPOSE_PROJECT_NAME=exported, VOLUME_LOG=str(log))
                probed = log.read_text(encoding="utf-8").split() if log.exists() else []
                self.assertEqual(probed[:1], [f"{project}_app-data"])


class InstallSecretsShellTests(SecretsWriter, unittest.TestCase):
    def install(self, fresh, **extra):
        script = SHELL.replace("@PROJECT_LINE@", project_line("bin/install"))
        return subprocess.run(
            ["bash", "-c", script], cwd=self.project, env=self.environment(fresh, extra),
            text=True, capture_output=True, timeout=60,
        )


@unittest.skipUnless(shutil.which("pwsh"), "pwsh is not installed")
class InstallSecretsPowerShellTests(SecretsWriter, unittest.TestCase):
    def install(self, fresh, **extra):
        script = self.scratch / "write-secrets.ps1"
        script.write_text(POWERSHELL.replace("@PROJECT_LINE@", project_line("bin/install.ps1")),
                          encoding="utf-8")
        env = {**self.environment(fresh, extra), "POWERSHELL_TELEMETRY_OPTOUT": "1"}
        return subprocess.run(
            ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script)],
            cwd=self.project, env=env, text=True, capture_output=True, timeout=120,
        )


class ComposeProjectNameTest(unittest.TestCase):
    """Each installer's project line names the project Compose itself uses, so
    the volume it probes is the one Compose created. Compose only parses a
    one-service file whose `name:` is the stack's (`config`, no daemon)."""

    CASES = (
        ("not set", None, ""),
        (".env", None, "COMPOSE_PROJECT_NAME=fi-from-env\n"),
        (".env quoted, with a comment", None, 'COMPOSE_PROJECT_NAME="fi-quoted" # second checkout\n'),
        ("shell over .env", "fi-from-shell", "COMPOSE_PROJECT_NAME=fi-from-env\n"),
        ("exported empty", "", "COMPOSE_PROJECT_NAME=fi-from-env\n"),
    )

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name)
        self.compose_file = self.project / "docker-compose.yml"
        self.compose_file.write_text(
            "name: futureagi\nservices:\n  probe:\n    image: unused:never-started\n", encoding="utf-8"
        )

    def environment(self, exported: str | None) -> dict[str, str]:
        env = {key: value for key, value in os.environ.items() if key in ("PATH", "HOME")}
        if exported is not None:
            env["COMPOSE_PROJECT_NAME"] = exported
        return env

    def compose_name(self, exported: str | None) -> str:
        try:
            from .compose_test_tools import compose_command
        except ImportError:
            from compose_test_tools import compose_command
        rendered = subprocess.run(
            [*compose_command(), "-f", str(self.compose_file), "config", "--format", "json"],
            cwd=self.project, env=self.environment(exported), text=True, capture_output=True, timeout=60,
        )
        self.assertEqual(rendered.returncode, 0, rendered.stderr[-500:])
        return json.loads(rendered.stdout)["name"]

    def installer_names(self, exported: str | None) -> dict[str, str]:
        script = f'. "{ROOT}/bin/lib/env.sh"\n{project_line("bin/install")}\nprintf %s "$project_name"\n'
        names = {"bin/install": subprocess.run(
            ["bash", "-c", "set -euo pipefail\n" + script], cwd=self.project,
            env=self.environment(exported), text=True, capture_output=True, timeout=30,
        ).stdout}
        if shutil.which("pwsh"):
            ps = self.project / "project-name.ps1"
            ps.write_text(
                f"$ErrorActionPreference = 'Stop'\n. '{ROOT}/bin/lib/env.ps1'\n"
                f"{project_line('bin/install.ps1')}\n[Console]::Out.Write($projectName)\n",
                encoding="utf-8",
            )
            names["bin/install.ps1"] = subprocess.run(
                ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(ps)],
                cwd=self.project, env=self.environment(exported), text=True, capture_output=True, timeout=60,
            ).stdout
        return names

    def test_the_installers_resolve_the_project_compose_uses(self):
        for case, exported, dotenv in self.CASES:
            with self.subTest(case=case):
                (self.project / ".env").write_text(dotenv, encoding="utf-8")
                expected = self.compose_name(exported)
                for installer, name in self.installer_names(exported).items():
                    self.assertEqual(name, expected, installer)

    def test_the_installers_probe_under_that_project(self):
        for installer, (_, call) in PROJECT_LINES.items():
            with self.subTest(installer=installer):
                project_line(installer)
                self.assertIn(call, (ROOT / installer).read_text(encoding="utf-8"))

    def test_the_stack_files_name_the_project(self):
        for name in ("docker-compose.yml", "docker-compose.distributed.yml"):
            with self.subTest(file=name):
                text = (ROOT / name).read_text(encoding="utf-8")
                self.assertRegex(text, r"(?m)^name: futureagi$")


class BackupSentenceTest(unittest.TestCase):
    """Where backups are described, the integration key is named beside the database."""

    def test_the_summaries_and_installation_md_say_to_back_up_the_key(self):
        for name in ("bin/lib/summary.sh", "bin/lib/summary.ps1", "INSTALLATION.md"):
            with self.subTest(file=name):
                text = re.sub(r"\s+", " ", (ROOT / name).read_text(encoding="utf-8"))
                self.assertIn("The integration encryption key is kept in", text)
                self.assertIn("back it up with the database", text)


if __name__ == "__main__":
    unittest.main()
