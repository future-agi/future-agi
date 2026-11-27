"""The unit CI fixture root must satisfy the unchanged production path guards."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import update_unit_chunks

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/update-ci.yml"


def unit_script():
    # Read just this step; do not add a YAML dependency to the stdlib-only lane.
    lines = WORKFLOW.read_text().splitlines()
    start = lines.index("      - name: deploy/tests/test_update_*.py")
    index = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("        run:"))
    inline = lines[index].partition("run:")[2].strip()
    if inline != "|":
        return inline
    script = []
    for line in lines[index + 1:]:
        if not line.startswith("          "):
            break
        script.append(line[10:])
    return "\n".join(script)


class UnitTemporaryAncestryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="fagi-ci-temp-", dir=Path.home())
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.bin = self.directory / "bin"
        self.bin.mkdir()
        self.result = self.directory / "result.json"
        probe = self.bin / "python"
        probe.write_text(
            "#!" + sys.executable + "\n"
            "import json, os, pathlib, sys, tempfile\n"
            "if sys.argv[1:2] == ['deploy/tests/update_unit_chunks.py']:\n"
            "    os.execv(" + repr(sys.executable) + ", [" + repr(sys.executable) + ", *sys.argv[1:]])\n"
            "sys.path.insert(0, " + repr(str(ROOT)) + ")\n"
            "from deploy.update.host_facts import safe_path\n"
            "with tempfile.TemporaryDirectory() as name:\n"
            "    path = pathlib.Path(name).resolve()\n"
            "    safe_path(path, directory=True)\n"
            "    pathlib.Path(os.environ['FIXTURE_RESULT']).write_text(json.dumps({\n"
            "        'parent': str(path.parent), 'mode': path.parent.stat().st_mode & 0o777,\n"
            "        'argv': sys.argv[1:]}))\n"
            "sys.exit(int(os.environ['FIXTURE_EXIT']))\n"
        )
        probe.chmod(0o700)

    def run_script(self, *, exit_code=0, home=None):
        return subprocess.run(
            ["/bin/bash", "-e", "-c", unit_script()], cwd=ROOT,
            env={**os.environ, "HOME": str(home or self.directory), "TMPDIR": "/tmp",
                 "PATH": str(self.bin) + os.pathsep + os.defpath,
                 "FIXTURE_RESULT": str(self.result), "FIXTURE_EXIT": str(exit_code),
                 "CHUNK": "1", "CHUNKS": "3"},
            capture_output=True, text=True, timeout=10,
        )

    def test_ci_uses_private_trusted_ancestry_cleans_up_and_preserves_test_exit(self):
        for exit_code in (0, 19):
            with self.subTest(exit_code=exit_code):
                result = self.run_script(exit_code=exit_code)
                self.assertEqual(result.returncode, exit_code, result.stdout + result.stderr)
                record = json.loads(self.result.read_text())
                parent = Path(record["parent"])
                self.assertEqual(parent.parent, self.directory)
                self.assertEqual(record["mode"], 0o700)
                # The leg runs its chunk's modules (update_unit_chunks.py).
                durations = json.loads(update_unit_chunks.DURATIONS.read_text())
                chunk = update_unit_chunks.plan(update_unit_chunks.modules(), durations, 3)[0]
                self.assertEqual(record["argv"], ["-m", "unittest", "-v", *chunk])
                self.assertFalse(parent.exists(), "the CI-owned temp directory leaked")

    def test_temp_creation_failure_stops_before_tests(self):
        result = self.run_script(home=self.directory / "missing-home")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mktemp", result.stderr)
        self.assertFalse(self.result.exists())


if __name__ == "__main__":
    unittest.main()
