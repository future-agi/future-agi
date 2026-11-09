"""Contract audits must ignore target-only changes, but reject branch drift."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class AuditBaselineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for name in ("skills-generation-audit.py", "client-nullability-audit.mjs"):
            target = self.root / "api_contracts/tests" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / "api_contracts/tests" / name, target)
        self.write("frontend/package.json", "{}")
        (self.root / "frontend/node_modules").symlink_to(
            ROOT / "frontend/node_modules", target_is_directory=True
        )
        self.schema = {"paths": {"/existing/": {}}, "definitions": {"Existing": {}}}
        self.tools = [{"name": f"existing_{number}"} for number in range(107)]
        self.write_schema()
        self.write_tools()
        self.write_zod("zod.string()")
        self.git("init", "-q")
        self.commit("base")
        self.git("branch", "target")
        self.git("checkout", "-qb", "feature")
        self.schema["paths"]["/model-hub/skills/"] = {}
        self.schema["definitions"]["SkillsExample"] = {}
        self.tools.extend({"name": f"skills_{number}"} for number in range(6))
        self.write_schema()
        self.write_tools()
        self.commit("feature contracts")
        self.git("checkout", "-q", "target")
        # This change never belonged to the feature branch.
        self.write(
            "api_contracts/openapi/swagger.json",
            json.dumps(
                {
                    "paths": {"/existing/": {}},
                    "definitions": {"Existing": {"new": True}},
                }
            ),
        )
        self.write_zod("zod.number()")
        self.commit("unrelated target changes")
        self.git("update-ref", "refs/remotes/origin/dev", "HEAD")
        self.git("checkout", "-q", "feature")

    def write(self, name, content):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def write_schema(self):
        self.write("api_contracts/openapi/swagger.json", json.dumps(self.schema))

    def write_tools(self):
        self.write(
            "futureagi/mcp_server/catalog/tools.generated.json",
            json.dumps({"tools": self.tools, "tool_count": len(self.tools)}),
        )

    def write_zod(self, expression):
        self.write(
            "frontend/src/generated/api-contracts/api.zod.ts",
            f"export const ExistingResponse = {expression};\n",
        )

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.root, check=True, capture_output=True, text=True
        )

    def commit(self, message):
        self.git("add", ".")
        self.git(
            "-c",
            "user.name=Contract test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            message,
        )

    def audit(self, name):
        interpreter = sys.executable if name.endswith(".py") else "node"
        return subprocess.run(
            [interpreter, str(self.root / "api_contracts/tests" / name)],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_target_only_drift_does_not_fail_either_audit(self):
        for name in ("skills-generation-audit.py", "client-nullability-audit.mjs"):
            with self.subTest(name=name):
                result = self.audit(name)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_branch_schema_drift_still_fails(self):
        self.schema["definitions"]["Existing"] = {"changed": True}
        self.write_schema()
        result = self.audit("skills-generation-audit.py")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Changed existing definitions", result.stderr)

    def test_branch_non_null_shape_drift_still_fails(self):
        self.write_zod("zod.boolean()")
        result = self.audit("client-nullability-audit.mjs")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Non-null validation changed outside Skills", result.stderr)


if __name__ == "__main__":
    unittest.main()
