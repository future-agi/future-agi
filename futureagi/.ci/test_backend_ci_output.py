"""Guard the shard command against unbounded verbose parametrization output."""

import pathlib
import re
import shlex
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]


class BackendOutputTest(unittest.TestCase):
    def test_shard_keeps_compact_progress_and_full_failure_reporting(self):
        workflow = (ROOT / ".github/workflows/backend-ci.yml").read_text()
        shard = workflow.split("      - name: Run shard\n", 1)[1].split(
            "      - name:", 1
        )[0]
        command = re.search(r"\buv run pytest ([^\n]+)", shard)
        self.assertIsNotNone(command)
        args = shlex.split(command.group(1).removesuffix("\\").strip())
        # pytest.ini already adds -v. The existing 1 MiB body parametrization
        # turns extra verbosity into an enormous progress line, not more tests.
        self.assertIn("-q", args)
        self.assertFalse(
            any(
                re.fullmatch(r"-v+|--verbose|--verbosity(?:=.*)?", arg) for arg in args
            ),
            args,
        )
        self.assertIn("-ra", args)
        self.assertIn("--junitxml=junit-${{ matrix.group }}.xml", shard)


if __name__ == "__main__":
    unittest.main()
