"""Contract audits must ignore target-only changes, but reject branch drift."""

import copy
import hashlib
import json
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


SWAGGER = "api_contracts/openapi/swagger.json"
MANIFEST = "futureagi/mcp_server/catalog/tools.generated.json"
SESSION = "/tracer/trace/list_traces_of_session/"
PROJECTION = "/tracer/trace/skill-projection/"
FILTER = "DashboardMetric"
GRAPH = "ObserveGraphDataResult"
QUERY = "TraceObserveListQuery"
METADATA = "TraceSessionListMetadata"
OLD_DESCRIPTION = "Column family such as SYSTEM_METRIC, SPAN_ATTRIBUTE, EVAL_METRIC, ANNOTATION, or NORMAL."


class AuditFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Exercise small slices of the checked-in generated outputs, not a fake
        # '120 tools' manifest. Reconstruct the pre-feature fields below so the
        # fixture has its own fork point independent of checkout history.
        document = json.loads((ROOT / SWAGGER).read_bytes())
        names = runpy.run_path(
            str(ROOT / "api_contracts/tests/skills-generation-audit.py")
        )["RUNTIME_DEFS"]
        cls.additions = {k: document["definitions"][k] for k in names}
        cls.schema_after = {
            "securityDefinitions": document["securityDefinitions"],
            "paths": {
                "/existing/": {},
                SESSION: document["paths"][SESSION],
                PROJECTION: document["paths"][PROJECTION],
                "/model-hub/skills/": {},
            },
            "definitions": {
                "Existing": {},
                "SkillsExample": {},
                **cls.additions,
                **{
                    k: document["definitions"][k]
                    for k in (FILTER, GRAPH, QUERY, METADATA)
                },
            },
        }
        tools = json.loads((ROOT / MANIFEST).read_bytes())["tools"]
        cls.tools_after = [
            {"name": "existing"},
            *(
                t
                for t in tools
                if t["group"] == "skills"
                or t["name"] in ("search_traces", "query_dashboard_metrics")
            ),
        ]

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for name in (
            "skills-generation-audit.py",
            "trace-zod-delta.mjs",
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
        self.schema = copy.deepcopy(self.schema_after)
        del self.schema["securityDefinitions"]["ContextCredential"]
        for k in (PROJECTION, "/model-hub/skills/"):
            del self.schema["paths"][k]
        for k in (*self.additions, "SkillsExample"):
            del self.schema["definitions"][k]
        self.schema["paths"][SESSION]["get"]["parameters"].pop(0)
        defs = self.schema["definitions"]
        self.remove_filter_delta(defs[FILTER])
        for k in ("series", "split_by", "totals_note"):
            del defs[GRAPH]["properties"][k]
        defs[GRAPH]["properties"]["metric_statistic"]["enum"].remove("p95")
        del defs[QUERY]["properties"]["include_skill_links"]
        del defs[METADATA]["properties"]["skill_links_exist"]
        self.tools = copy.deepcopy(
            [t for t in self.tools_after if t.get("group") != "skills"]
        )
        search = self.tool("search_traces")
        del search["inputSchema"]["properties"]["include_skill_links"]
        search["request"]["parameters"]["query"].remove("include_skill_links")
        dashboard = self.tool("query_dashboard_metrics")["inputSchema"]
        self.remove_filter_delta(dashboard)
        self.remove_filter_delta(dashboard["definitions"][FILTER])
        self.write_schema()
        self.write_zod("zod.string()")
        self.git("init", "-q")
        self.commit("base")
        self.git("branch", "target")
        self.git("checkout", "-qb", "feature")
        self.schema = copy.deepcopy(self.schema_after)
        self.tools = copy.deepcopy(self.tools_after)
        self.write_schema()
        self.commit("feature contracts")
        self.git("checkout", "-q", "target")
        # The target alone changes a schema, an existing tool and its Zod shape.
        target_schema = json.loads((self.root / SWAGGER).read_bytes())
        target_schema["definitions"]["Existing"] = {"new": True}
        self.write(SWAGGER, json.dumps(target_schema))
        target_tools = json.loads((self.root / MANIFEST).read_bytes())
        target_tools["tools"][0]["description"] = "target only"
        self.write(MANIFEST, json.dumps(target_tools))
        self.write_zod("zod.number()")
        self.commit("unrelated target changes")
        self.git("update-ref", "refs/remotes/origin/dev", "HEAD")
        self.git("checkout", "-q", "feature")

    @staticmethod
    def remove_filter_delta(schema):
        props = schema["properties"]["filters"]["items"]["properties"]
        del props["group"]
        props["filter_config"]["properties"]["col_type"]["description"] = (
            OLD_DESCRIPTION
        )

    def tool(self, name):
        return next(t for t in self.tools if t["name"] == name)

    def write(self, name, content):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def write_schema(self):
        self.write(SWAGGER, json.dumps(self.schema))
        self.write_tools()

    def write_tools(self):
        self.write(
            MANIFEST,
            json.dumps(
                {
                    "tools": self.tools,
                    "tool_count": len(self.tools),
                    "source_contract_sha256": hashlib.sha256(
                        (self.root / SWAGGER).read_bytes()
                    ).hexdigest(),
                }
            ),
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
            check=False,
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def assert_rejected(self, name, message):
        result = self.audit(name)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(message, result.stderr)


class AuditBaselineTests(AuditFixture):
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


class TraceDeltaTests(AuditFixture):
    def reject_schema(self, message):
        self.write_schema()
        self.assert_rejected("skills-generation-audit.py", message)

    def test_session_parameter_is_exact_and_route_scoped(self):
        for key, value in (
            ("required", True),
            ("required", 0),
            ("type", "string"),
            ("in", "header"),
        ):
            with self.subTest(key=key, value=value):
                self.schema = copy.deepcopy(self.schema_after)
                self.schema["paths"][SESSION]["get"]["parameters"][0][key] = value
                self.reject_schema("Changed existing paths")
        self.schema = copy.deepcopy(self.schema_after)
        self.schema["paths"]["/existing/"] = {
            "get": {
                "parameters": [self.schema["paths"][SESSION]["get"]["parameters"][0]]
            }
        }
        self.reject_schema("Changed existing paths: /existing/")

    def test_existing_definition_exceptions_are_field_scoped(self):
        for mutation in (
            "group required",
            "group bound",
            "family",
            "metric enum",
            "split enum",
            "series ref",
            "unrelated",
            "metadata type",
            "query required",
        ):
            with self.subTest(mutation=mutation):
                self.schema = copy.deepcopy(self.schema_after)
                defs = self.schema["definitions"]
                item = defs[FILTER]["properties"]["filters"]["items"]
                graph = defs[GRAPH]["properties"]
                if mutation == "group required":
                    item["required"].append("group")
                elif mutation == "group bound":
                    item["properties"]["group"]["minimum"] = False
                elif mutation == "family":
                    item["properties"]["filter_config"]["properties"]["col_type"][
                        "description"
                    ] = OLD_DESCRIPTION
                elif mutation == "metric enum":
                    graph["metric_statistic"]["enum"].remove("mean")
                elif mutation == "split enum":
                    graph["split_by"]["enum"].append("project")
                elif mutation == "series ref":
                    graph["series"]["items"]["$ref"] = "#/definitions/TraceSkill"
                elif mutation == "unrelated":
                    graph["extra"] = {"type": "boolean"}
                elif mutation == "metadata type":
                    defs[METADATA]["properties"]["skill_links_exist"]["type"] = (
                        "integer"
                    )
                else:
                    defs[QUERY].setdefault("required", []).append("include_skill_links")
                self.reject_schema("Changed existing definitions")

    def test_trace_additions_require_the_reviewed_name_and_shape(self):
        for group, name, mutation in (
            ("paths", PROJECTION, "method"),
            ("definitions", "TraceSkillEntry", "required"),
            ("definitions", "RuntimeUsageResult", "missing"),
            ("definitions", "TraceOther", "extra"),
        ):
            with self.subTest(name=name):
                self.schema = copy.deepcopy(self.schema_after)
                entries = self.schema[group]
                if mutation == "method":
                    entries[name]["get"] = entries[name].pop("post")
                elif mutation == "required":
                    entries[name]["required"].append("not_reviewed")
                elif mutation == "missing":
                    del entries[name]
                else:
                    entries[name] = {}
                self.reject_schema(
                    f"added {group}"
                    if mutation != "missing"
                    else f"Missing reviewed {group}"
                )

    def test_wrong_before_field_is_not_admitted(self):
        self.git("checkout", "-q", "feature~1", "--", SWAGGER, MANIFEST)
        schema = json.loads((self.root / SWAGGER).read_bytes())
        item = schema["definitions"][FILTER]["properties"]["filters"]["items"]
        item["properties"]["filter_config"]["properties"]["col_type"]["description"] = (
            "different old family"
        )
        self.write(SWAGGER, json.dumps(schema))
        self.commit("different filter baseline")
        self.git("update-ref", "refs/remotes/origin/dev", "HEAD")
        self.reject_schema("Changed existing definitions: DashboardMetric")

    def test_tool_deltas_are_name_and_location_scoped(self):
        for mutation, error in (
            ("renamed", "Unexpected MCP tool set"),
            ("duplicate", "Duplicate MCP tool"),
            ("wrong route", "Changed new Skills MCP tool"),
            ("new tool type", "Changed new Skills MCP tool"),
            ("wrong tool", "Changed existing MCP tools"),
            ("required", "Changed existing MCP tools"),
            ("nested", "Changed existing MCP tools"),
            ("mapping", "Changed existing MCP tools"),
            ("tool metadata", "Changed existing MCP tools"),
        ):
            with self.subTest(mutation=mutation):
                self.tools = copy.deepcopy(self.tools_after)
                search = self.tool("search_traces")
                if mutation == "renamed":
                    self.tool("get_agent_context")["name"] = "unreviewed_name"
                elif mutation == "duplicate":
                    self.tool("get_agent_context")["name"] = "list_skills"
                elif mutation == "wrong route":
                    self.tool("get_agent_context")["request"]["path"] = SESSION
                elif mutation == "new tool type":
                    self.tool("get_agent_context")["inputSchema"]["properties"][
                        "context_id"
                    ]["type"] = "number"
                elif mutation == "wrong tool":
                    self.tool("existing")["inputSchema"] = search["inputSchema"]
                elif mutation == "required":
                    search["inputSchema"].setdefault("required", []).append(
                        "include_skill_links"
                    )
                elif mutation == "nested":
                    nested = self.tool("query_dashboard_metrics")["inputSchema"][
                        "definitions"
                    ][FILTER]
                    nested["properties"]["filters"]["items"]["properties"]["group"][
                        "type"
                    ] = "number"
                elif mutation == "mapping":
                    search["request"]["parameters"]["query"].remove(
                        "include_skill_links"
                    )
                else:
                    search["annotations"]["readOnlyHint"] = False
                self.write_tools()
                self.assert_rejected("skills-generation-audit.py", error)

    def test_zod_delta_is_bound_to_declaration_path_and_validators(self):
        route = "TracerTraceListTracesOfSessionQueryParams"
        path = "frontend/src/generated/api-contracts/api.zod.ts"

        def decl(name, fields):
            return f"export const {name} = zod.object({{{fields}}});\n"

        self.write(path, decl(route, "limit: zod.number()") + decl("OtherQuery", ""))
        self.commit("session query baseline")
        self.git("update-ref", "refs/remotes/origin/dev", "HEAD")
        flag = "include_skill_links: zod.boolean().optional()"
        good = decl(route, "limit: zod.number(), " + flag) + decl("OtherQuery", "")
        self.write(path, good)
        result = self.audit("client-nullability-audit.mjs")
        self.assertEqual(result.returncode, 0, result.stderr)
        for bad in (
            good.replace(".optional()", ""),
            good.replace("boolean()", "string()"),
            good.replace("number()", "string()"),
            good.replace(flag, f"nested: zod.object({{{flag}}})"),
            decl(route, "limit: zod.number()") + decl("OtherQuery", flag),
        ):
            with self.subTest(bad=bad):
                self.write(path, bad)
                self.assert_rejected(
                    "client-nullability-audit.mjs",
                    "Non-null validation changed outside Skills",
                )


class ProtectedReferenceTests(unittest.TestCase):
    def test_shared_definition_is_immutable_when_reachable_from_protected_operation(self):
        module = runpy.run_path(str(ROOT / "api_contracts/tests/skills-generation-audit.py"))
        baseline = {
            "paths": {"/model-hub/develops/{dataset_id}/get-row-data/": {
                "post": {"parameters": [{"schema": {"$ref": "#/definitions/Envelope"}}]}
            }},
            "definitions": {"Envelope": {"allOf": [{"$ref": "#/definitions/Shared"}]},
                            "Shared": {"type": "object", "properties": {}}},
        }
        module["audit_unchanged_shared_routes"](baseline, copy.deepcopy(baseline))
        changed = copy.deepcopy(baseline)
        changed["definitions"]["Shared"]["properties"]["group"] = {"type": "integer"}
        with self.assertRaisesRegex(module["Drift"], "Changed protected definition: Shared"):
            module["audit_unchanged_shared_routes"](baseline, changed)


class BillingReconciliationTests(AuditFixture):
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
