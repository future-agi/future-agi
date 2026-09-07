"""Offline safety/protocol tests only; no Django setup, sockets, or real databases.

Run from the repository root with the normal backend Python:
  python -m unittest discover -s futureagi/scripts/property_catalog_oss/tests/managed_smoke \
      -p test_relational_smoke.py -v
Real ORM migrations, qualification, and authentication are exercised only when
the parent explicitly integrates relational_smoke into its disposable live run.
"""

from __future__ import annotations

import ast
import copy
import json
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import relational_smoke as smoke
from workspace_smoke import fixture_scopes


class Row(SimpleNamespace):
    def save(self, **kwargs):
        self.saves += 1
        self.updated_at += 1

    def delete(self, **kwargs):
        self.deleted = True
        self.save(**kwargs)

    def refresh_from_db(self, **kwargs):
        pass


class Manager:
    def __init__(self):
        self.rows = {}

    def using(self, alias):
        assert alias == "default"
        return self

    def get_or_create(self, *, id, defaults):
        if id in self.rows:
            return self.rows[id], False
        self.rows[id] = Row(
            id=id, deleted=False, updated_at=1, saves=0, **copy.deepcopy(defaults)
        )
        return self.rows[id], True

    def get(self, **filters):
        matches = [
            row
            for row in self.rows.values()
            if all(str(getattr(row, k)) == str(v) for k, v in filters.items())
        ]
        if len(matches) != 1:
            raise RuntimeError("fixture preflight failed")
        return matches[0]


def response(items, *, catalog=False, cursor=None):
    result = {
        "metrics" if catalog else "values": items,
        "query_complete": True,
        "query_status": "complete",
        "has_more": cursor is not None,
        "next_cursor": cursor,
    }
    if catalog:
        result["query_provenance"] = "activated_property_catalog"
    return 200, {"result": result}


class RelationalSmokeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.run = SimpleNamespace(
            directory=Path(self.temporary.name),
            manifest={
                "application": True,
                "run_id": "0123456789abcdef",
                "ports": {"postgres": 15499},
            },
            owned=Mock(return_value=["owned-container"]),
        )
        self.original = {
            "organization_id": "00000000-0000-4000-8000-000000000001",
            "workspace_id": "00000000-0000-4000-8000-000000000002",
            "project_id": "00000000-0000-4000-8000-000000000003",
            "since": "2026-09-06T00:00:00+00:00",
        }
        self.others = fixture_scopes(self.run, self.original)
        self.plan = smoke.fixture_plan(self.run, self.original, self.others)
        self.write("source-fixture.json", self.original)
        self.write("workspace-fixtures.json", {"scopes": self.others})
        self.write("relational-fixture.json", self.plan)
        self.models = {}
        for name in (*self.plan["scopes"][0]["ids"], "project", "key"):
            manager = Manager()
            self.models[name] = SimpleNamespace(
                all_objects=manager, no_workspace_objects=manager, objects=manager
            )
        for scope in self.plan["scopes"]:
            self.models["project"].objects.get_or_create(
                id=scope["project_id"],
                defaults={
                    "organization_id": scope["organization_id"],
                    "workspace_id": scope["workspace_id"],
                    "trace_type": "observe",
                },
            )
            self.models["key"].objects.get_or_create(
                id=scope["workspace_id"],
                defaults={
                    "name": scope["key_name"],
                    "organization_id": scope["organization_id"],
                    "workspace_id": scope["workspace_id"],
                    "api_key": "test-key",
                    "secret_key": "test-secret",
                },
            )

    def write(self, name, payload):
        (self.run.directory / name).write_text(json.dumps(payload))

    def patches(self):
        from contextlib import ExitStack

        stack = ExitStack()
        stack.enter_context(patch.object(smoke, "_guard"))
        stack.enter_context(patch.object(smoke, "_models", return_value=self.models))
        stack.enter_context(patch.object(smoke, "_atomic", return_value=nullcontext()))
        return stack

    def seed(self):
        with self.patches():
            return smoke.seed(self.run)

    def passed(self, phase):
        self.write(
            f"catalog-relational-{phase}.json",
            {
                "status": "passed",
                "phase": phase,
                "fixture_sha256": smoke._digest(self.plan),
            },
        )

    def test_all_entrypoints_refuse_unowned_before_django_or_models(self):
        self.run.owned.return_value = []
        with patch.object(smoke, "_models") as models:
            for action in (
                lambda: smoke.seed(self.run),
                lambda: smoke.mutate(self.run, phase="updated"),
                lambda: smoke.verify(self.run, self.original, object()),
            ):
                with self.assertRaisesRegex(RuntimeError, "owned disposable"):
                    action()
            models.assert_not_called()

    def test_database_roles_dsn_and_managed_admission_are_exact(self):
        database = {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": "managed_smoke",
            "HOST": "127.0.0.1",
            "PORT": 15499,
            "USER": "smoke_admin",
        }
        smoke._database_guard(self.run, database, "off", write=True)
        reader = {**database, "USER": "property_catalog_oss_reader"}
        smoke._database_guard(self.run, reader, "managed", write=False)
        for bad in (
            {"NAME": "production"},
            {"HOST": "localhost"},
            {"HOST": "remote"},
            {"PORT": 5432},
            {"USER": "postgres"},
            {"ENGINE": "sqlite3"},
        ):
            with (
                self.subTest(bad=bad),
                self.assertRaisesRegex(RuntimeError, "isolated database"),
            ):
                smoke._database_guard(
                    self.run, {**database, **bad}, "managed", write=True
                )
        for db, mode, write in (
            (database, "managed", False),
            (reader, "off", False),
            (reader, "managed", True),
        ):
            with self.assertRaises(RuntimeError):
                smoke._database_guard(self.run, db, mode, write=write)

    def test_plan_is_repeatable_has_24_new_ids_and_exact_foreign_scopes(self):
        original_before = copy.deepcopy(self.original)
        self.assertEqual(
            self.plan, smoke.fixture_plan(self.run, self.original, self.others)
        )
        ids = [i for scope in self.plan["scopes"] for i in scope["ids"].values()]
        self.assertEqual(len(set(ids)), 24)
        self.assertFalse(set(ids) & set(self.original.values()))
        self.assertEqual(self.original, original_before)
        for bad in (
            [],
            list(reversed(self.others)),
            [
                {**self.others[0], "project_id": self.original["project_id"]},
                self.others[1],
            ],
        ):
            with self.assertRaisesRegex(RuntimeError, "exact owned"):
                smoke.fixture_plan(self.run, self.original, bad)
        self.run.manifest["run_id"] = "malformed"
        with self.assertRaisesRegex(RuntimeError, "run identity"):
            smoke.fixture_plan(self.run, self.original, self.others)

    def test_canonical_spec_required_fields_and_fk_dependency_order(self):
        scope = self.plan["scopes"][0]
        specs = smoke._specs(self.plan, scope)
        self.assertEqual(
            list(specs),
            [
                "template",
                "config",
                "agent",
                "run_test",
                "simulation",
                "label",
                "dataset",
                "column",
            ],
        )
        self.assertEqual(specs["config"]["project_id"], scope["project_id"])
        self.assertEqual(
            specs["simulation"]["eval_template_id"], scope["ids"]["template"]
        )
        self.assertEqual(specs["simulation"]["run_test_id"], scope["ids"]["run_test"])
        self.assertFalse(specs["agent"]["inbound"])
        self.assertEqual(specs["column"]["source"], "OTHERS")
        self.assertEqual(specs["column"]["dataset_id"], scope["ids"]["dataset"])
        self.assertEqual(
            set(specs["label"]["settings"]),
            {"options", "rule_prompt", "multi_choice", "auto_annotate", "strategy"},
        )
        self.assertEqual(len(specs["label"]["settings"]["options"]), 2)

    def test_seed_normal_models_exact_retry_no_relabel_or_original_changes(self):
        source_before = (self.run.directory / "source-fixture.json").read_bytes()
        self.assertEqual(self.seed(), self.plan)
        self.assertEqual(self.seed(), self.plan)
        count = sum(
            len(self.models[name].all_objects.rows)
            for name in self.plan["scopes"][0]["ids"]
        )
        self.assertEqual(count, 24)
        self.assertEqual(
            (self.run.directory / "source-fixture.json").read_bytes(), source_before
        )
        scope = self.plan["scopes"][0]
        row = self.models["template"].all_objects.rows[scope["ids"]["template"]]
        row.name = "different preexisting record"
        with self.assertRaisesRegex(RuntimeError, "not relabeling"):
            self.seed()
        self.assertEqual(row.name, "different preexisting record")
        self.assertEqual(row.saves, 0)

    def test_seed_refuses_tampered_plan_and_missing_project_before_creation(self):
        bad = copy.deepcopy(self.plan)
        bad["scopes"][0]["ids"]["template"] = self.original["project_id"]
        self.write("relational-fixture.json", bad)
        with self.assertRaisesRegex(RuntimeError, "different relational"):
            self.seed()
        self.write("relational-fixture.json", self.plan)
        self.models["project"].objects.rows.clear()
        with self.assertRaisesRegex(RuntimeError, "preflight"):
            self.seed()
        self.assertEqual(self.models["template"].objects.rows, {})

    def test_mutations_require_successful_previous_real_api_stage(self):
        self.seed()
        for phase in ("updated", "deleted"):
            with self.patches(), self.assertRaises(FileNotFoundError):
                smoke.mutate(self.run, phase=phase)
        self.passed("seeded")
        self.write(
            "catalog-relational-seeded.json",
            {"status": "passed", "phase": "seeded", "fixture_sha256": "foreign"},
        )
        with self.patches(), self.assertRaisesRegex(RuntimeError, "not proven"):
            smoke.mutate(self.run, phase="updated")

    def test_update_and_parent_delete_keep_child_ids_clocks_and_foreign_rows(self):
        self.seed()
        primary = self.plan["scopes"][0]
        foreign_before = {
            kind: copy.deepcopy(vars(row))
            for kind, model in self.models.items()
            for identity, row in model.objects.rows.items()
            if identity in self.plan["scopes"][1]["ids"].values()
        }
        self.passed("seeded")
        with self.patches():
            result = smoke.mutate(self.run, phase="updated")
            self.assertEqual(result["touched"], ["template", "label", "column"])
            self.assertEqual(smoke.mutate(self.run, phase="updated")["touched"], [])
        self.passed("updated")
        with self.patches():
            result = smoke.mutate(self.run, phase="deleted")
            self.assertEqual(result["touched"], ["template", "label", "dataset"])
            self.assertEqual(smoke.mutate(self.run, phase="deleted")["touched"], [])
        for kind in ("config", "simulation", "column"):
            row = self.models[kind].objects.rows[primary["ids"][kind]]
            self.assertFalse(row.deleted)  # Tombstones must derive from parents.
            if kind != "column":
                self.assertEqual(row.updated_at, 1)
        for kind, before in foreign_before.items():
            row = self.models[kind].objects.rows[before["id"]]
            self.assertEqual(vars(row), before)

    def test_mutation_refuses_tampered_fk_without_touching_it(self):
        self.seed()
        self.passed("seeded")
        row = self.models["config"].objects.rows[
            self.plan["scopes"][0]["ids"]["config"]
        ]
        row.project_id = self.plan["scopes"][1]["project_id"]
        with (
            self.patches(),
            self.assertRaisesRegex(RuntimeError, "exact relational fields"),
        ):
            smoke.mutate(self.run, phase="updated")
        self.assertEqual(row.saves, 0)

    def test_api_identity_contract_simulation_is_eval_config_not_relabelled(self):
        scope = self.plan["scopes"][0]
        expected = smoke._expected(self.plan, scope, "seeded")
        self.assertEqual(len(expected), 5)
        simulation = expected["eval_config:" + scope["ids"]["simulation"]]
        self.assertEqual(simulation["source"], "simulation")
        self.assertEqual(simulation["type"], "number")
        self.assertEqual(simulation["output_type"], "CHOICES")
        column = expected["dataset_column:" + scope["ids"]["column"]]
        self.assertEqual((column["source"], column["role"]), ("datasets", "dimension"))
        queries = smoke._definition_queries(self.plan, scope)
        self.assertEqual(queries["template"]["per_eval_config"], "false")
        self.assertEqual(queries["config"]["per_eval_config"], "true")
        self.assertEqual(
            queries["simulation"]["agent_definition_id"], scope["ids"]["agent"]
        )
        self.assertNotIn("project_ids", queries["column"])
        updated = smoke._expected(self.plan, scope, "updated")
        self.assertEqual(set(updated), set(expected))
        self.assertNotEqual(
            updated[simulation["property_id"]]["choices"], simulation["choices"]
        )

    def test_native_pages_do_not_require_catalog_provenance_and_forward_cursor(self):
        first, second = {"value": "a", "label": "A"}, {"value": "b", "label": "B"}
        with patch.object(
            smoke,
            "request",
            side_effect=[response([first], cursor="signed"), response([second])],
        ) as get:
            self.assertEqual(
                smoke._pages(
                    object(), "filter_values", {"page_size": 1}, catalog=False
                ),
                [first, second],
            )
        self.assertEqual(
            get.call_args_list[1].args[2], {"page_size": 1, "cursor": "signed"}
        )

    def test_pages_reject_http_malformed_incomplete_duplicates_and_bad_cursors(self):
        complete = response([{"value": "a"}])
        cases = [
            [(503, {"result": {}})],
            [(200, {"result": "bad"})],
            [(200, {"result": {"values": [], "query_complete": False}})],
            [response([{"value": "a"}, {"value": "a"}])],
            [
                response([{"value": "a"}], cursor="same"),
                response([{"value": "b"}], cursor="same"),
            ],
            [(200, {"result": {**complete[1]["result"], "has_more": True}})],
        ]
        for sequence in cases:
            with (
                self.subTest(sequence=sequence),
                patch.object(smoke, "request", side_effect=sequence),
                self.assertRaises(RuntimeError),
            ):
                smoke._pages(object(), "filter_values", {}, catalog=False)

    def test_pages_are_bounded_and_empty_native_shortcut_is_explicit(self):
        pages = [response([{"value": str(i)}], cursor=str(i)) for i in range(8)]
        with (
            patch.object(smoke, "request", side_effect=pages),
            self.assertRaisesRegex(RuntimeError, "eight pages"),
        ):
            smoke._pages(object(), "filter_values", {}, catalog=False)
        with patch.object(
            smoke, "request", return_value=(200, {"result": {"values": []}})
        ):
            self.assertEqual(
                smoke._pages(
                    object(), "filter_values", {}, catalog=False, allow_empty=True
                ),
                [],
            )
            with self.assertRaises(RuntimeError):
                smoke._pages(object(), "filter_values", {}, catalog=False)

    def test_catalog_pages_require_selected_provenance_and_typed_pending(self):
        pending = {
            "query_provenance": "property_catalog_bootstrap",
            "query_status": "pending",
            "query_complete": False,
            "query_exact": False,
            "metrics": [],
        }
        with patch.object(smoke, "request", return_value=(200, {"result": pending})):
            self.assertIsNone(smoke._pages(object(), "metrics", {}, catalog=True))
        for result in (
            {**pending, "query_complete": True},
            response([], catalog=False)[1]["result"],
        ):
            with (
                patch.object(smoke, "request", return_value=(200, {"result": result})),
                self.assertRaises(RuntimeError),
            ):
                smoke._pages(object(), "metrics", {}, catalog=True)

    def definition_rows(self, scope, phase):
        expected = smoke._expected(self.plan, scope, phase)
        return [
            [
                v
                for v in expected.values()
                if v["property_id"]
                in {
                    smoke.FAMILIES[k][0] + ":" + scope["ids"][k]
                    for k in (
                        (family, "template") if family == "simulation" else (family,)
                    )
                }
            ]
            for family in smoke.FAMILIES
        ]

    def test_five_definition_queries_converge_without_accepting_stale_choices(self):
        scope = self.plan["scopes"][0]
        rows = self.definition_rows(scope, "seeded")
        with patch.object(smoke, "_pages", side_effect=rows):
            self.assertTrue(smoke._definitions(object(), self.plan, scope, "seeded"))
        with patch.object(smoke, "_pages", side_effect=rows):
            self.assertFalse(smoke._definitions(object(), self.plan, scope, "updated"))
        for missing in (None, []):
            with patch.object(smoke, "_pages", return_value=missing):
                self.assertFalse(
                    smoke._definitions(object(), self.plan, scope, "seeded")
                )

    def test_foreign_catalog_row_fails_immediately_not_as_convergence(self):
        foreign = next(
            iter(smoke._expected(self.plan, self.plan["scopes"][1], "seeded").values())
        )
        with (
            patch.object(smoke, "_pages", return_value=[foreign]),
            self.assertRaisesRegex(RuntimeError, "foreign relational"),
        ):
            smoke._definitions(object(), self.plan, self.plan["scopes"][0], "seeded")

    def test_configured_value_checks_assert_codes_search_and_no_dataset_value_call(
        self,
    ):
        scope = self.plan["scopes"][0]
        specs = smoke._specs(self.plan, scope)
        replies = []
        for kind in ("template", "config", "simulation", "label"):
            options = (
                specs["label"]["settings"]["options"]
                if kind == "label"
                else [{"value": v, "label": v} for v in specs["template"]["choices"]]
            )
            replies.extend([options, [options[1]]])
        with patch.object(smoke, "_pages", side_effect=replies) as pages:
            smoke._values(object(), self.plan, scope, "seeded")
        self.assertEqual(pages.call_count, 8)
        self.assertTrue(
            all(
                "dataset_column:" not in c.args[2]["property_id"]
                for c in pages.call_args_list
            )
        )
        with (
            patch.object(smoke, "_pages", return_value=[{"value": "foreign-choice"}]),
            self.assertRaisesRegex(RuntimeError, "wrong relational"),
        ):
            smoke._values(object(), self.plan, scope, "seeded")

    def test_deleted_definitions_and_native_options_must_be_empty(self):
        scope = self.plan["scopes"][0]
        with patch.object(smoke, "_pages", return_value=[]) as pages:
            self.assertTrue(smoke._definitions(object(), self.plan, scope, "deleted"))
            smoke._values(object(), self.plan, scope, "deleted")
        self.assertEqual(pages.call_count, 9)
        self.assertEqual(
            len(smoke._expected(self.plan, self.plan["scopes"][1], "deleted")), 5
        )

    def test_foreign_values_and_scope_errors_are_not_mistaken_for_empty_success(self):
        with (
            patch.object(smoke, "_pages", return_value=[]),
            patch.object(smoke, "request", return_value=(400, {})) as get,
        ):
            self.assertEqual(
                smoke._foreign_checks({"original": object()}, self.plan), [400] * 6
            )
            self.assertEqual(get.call_count, 6)
        with (
            patch.object(smoke, "_pages", return_value=[{"value": "leak"}]),
            self.assertRaisesRegex(RuntimeError, "foreign relational"),
        ):
            smoke._foreign_checks({"original": object()}, self.plan)
        with (
            patch.object(smoke, "_pages", return_value=[]),
            patch.object(smoke, "request", return_value=(503, {})),
            self.assertRaisesRegex(RuntimeError, "scope not rejected"),
        ):
            smoke._foreign_checks({"original": object()}, self.plan)

    def test_real_client_construction_uses_scoped_credentials_without_auth_bypass(self):
        rest = ModuleType("rest_framework")
        rest.__path__ = []
        api = ModuleType("rest_framework.test")
        api.APIClient = Mock(side_effect=lambda: SimpleNamespace(credentials=Mock()))
        key = self.models["key"].objects.rows[self.original["workspace_id"]]
        with (
            patch.dict(
                "sys.modules", {"rest_framework": rest, "rest_framework.test": api}
            ),
            patch.object(smoke, "_models", return_value=self.models),
        ):
            clients = smoke._clients(self.plan, key)
            for scope in self.plan["scopes"]:
                clients[scope["label"]].credentials.assert_called_once_with(
                    HTTP_X_API_KEY="test-key",
                    HTTP_X_SECRET_KEY="test-secret",
                    HTTP_X_WORKSPACE_ID=scope["workspace_id"],
                )
            key.workspace_id = self.plan["scopes"][1]["workspace_id"]
            with self.assertRaisesRegex(RuntimeError, "key belongs to another scope"):
                smoke._clients(self.plan, key)

    def test_verify_polls_only_definitions_and_reports_real_coverage_boundaries(self):
        clients = {s["label"]: object() for s in self.plan["scopes"]}
        with (
            self.patches(),
            patch.object(smoke, "_clients", return_value=clients),
            patch.object(
                smoke, "_definitions", side_effect=[False, True, True, True]
            ) as definitions,
            patch.object(smoke, "_values") as values,
            patch.object(smoke, "_foreign_checks", return_value=[400] * 6),
            patch.object(smoke.time, "sleep"),
        ):
            result = smoke.verify(self.run, self.original, object())
        self.assertEqual(definitions.call_count, 4)
        self.assertEqual(values.call_count, 3)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["dataset_values"], "definitions_only_not_tested")
        self.assertEqual(sum(len(v) for v in result["property_ids"].values()), 15)
        self.assertEqual(
            json.loads(
                (self.run.directory / "catalog-relational-seeded.json").read_text()
            ),
            result,
        )

    def test_verify_timeout_and_api_failure_write_failed_not_passed_reports(self):
        clients = {s["label"]: object() for s in self.plan["scopes"]}
        for failure in (False, RuntimeError("API failed")):
            with (
                self.patches(),
                patch.object(smoke, "_clients", return_value=clients),
                patch.object(
                    smoke,
                    "_definitions",
                    side_effect=[failure] if failure is False else failure,
                ),
                patch.object(smoke, "_values") as values,
                patch.object(smoke.time, "monotonic", side_effect=[0, 100, 101]),
                self.assertRaises(RuntimeError),
            ):
                smoke.verify(self.run, self.original, object(), timeout=1)
            values.assert_not_called()
            report = json.loads(
                (self.run.directory / "catalog-relational-seeded.json").read_text()
            )
            self.assertEqual(report["status"], "failed")

    def test_verify_rejects_bad_phase_timeout_fixture_and_missing_mutation(self):
        with self.patches():
            for kwargs in (
                {"phase": "fake"},
                {"timeout": 0},
                {"timeout": 301},
                {"timeout": float("nan")},
            ):
                with self.assertRaises(ValueError):
                    smoke.verify(self.run, self.original, object(), **kwargs)
            with self.assertRaisesRegex(RuntimeError, "different source"):
                smoke.verify(self.run, {}, object())
            with self.assertRaises(FileNotFoundError):
                smoke.verify(self.run, self.original, object(), phase="updated")

    def test_successful_checks_cannot_pass_at_or_after_shared_deadline(self):
        clients = {s["label"]: object() for s in self.plan["scopes"]}
        for stage in ("definitions", "values", "foreign"):
            for elapsed in (89.999, 90, 91):
                now = [0]

                def finish(name, result, *, stage=stage, now=now, elapsed=elapsed):
                    def check(*args, deadline):
                        self.assertEqual(deadline, 90)
                        if name == stage:
                            now[0] = elapsed
                        return result

                    return check

                with (
                    self.subTest(stage=stage, elapsed=elapsed),
                    self.patches(),
                    patch.object(
                        smoke.time, "monotonic", side_effect=lambda now=now: now[0]
                    ),
                    patch.object(smoke, "_clients", return_value=clients),
                    patch.object(
                        smoke, "_definitions", side_effect=finish("definitions", True)
                    ),
                    patch.object(smoke, "_values", side_effect=finish("values", None)),
                    patch.object(
                        smoke,
                        "_foreign_checks",
                        side_effect=finish("foreign", [400] * 6),
                    ),
                ):
                    if elapsed < 90:
                        smoke.verify(self.run, self.original, object())
                    else:
                        with self.assertRaisesRegex(RuntimeError, "bounded wall"):
                            smoke.verify(self.run, self.original, object())
                report = json.loads(
                    (self.run.directory / "catalog-relational-seeded.json").read_text()
                )
                self.assertEqual(
                    report["status"], "passed" if elapsed < 90 else "failed"
                )

    def test_value_page_overrun_stops_before_next_request(self):
        now = [0]

        def slow(*args):
            now[0] = 90
            return response([{"value": "a"}], cursor="next")

        with (
            patch.object(smoke.time, "monotonic", side_effect=lambda: now[0]),
            patch.object(smoke, "request", side_effect=slow) as read,
            self.assertRaisesRegex(RuntimeError, "bounded wall"),
        ):
            smoke._pages(object(), "filter_values", {}, catalog=False, deadline=90)
        self.assertEqual(read.call_count, 1)

    def test_scope_denial_overrun_stops_before_next_request(self):
        now = [0]

        def slow(*args):
            now[0] = 90
            return 400, {}

        with (
            patch.object(smoke.time, "monotonic", side_effect=lambda: now[0]),
            patch.object(smoke, "_pages", return_value=[]),
            patch.object(smoke, "request", side_effect=slow) as read,
            self.assertRaisesRegex(RuntimeError, "bounded wall"),
        ):
            smoke._foreign_checks({"original": object()}, self.plan, deadline=90)
        self.assertEqual(read.call_count, 1)

    def test_module_has_no_infrastructure_ingestion_catalog_or_activation_writer(self):
        tree = ast.parse(Path(smoke.__file__).read_text())
        calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertFalse(
            calls
            & {
                "execute",
                "execute_ch_query",
                "post",
                "put",
                "bulk_create",
                "bulk_update",
                "update",
                "force_authenticate",
            }
            - {"update"}
        )
        imports = [
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        ]
        self.assertFalse(any("property_catalog" in module for module in imports))
        self.assertNotIn("subprocess", imports)
        self.assertNotIn("requests", imports)


if __name__ == "__main__":
    unittest.main()
