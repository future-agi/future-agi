"""Credential-free checks for the backend test packages script: which Ubuntu
source it asks first, how long it waits, and what it does when a source
fails. apt, sudo and timeout are stand-ins on PATH: nothing is downloaded or
installed."""

import os
import pathlib
import subprocess
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).with_name("install-test-packages.sh")
RUNNER_SOURCES = "/etc/apt/sources.list.d/ubuntu.sources"
PGBOUNCER = "/usr/sbin/pgbouncer"
CANONICAL = "https://archive.ubuntu.com/ubuntu"
SOURCES = """Types: deb
URIs: mirror+file:/etc/apt/apt-mirrors.txt
Suites: noble noble-updates noble-backports noble-security
Components: main universe restricted multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
"""
# Records "<source> <verb> lists=<index files left by earlier calls>" and
# exits with the code the FAIL file gives that call: one "source:verb:code"
# line per failing call, each used once.
APT = r"""#!/bin/bash
for arg in "$@"; do
  case "$arg" in
    Dir::Etc::sourceparts=*) parts=${arg#*=} ;;
    Dir::State::lists=*) lists=${arg#*=} ;;
    update) verb=update ;;
    --download-only) verb=download ;;
    --no-download) verb=install ;;
  esac
done
if grep -q '^URIs: https://archive.ubuntu.com/ubuntu$' "$parts/ubuntu.sources"; then
  source=canonical
else
  source=runner
fi
left=$(ls "$lists" | grep -v '^partial$' | tr '\n' ',')
printf '%s %s lists=%s\n' "$source" "$verb" "$left" >> "$CALLS"
printf '%s\n' "$*" >> "$ARGS"
code=0
line=$(grep -n -m1 "^$source:$verb:" "$FAIL" || true)
if [ -n "$line" ]; then
  code=${line##*:}
  sed -i.bak "${line%%:*}d" "$FAIL"
fi
if [ "$verb" = update ] && [ "$code" = 0 ]; then touch "$lists/$source-index"; fi
exit "$code"
"""


class InstallTestPackagesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.sources = self.root / "ubuntu.sources"
        self.sources.write_text(SOURCES)
        self.tools = self.root / "tools"
        self.tools.mkdir()
        text = SCRIPT.read_text()
        # The two absolute paths the script reads are the only edits: the
        # runner's source list and the installed pgbouncer.
        self.assertEqual((text.count(RUNNER_SOURCES), text.count(PGBOUNCER)), (1, 1))
        self.script = self.root / "script.sh"
        self.script.write_text(
            text.replace(RUNNER_SOURCES, str(self.sources)).replace(
                PGBOUNCER, str(self.tools / "pgbouncer")
            )
        )
        self.cache = self.root / "cache"
        self.calls = self.root / "calls"
        self.fail = self.root / "fail"
        self.env = dict(os.environ, PATH=f"{self.tools}:{os.environ['PATH']}")
        self.env.update(
            CALLS=str(self.calls), ARGS=str(self.root / "args"), FAIL=str(self.fail)
        )
        self.tool("apt-get", APT)
        self.tool("sudo", '#!/bin/sh\nexec "$@"\n')
        self.tool(
            "timeout",
            '#!/bin/sh\nprintf "timeout %s\\n" "$1" >> "$CALLS"\nshift\nexec "$@"\n',
        )
        self.tool("sleep", '#!/bin/sh\nprintf "sleep %s\\n" "$1" >> "$CALLS"\n')
        for name in ("ffmpeg", "ffprobe", "xmlsec1", "pgbouncer", "bwrap"):
            self.tool(name, f'#!/bin/sh\necho "{name} stand-in"\n')

    def tool(self, name, source):
        path = self.tools / name
        path.write_text(source)
        path.chmod(0o755)

    def run_script(self, *failures):
        self.fail.write_text("".join(f"{line}\n" for line in failures))
        return subprocess.run(
            ["bash", str(self.script), str(self.cache)],
            env=self.env,
            text=True,
            capture_output=True,
            timeout=60,
        )

    def log(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def test_the_runner_mirror_is_asked_first_within_a_short_bound(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.log(),
            [
                "timeout 30s",
                "runner update lists=",
                "timeout 30s",
                "runner download lists=runner-index,",
                "timeout 120s",
                "runner install lists=runner-index,",
            ],
        )
        self.assertEqual((self.cache / "complete").read_text(), "runner\n")
        self.assertEqual((self.cache / "sources/ubuntu.sources").read_text(), SOURCES)
        self.assertRegex(
            result.stdout, r"(?m)^APT packages served by the runner source in \d+s$"
        )
        self.assertNotIn("::warning::", result.stdout)

    def test_a_stalled_mirror_falls_back_to_the_canonical_archive(self):
        for name, failure in [
            ("index refresh timed out", "runner:update:124"),
            ("index refresh failed", "runner:update:100"),
            ("package download timed out", "runner:download:124"),
            ("package download failed", "runner:download:100"),
        ]:
            with self.subTest(name=name):
                self.setUp()
                result = self.run_script(failure)
                self.assertEqual(result.returncode, 0, result.stderr)
                log = self.log()
                first_canonical = log.index("canonical update lists=")
                # One bounded try of the mirror: no retry and no wait before
                # the fall-back, and none of its indexes left for the archive.
                self.assertNotIn("sleep", " ".join(log))
                self.assertEqual(
                    [
                        line
                        for line in log[:first_canonical]
                        if line.startswith("runner")
                    ].count("runner update lists="),
                    1,
                )
                self.assertEqual(
                    log[first_canonical - 1 :],
                    [
                        "timeout 120s",
                        "canonical update lists=",
                        "timeout 180s",
                        "canonical download lists=canonical-index,",
                        "timeout 120s",
                        "canonical install lists=canonical-index,",
                    ],
                )
                self.assertEqual((self.cache / "complete").read_text(), "canonical\n")
                self.assertEqual(
                    (self.cache / "sources/ubuntu.sources").read_text(),
                    SOURCES.replace("mirror+file:/etc/apt/apt-mirrors.txt", CANONICAL),
                )
                self.assertIn("::warning::", result.stdout)
                self.assertRegex(
                    result.stdout,
                    r"(?m)^APT packages served by the canonical source in \d+s$",
                )

    def test_a_mid_sync_canonical_archive_is_retried_and_a_timeout_is_not(self):
        result = self.run_script(
            "runner:update:124", "canonical:update:100", "canonical:update:100"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [line for line in self.log() if line.startswith("sleep")],
            ["sleep 20", "sleep 40"],
        )
        self.assertEqual(self.log().count("canonical update lists="), 3)
        self.setUp()
        result = self.run_script("runner:update:124", "canonical:update:124")
        self.assertEqual(result.returncode, 124)
        self.assertEqual(self.log().count("canonical update lists="), 1)

    def test_no_source_working_fails_without_marking_the_cache_complete(self):
        result = self.run_script("runner:download:100", "canonical:download:100")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.cache / "complete").exists())
        self.assertNotIn("install", " ".join(self.log()))
        self.assertNotIn("served by", result.stdout)

    def test_a_cache_hit_uses_no_network(self):
        self.assertEqual(self.run_script().returncode, 0)
        self.calls.unlink()
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.log(), ["timeout 120s", "runner install lists=runner-index,"]
        )
        self.assertIn("APT cache hit: runner source", result.stdout)

    def test_package_signatures_stay_checked(self):
        # Both sources are Ubuntu archives checked against the same keyring.
        # No call and no line of the script may switch that check off.
        self.assertEqual(self.run_script("runner:update:124").returncode, 0)
        seen = (SCRIPT.read_text() + (self.root / "args").read_text()).lower()
        for switch in (
            "allow-unauthenticated",
            "allowunauthenticated",
            "allowinsecure",
            "trusted=yes",
            "force-yes",
            "no-check-certificate",
            "verify-peer=false",
        ):
            self.assertNotIn(switch, seen)
        self.assertIn(
            "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg",
            (self.cache / "sources/ubuntu.sources").read_text(),
        )


if __name__ == "__main__":
    unittest.main()
