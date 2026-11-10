"""Contract audits must ignore target-only changes, but reject branch drift."""

import copy
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
        for name in (
            "skills-generation-audit.py",
            "client-nullability-audit.mjs",
            "ee-autoreload-contract-delta.json",
        ):
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


class BillingReconciliationTests(AuditBaselineTests):
    def setUp(self):
        super().setUp()
        self.delta = json.loads(
            (ROOT / "api_contracts/tests/ee-autoreload-contract-delta.json").read_text()
        )
        self.apply_billing("before")
        self.commit("billing baseline")
        self.git("update-ref", "refs/remotes/origin/dev", "HEAD")
        self.apply_billing("after")

    def apply_billing(self, side):
        for group, entries in self.delta["swagger"].items():
            for name, pair in entries.items():
                self.schema[group][name] = copy.deepcopy(pair[side])
        self.write_schema()
        self.write(
            "frontend/src/generated/api-contracts/api.zod.ts",
            "export const ExistingResponse = zod.string();\n"
            + "\n".join(
                f"export const {pair[side]};" for pair in self.delta["zod"].values()
            ),
        )

    def assert_rejected(self, name, message):
        result = self.audit(name)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(message, result.stderr)

    def test_exact_billing_pair_passes_both_audits(self):
        for name in ("skills-generation-audit.py", "client-nullability-audit.mjs"):
            result = self.audit(name)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("exact EE billing reconciliations", result.stdout)

    def test_swagger_billing_mutations_still_fail(self):
        baseline = copy.deepcopy(self.schema)
        for mutation in ("default", "type", "required", "extra", "removed"):
            with self.subTest(mutation=mutation):
                self.schema = copy.deepcopy(baseline)
                request = self.schema["definitions"]["AutoReloadSettingsRequest"]
                if mutation == "default":
                    request["properties"]["autoreload_charge_now"]["default"] = True
                elif mutation == "type":
                    request["properties"]["autoreload_maxcharge"]["type"] = "number"
                elif mutation == "required":
                    request["required"].append("autoreload_consent")
                elif mutation == "extra":
                    request["properties"]["unexpected"] = {"type": "string"}
                else:
                    del request["properties"]["autoreload_consent"]
                self.write_schema()
                self.assert_rejected(
                    "skills-generation-audit.py", "Changed existing definitions"
                )

    def test_billing_response_change_still_fails(self):
        route = self.schema["paths"]["/usage/update-auto-reload-settings/"]
        route["post"]["responses"]["200"]["schema"] = {"type": "string"}
        self.write_schema()
        self.assert_rejected("skills-generation-audit.py", "Changed existing paths")

    def test_zod_billing_mutations_still_fail(self):
        name = "frontend/src/generated/api-contracts/api.zod.ts"
        original = (self.root / name).read_text()
        for old, new in (
            ("default ( false )", "default ( true )"),
            ("zod . string ( ) . optional ( )", "zod . number ( ) . optional ( )"),
            (". optional ( )", ""),
            ("autoreload_consent", "unexpected_consent"),
        ):
            with self.subTest(new=new):
                self.write(name, original.replace(old, new))
                self.assert_rejected(
                    "client-nullability-audit.mjs",
                    "Non-null validation changed outside Skills",
                )

    def test_different_before_shape_is_not_reconciled(self):
        self.apply_billing("before")
        request = self.schema["definitions"]["AutoReloadSettingsRequest"]
        request["required"].remove("autoreload_enabled")
        self.write_schema()
        path = "frontend/src/generated/api-contracts/api.zod.ts"
        self.write(path, (self.root / path).read_text().replace("boolean", "string"))
        self.commit("different billing baseline")
        self.git("update-ref", "refs/remotes/origin/dev", "HEAD")
        self.apply_billing("after")
        self.assert_rejected(
            "skills-generation-audit.py", "Changed existing definitions"
        )
        self.assert_rejected(
            "client-nullability-audit.mjs", "Non-null validation changed outside Skills"
        )

    def test_reconciled_target_needs_no_exception(self):
        self.commit("target already includes billing")
        self.git("update-ref", "refs/remotes/origin/dev", "HEAD")
        for name in ("skills-generation-audit.py", "client-nullability-audit.mjs"):
            result = self.audit(name)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("0 exact EE billing reconciliations", result.stdout)


if __name__ == "__main__":
    unittest.main()
