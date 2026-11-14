"""Complete 51/56-pending-migration A9 intersection regression, without Django.

Run the shipped touched()/inbound() resolver against AST-derived operation and
ModelState doubles. No migration module, RunPython, SQL or app is executed.
This guards the prior incomplete hand inventory, not a substitute for the
native B MigrationLoader scope probe or PostgreSQL constraint observation.
"""

import ast
import copy
import re
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from deploy.tests.test_update_usage_reference import REVISIONS, ROOT, source
from scripts import rehearse_update_compatibility as p

LATEST = (
    ("accounts", "0029_demotracepublication_demoseedrequest"),
    ("accounts", "0030_queue_existing_demo_trace_publications"),
    ("accounts", "0031_orgapikey_coding_agent"),
    ("usage", "0030_add_jev_evaluator_call_type"),
    ("tracer", "0116_insight_reports"),
    ("tracer", "0117_instant_eval_scale"),
    ("tracer", "0118_instant_eval_answers"),
    ("tracer", "0119_instant_eval_scope_preset"),
    ("tracer", "0120_merge_error_feed_alerts_and_instant_eval"),
    ("tracer", "0121_remove_insightconfig_schedule"),
    ("tracer", "0122_merge_instant_eval_and_simulation_project"),
    ("tracer", "0123_merge_instant_eval_and_shared_link_resource"),
    ("tracer", "0124_merge_analysis_and_alert_high_water"),
    ("tracer", "0125_insightrun_source_started_at"),
    ("tracer", "0126_analysis_source_arrival"),
    ("tracer", "0127_project_analysis_source_generation"),
    ("tracer", "0128_instantevalrow_source_session_ids"),
    ("tracer", "0129_project_analysis_source_generation_db_default"),
    ("tracer", "0130_observationspan_internal_type"),
    ("simulate", "0102_agent_fix_engine_notes"),
    ("simulate", "0103_harness_job_verify_of"),
    ("tracer", "0098_useralertmonitor_webhook_url"),
    ("tracer", "0131_merge_alert_webhook"),
    ("tracer", "0132_merge_alert_webhook_and_sql_workbench"),
    ("accounts", "0026_orgapikey_expires_at"),
    ("accounts", "0031_merge_api_key_expiry"),
    ("accounts", "0032_merge_expiry_and_coding_agent"),
    ("tracer", "0133_eval_recalculation"),
    ("tracer", "0134_merge_eval_recalculation_and_sampling"),
    ("tracer", "0135_eval_put_back"),
)
# Eight SQL additions shared with the SQL-only candidate. These identities
# account for the graph; they are not safety declarations for opaque operations.
SQL_ADDITIONS = (
    ("tracer", "0117_sql_workbench"),
    ("tracer", "0118_merge_sql_workbench_and_simulation_project"),
    ("tracer", "0119_sql_execution_result_reuse"),
    ("tracer", "0120_merge_sql_execution_and_shared_issue"),
    ("tracer", "0121_merge_sql_execution_and_alert_high_water"),
    ("tracer", "0122_sql_payload_expiry_index"),
    ("tracer", "0130_merge_20261104_0940"),
    ("tracer", "0131_merge_sql_workbench_and_observationspan_internal_type"),
)
# Ten more identities on the sampling layer, including its graph joins.
SAMPLING_ADDITIONS = (
    ("tracer", "0117_cohort_sampling"),
    ("tracer", "0117_sampling_project_db_default"),
    ("tracer", "0119_merge_sampling_default_and_simulation_project"),
    ("tracer", "0120_sampling_cohort_lease"),
    ("tracer", "0121_merge_sampling_and_sql_reuse"),
    ("tracer", "0122_merge_sampling_and_alert_issue_state"),
    ("tracer", "0123_merge_20261104_0743"),
    ("tracer", "0131_merge_20261104_1308"),
    ("tracer", "0132_merge_sampling_and_sql_workbench_dev_merge"),
    ("tracer", "0133_merge_sampling_and_alert_webhook"),
)
# Inventory identities use source directories; Django's historical app label
# remains prism (agentcc/apps.py), including migration dependencies and states.
WEBHOOK_ADDITIONS = (
    ("agentcc", "0030_webhook_delivery_health"),
    ("agentcc", "0031_webhook_event_status_indexes"),
)
# Additive configuration only, not a request-log data migration or webhook change.
RETENTION_ADDITIONS = (("agentcc", "0032_request_log_retention"),)
APP_LABELS = {"agentcc": "prism"}
LATEST += SQL_ADDITIONS + SAMPLING_ADDITIONS + WEBHOOK_ADDITIONS + RETENTION_ADDITIONS
FLOOR_ONLY = (
    ("simulate", "0099_harness_versioning"),
    ("simulate", "0100_agent_fix"),
    ("simulate", "0101_agent_fix_candidate_override"),
    ("tracer", "0119_retire_deleted_eval_memberships"),
    ("tracer", "0120_alert_issue_state_occurrences_high_water"),
)
INVENTORIES = (
    (REVISIONS[1], LATEST, 51),
    (REVISIONS[0], LATEST + FLOOR_ONLY, 56),
)


def keywords(node):
    return {k.arg: k.value for k in node.keywords}


class CharField(SimpleNamespace):
    """AST-only built-in-field double; constructor equality is not SQL proof."""

    non_db_attrs = ("choices",)

    def deconstruct(self):
        return None, "django.db.models.CharField", self.arguments, dict(self.options)


def field(node):
    assert isinstance(node, ast.Call)
    options = keywords(node)
    kind = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
    remote = None
    if kind in ("ForeignKey", "OneToOneField", "ManyToManyField"):
        target = options.get("to") or (node.args[0] if node.args else None)
        assert target is not None
        if isinstance(target, ast.Attribute):
            assert ast.unparse(target) == "settings.AUTH_USER_MODEL"
            target = "accounts.user"
        else:
            target = ast.literal_eval(target)
        remote = SimpleNamespace(
            model=target,
            through=ast.literal_eval(options["through"])
            if "through" in options
            else None,
        )
    cls = CharField if kind == "CharField" else SimpleNamespace
    return cls(
        remote_field=remote,
        many_to_many=kind == "ManyToManyField",
        primary_key=ast.literal_eval(options["primary_key"])
        if "primary_key" in options
        else False,
        unique=ast.literal_eval(options["unique"])
        if "unique" in options
        else kind == "OneToOneField",
        db_index=ast.literal_eval(options["db_index"])
        if "db_index" in options
        else False,
        db_table=ast.literal_eval(options["db_table"])
        if "db_table" in options
        else None,
        arguments=[ast.dump(arg) for arg in node.args],
        options={key: ast.dump(value) for key, value in options.items()},
    )


def model(node):
    options = keywords(node)
    declared = {
        ast.literal_eval(k): v
        for k, v in zip(
            options.get("options", ast.Dict(keys=[], values=[])).keys,
            options.get("options", ast.Dict(keys=[], values=[])).values,
        )
    }
    return SimpleNamespace(
        options=(
            {"db_table": ast.literal_eval(declared["db_table"])}
            if "db_table" in declared
            else {}
        ),
        fields={
            ast.literal_eval(pair.elts[0]): field(pair.elts[1])
            for pair in options["fields"].elts
        },
    )


class State:
    def __init__(self):
        self.models = {}

    def clone(self):
        return copy.deepcopy(self)


class Operation:
    def __init__(self, node):
        self.node = node
        options = keywords(node)
        for key in ("name", "model_name", "old_name", "new_name"):
            if key in options:
                setattr(self, key, ast.literal_eval(options[key]))
        if "field" in options:
            self.field = field(options["field"])
        if type(self).__name__ == "RunPython":
            self.code = object()  # opaque; never called
        if type(self).__name__ == "RunSQL":
            self.sql = "opaque"  # only declaration is read

    def state_forwards(self, app, state):
        kind = type(self).__name__
        if kind == "CreateModel":
            state.models[(app, self.name.lower())] = model(self.node)
        elif kind in ("AddField", "AlterField"):
            state.models[(app, self.model_name)].fields[self.name] = self.field
        elif kind == "RemoveField":
            state.models[(app, self.model_name)].fields.pop(self.name, None)
        elif kind == "RenameField":
            fields = state.models[(app, self.model_name)].fields
            fields[self.new_name] = fields.pop(self.old_name)
        else:
            # All remaining operations in these COMPLETE plans affect catalog
            # indexes/constraints or carry declarations; no state table rename.
            assert kind in (
                "RunPython",
                "RunSQL",
                "AddIndex",
                "AddIndexConcurrently",
                "RemoveIndex",
                "AddConstraint",
            )


def operation_types():
    kinds = (
        "CreateModel",
        "AddField",
        "AlterField",
        "RemoveField",
        "RenameField",
        "AddIndex",
        "RemoveIndex",
        "AddConstraint",
        "RunPython",
        "RunSQL",
        "SeparateDatabaseAndState",
    )
    result = {
        kind: type(
            kind,
            (Operation,),
            {"__module__": "django.db.migrations.operations.fixtures"},
        )
        for kind in kinds
    }
    result["AddIndexConcurrently"] = type(
        "AddIndexConcurrently",
        (result["AddIndex"],),
        {"__module__": "django.contrib.postgres.operations"},
    )
    result["RunPython"].noop = object()
    result["RunSQL"].noop = ""
    return SimpleNamespace(**result)


def scope_environment(state):
    def resolve(remote, app, model_name):
        if remote == "self":
            return app, model_name.lower()
        if "." in remote:
            app, remote = remote.split(".", 1)
        return app, remote.lower()

    env = {
        "ast": ast,
        "re": re,
        "migrations": operation_types(),
        "models": SimpleNamespace(CharField=CharField),
        "schemas": {"postgres": "public", "clickhouse": "default"},
        "resolve_relation": resolve,
        "strip_quotes": lambda s: s.replace('"', ""),
        "truncate_name": lambda s, length: s[:length],
    }
    # Use the actual resolver's functions/constants, with imports and final
    # loader invocation left to the native test. Opaque declarations are AST data.
    nodes = [
        n
        for n in ast.parse(p.PENDING_TABLE_SCOPE).body
        if isinstance(n, ast.FunctionDef)
        and n.name != "declared"
        or isinstance(n, ast.Assign)
        and any(
            isinstance(t, ast.Name) and t.id in ("IDENT", "NO_TABLE", "MODEL_OPS")
            for t in n.targets
        )
    ]
    exec(  # noqa: S102 - exercise trusted shipping resolver AST, never migration code.
        compile(
            ast.Module(body=nodes, type_ignores=[]), "<shipping-table-scope>", "exec"
        ),
        env,
    )

    def declared(migration):
        assert migration.declaration is not None, (
            "opaque_operation_table_scope_unavailable"
        )
        return {
            env["resolved"](*v.split(":", 1)) for v in migration.declaration["tables"]
        }

    env["declared"] = declared
    return env


def migration_path(app, name):
    return (
        ROOT
        / "futureagi"
        / ("ee/usage" if app == "usage" else app)
        / "migrations"
        / (name + ".py")
    )


def apply_model_renames(state, app, raw):
    """Read 0024's literal data, never import it or call its _build_ops().

    Model/table renames also retarget inbound relations, as RenameModel does.
    Index/constraint names are not part of these table-scope state doubles.
    """
    values = [
        node.value
        for node in ast.parse(raw).body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "MODEL_RENAMES"
            for target in node.targets
        )
    ]
    assert len(values) == 1
    renames = ast.literal_eval(values[0])
    renamed = {}
    for old, new, table in renames:
        old_key, new_key = (app, old.lower()), (app, new.lower())
        assert old_key in state.models and new_key not in state.models
        state.models[new_key] = state.models.pop(old_key)
        state.models[new_key].options["db_table"] = table
        renamed[old.lower()] = new.lower()
    for (other_app, _), state_model in state.models.items():
        for state_field in state_model.fields.values():
            remote = state_field.remote_field
            if remote is None:
                continue
            for attribute in ("model", "through"):
                target = getattr(remote, attribute, None)
                if target is None or target == "self":
                    continue
                target_app, target_model = (
                    target.split(".", 1) if "." in target else (other_app, target)
                )
                if target_app == app and target_model.lower() in renamed:
                    setattr(
                        remote, attribute, app + "." + renamed[target_model.lower()]
                    )


def baseline_state(revision):
    state = State()
    pending = set(
        next(identities for source, identities, _ in INVENTORIES if source == revision)
    )
    # Model table options/initial fields are read from real CreateModel ASTs.
    # Exclude this source's pending additions: their models do not exist until
    # state_forwards runs. In particular, 0032's retention config is not baseline.
    # Only usage's pre-AlterField inbound edges/uniqueness are relevant among
    # baseline fields in these plans; replace them with exact source model ASTs
    # below rather than deriving that key fact from the target's added choices.
    for path in sorted((ROOT / "futureagi").glob("**/migrations/*.py")):
        directory = path.parent.parent.name
        if (directory, path.stem) in pending:
            continue
        app = APP_LABELS.get(directory, directory)
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "migrations"
                and node.func.attr == "CreateModel"
            ):
                continue
            options = keywords(node)
            if "name" not in options or "fields" not in options:
                continue
            state.models[(app, ast.literal_eval(options["name"]).lower())] = model(node)
    # Both supported sources already applied 0024. Preserve its exact model and
    # physical table names instead of inferring them from an Agentcc prefix.
    apply_model_renames(
        state,
        "prism",
        source(revision, "agentcc/migrations/0024_rename_prism_to_agentcc.py"),
    )
    usage = ast.parse(source(revision, "ee/usage/models/usage.py"))
    for node in usage.body:
        if not isinstance(node, ast.ClassDef):
            continue
        fields = {}
        for item in node.body:
            if not (
                isinstance(item, ast.Assign)
                and isinstance(item.value, ast.Call)
                and isinstance(item.value.func, ast.Attribute)
                and isinstance(item.value.func.value, ast.Name)
                and item.value.func.value.id == "models"
            ):
                continue
            call = copy.deepcopy(item.value)
            # Source model imports are classes; normalize only real FK targets.
            if call.func.attr in ("ForeignKey", "OneToOneField") and isinstance(
                call.args[0], ast.Name
            ):
                name = call.args[0].id
                call.args[0] = ast.Constant(
                    (
                        "accounts."
                        if name in ("Organization", "Workspace", "User")
                        else "usage."
                    )
                    + name.lower()
                )
            fields[item.targets[0].id] = field(call)
        if fields:
            state.models[("usage", node.name.lower())] = SimpleNamespace(
                options={}, fields=fields
            )
    return state


def pending_plan(identities, env):
    migrations = {}
    for directory, name in identities:
        app = APP_LABELS.get(directory, directory)
        tree = ast.parse(migration_path(directory, name).read_text())
        cls = next(
            n
            for n in tree.body
            if isinstance(n, ast.ClassDef) and n.name == "Migration"
        )
        values = {
            n.targets[0].id: n.value for n in cls.body if isinstance(n, ast.Assign)
        }
        dependencies = [
            ast.literal_eval(n)
            for n in values["dependencies"].elts
            if isinstance(n, ast.Tuple)
        ]
        operations = []
        for node in values["operations"].elts:
            if isinstance(node.func, ast.Name):
                # The SQL migration imports this standard Django operation by
                # name. Do not accept arbitrary custom operations as AddIndex.
                assert node.func.id == "AddIndexConcurrently"
                assert any(
                    isinstance(item, ast.ImportFrom)
                    and item.module == "django.contrib.postgres.operations"
                    and any(
                        alias.name == "AddIndexConcurrently" and alias.asname is None
                        for alias in item.names
                    )
                    for item in tree.body
                )
                kind = node.func.id
            elif (
                ast.unparse(node.func)
                == "django.contrib.postgres.operations.AddIndexConcurrently"
            ):
                # 0031 uses Django's full module path. Require that exact path
                # and import, not just an attribute ending in the same name.
                assert any(
                    isinstance(item, ast.Import)
                    and any(
                        alias.name == "django.contrib.postgres.operations"
                        and alias.asname is None
                        for alias in item.names
                    )
                    for item in tree.body
                )
                kind = "AddIndexConcurrently"
            else:
                assert isinstance(node.func, ast.Attribute) and isinstance(
                    node.func.value, ast.Name
                )
                assert node.func.value.id == "migrations"
                kind = node.func.attr
                assert kind != "AddIndexConcurrently"  # Not exported by migrations.
            operations.append(getattr(env["migrations"], kind)(node))
        declaration = next(
            (
                ast.literal_eval(n.value)
                for n in tree.body
                if isinstance(n, ast.Assign)
                and any(
                    isinstance(t, ast.Name) and t.id == "UPDATE_SAFETY"
                    for t in n.targets
                )
            ),
            None,
        )
        migrations[(app, name)] = SimpleNamespace(
            app_label=app,
            name=name,
            operations=operations,
            dependencies=dependencies,
            declaration=declaration,
        )
    ordered = []
    while migrations:
        ready = sorted(
            key
            for key, m in migrations.items()
            if not set(m.dependencies) & set(migrations)
        )
        assert ready, "cyclic pending fixture"
        for key in ready:
            ordered.append(migrations.pop(key))
    return ordered


class CompleteUsagePendingIntersectionTests(unittest.TestCase):
    def assert_complete_inventory(self, revision, identities, count):
        # Additions, not edited applied migrations. This deliberately breaks if
        # the shipping plan changes without refreshing its complete inventory.
        paths = subprocess.check_output(
            [
                "git",
                "-C",
                str(ROOT),
                "diff",
                "--name-only",
                "--diff-filter=A",
                revision,
                "HEAD",
                "--",
                "futureagi/**/migrations/*.py",
            ],
            text=True,
        ).splitlines()
        actual = {(Path(path).parent.parent.name, Path(path).stem) for path in paths}
        self.assertEqual(actual, set(identities))
        self.assertEqual(len(actual), count)
        self.assertEqual(
            len(identities), count
        )  # duplicate fixture identities also fail

    def test_complete_latest_and_floor_inventory(self):
        self.assertEqual(len(SQL_ADDITIONS), 8)
        self.assertEqual(len(SAMPLING_ADDITIONS), 10)
        self.assertTrue(set(SQL_ADDITIONS).isdisjoint(SAMPLING_ADDITIONS))
        for revision, identities, count in INVENTORIES:
            with self.subTest(revision=revision):
                self.assert_complete_inventory(revision, identities, count)

    def test_pending_inventory_rejects_unknown_missing_and_substituted_migrations(self):
        # Exercise every identity, including webhook, current dev and floor-only
        # additions. Same-count replacement must fail just like extra/missing files.
        unknown = "futureagi/tracer/migrations/9999_unreviewed_migration.py"
        for revision, identities, count in INVENTORIES:
            paths = subprocess.check_output(
                [
                    "git",
                    "-C",
                    str(ROOT),
                    "diff",
                    "--name-only",
                    "--diff-filter=A",
                    revision,
                    "HEAD",
                    "--",
                    "futureagi/**/migrations/*.py",
                ],
                text=True,
            ).splitlines()
            self.assert_complete_inventory(revision, identities, count)
            for app, name in identities:
                missing = migration_path(app, name).relative_to(ROOT).as_posix()
                self.assertIn(missing, paths)
                without = [path for path in paths if path != missing]
                for mutation, changed in (
                    ("missing", without),
                    ("unknown", paths + [unknown]),
                    ("same_count_substitution", without + [unknown]),
                ):
                    with (
                        self.subTest(
                            revision=revision, missing=missing, mutation=mutation
                        ),
                        mock.patch.object(
                            subprocess,
                            "check_output",
                            return_value="\n".join(changed) + "\n",
                        ),
                        self.assertRaises(AssertionError),
                    ):
                        self.assert_complete_inventory(revision, identities, count)

    def test_sql_index_operations_have_explicit_state_table_scope(self):
        # The sampling layer retains SQL's concurrent-index scope regression.
        state = baseline_state(REVISIONS[1])
        env = scope_environment(state)
        # SQLExecution is itself pending, so replay preceding SQL state rather
        # than leaking its target CreateModel into the source baseline.
        for migration in pending_plan(SQL_ADDITIONS, env):
            if migration.name == "0122_sql_payload_expiry_index":
                break
            for op in migration.operations:
                op.state_forwards(migration.app_label, state)
        self.assertEqual(migration.name, "0122_sql_payload_expiry_index")
        self.assertEqual(
            [type(op).__name__ for op in migration.operations],
            ["AddIndexConcurrently", "AddIndexConcurrently", "RunPython"],
        )
        for op in migration.operations[:2]:
            self.assertEqual(
                env["touched"](op, migration, state),
                {("postgres", "public.tracer_sqlexecution")},
            )
        # This does not authorize the separate undeclared RunPython operation.

    def test_agentcc_directory_resolves_to_historical_prism_app_label(self):
        tree = ast.parse((ROOT / "futureagi/agentcc/apps.py").read_text())
        config = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "AgentccConfig"
        )
        values = {
            node.targets[0].id: ast.literal_eval(node.value)
            for node in config.body
            if isinstance(node, ast.Assign)
        }
        self.assertEqual((values["name"], values["label"]), ("agentcc", "prism"))
        self.assertEqual(APP_LABELS[values["name"]], values["label"])
        plan = pending_plan(reversed(WEBHOOK_ADDITIONS), scope_environment(State()))
        self.assertEqual(
            [(migration.app_label, migration.name) for migration in plan],
            [
                ("prism", "0030_webhook_delivery_health"),
                ("prism", "0031_webhook_event_status_indexes"),
            ],
        )
        self.assertEqual(
            plan[1].dependencies, [("prism", "0030_webhook_delivery_health")]
        )

    def test_baseline_0024_model_table_names_and_inbound_relations(self):
        expected = {
            "agentccblocklist": "agentcc_blocklist",
            "agentccprovidercredential": "agentcc_provider_credential",
            "agentccwebhook": "agentcc_webhook",
            "agentccwebhookevent": "agentcc_webhook_event",
            "agentccemailalert": "agentcc_email_alert",
            "agentccsession": "agentcc_session",
            "agentccroutingpolicy": "agentcc_routing_policy",
            "agentccapikey": "agentcc_api_key",
            "agentccshadowexperiment": "agentcc_shadow_experiment",
            "agentcccustompropertyschema": "agentcc_custom_property_schema",
            "agentccshadowresult": "agentcc_shadow_result",
            "agentccrequestlog": "agentcc_request_log",
            "agentccorgconfig": "agentcc_org_config",
            "agentccguardrailfeedback": "agentcc_guardrail_feedback",
            "agentccproject": "agentcc_project",
            "agentccguardrailpolicy": "agentcc_guardrail_policy",
        }
        for revision in REVISIONS:
            with self.subTest(revision=revision):
                state = baseline_state(revision)
                self.assertFalse(any(app == "agentcc" for app, _ in state.models))
                self.assertEqual(
                    {
                        name: model.options["db_table"]
                        for (app, name), model in state.models.items()
                        if app == "prism" and name.startswith("agentcc")
                    },
                    expected,
                )
                self.assertNotIn(("prism", "prismwebhook"), state.models)
                self.assertNotIn(("prism", "prismwebhookevent"), state.models)
                event = state.models[("prism", "agentccwebhookevent")]
                self.assertEqual(
                    event.fields["webhook"].remote_field.model, "prism.agentccwebhook"
                )
                self.assertNotIn("requeued_at", event.fields)
                self.assertNotIn(
                    "consecutive_failures",
                    state.models[("prism", "agentccwebhook")].fields,
                )
                env = scope_environment(state)
                self.assertEqual(
                    env["inbound"](state, "prism", "agentccwebhook"),
                    {
                        ("postgres", "public.agentcc_webhook"),
                        ("postgres", "public.agentcc_webhook_event"),
                    },
                )

    def test_0024_rename_data_is_literal_only_and_never_executes_builder(self):
        state = State()
        state.models[("prism", "prismwebhook")] = SimpleNamespace(options={}, fields={})
        raw = """
raise AssertionError('migration module must not execute')
MODEL_RENAMES = [('PrismWebhook', 'AgentccWebhook', 'agentcc_webhook')]
def _build_ops():
    raise AssertionError('migration builder must not execute')
operations = _build_ops()
"""
        apply_model_renames(state, "prism", raw)
        self.assertEqual(set(state.models), {("prism", "agentccwebhook")})
        self.assertEqual(
            state.models[("prism", "agentccwebhook")].options,
            {"db_table": "agentcc_webhook"},
        )
        for raw in (
            "MODEL_RENAMES = _build_ops()",
            "MODEL_RENAMES = []\nMODEL_RENAMES = []",
            "",
        ):
            with self.subTest(raw=raw), self.assertRaises((AssertionError, ValueError)):
                apply_model_renames(State(), "prism", raw)

    def test_webhook_layer_has_exact_table_scopes(self):
        expected = {
            "0030_webhook_delivery_health": {
                ("postgres", "public.agentcc_webhook"),
                ("postgres", "public.agentcc_webhook_event"),
            },
            "0031_webhook_event_status_indexes": {
                ("postgres", "public.agentcc_webhook_event")
            },
        }
        for revision in REVISIONS:
            state = baseline_state(revision)
            env = scope_environment(state)
            actual = {}
            for migration in pending_plan(WEBHOOK_ADDITIONS, env):
                tables = set()
                for op in migration.operations:
                    touched = env["touched"](op, migration, state)
                    with self.subTest(
                        revision=revision,
                        migration=migration.name,
                        operation=type(op).__name__,
                        field=getattr(op, "name", None),
                    ):
                        if type(op).__name__ == "AlterField":
                            self.assertEqual(
                                (op.model_name, op.name),
                                ("agentccwebhookevent", "status"),
                            )
                            self.assertEqual(
                                touched, set()
                            )  # Choices-only, no physical change.
                        else:
                            table = (
                                "agentcc_webhook"
                                if op.model_name == "agentccwebhook"
                                else "agentcc_webhook_event"
                            )
                            self.assertEqual(touched, {("postgres", "public." + table)})
                        if migration.name == "0031_webhook_event_status_indexes":
                            self.assertEqual(
                                (type(op).__module__, type(op).__name__),
                                (
                                    "django.contrib.postgres.operations",
                                    "AddIndexConcurrently",
                                ),
                            )
                    tables |= touched
                    op.state_forwards(migration.app_label, state)
                actual[migration.name] = tables
            with self.subTest(revision=revision):
                self.assertEqual(actual, expected)

    def test_request_log_retention_is_additive_config_with_exact_scope(self):
        registry = p.load_high_volume_registry()
        for revision in REVISIONS:
            with self.subTest(revision=revision):
                state = baseline_state(revision)
                key = ("prism", "agentccrequestlogretention")
                self.assertNotIn(key, state.models)
                env = scope_environment(state)
                plan = pending_plan(RETENTION_ADDITIONS, env)
                self.assertEqual(len(plan), 1)
                migration = plan[0]
                self.assertEqual(
                    (migration.app_label, migration.name),
                    ("prism", "0032_request_log_retention"),
                )
                self.assertEqual(
                    migration.dependencies,
                    [
                        ("accounts", "0032_merge_expiry_and_coding_agent"),
                        ("prism", "0031_webhook_event_status_indexes"),
                    ],
                )
                self.assertIsNone(migration.declaration)
                self.assertEqual(
                    [type(op).__name__ for op in migration.operations], ["CreateModel"]
                )
                op = migration.operations[0]
                self.assertEqual(op.name, "AgentccRequestLogRetention")
                tables = env["touched"](op, migration, state)
                self.assertEqual(
                    tables, {("postgres", "public.agentcc_request_log_retention")}
                )
                self.assertNotIn(key, state.models)  # Scope resolution clones state.
                op.state_forwards(migration.app_label, state)
                created = state.models[key]
                self.assertEqual(
                    created.options, {"db_table": "agentcc_request_log_retention"}
                )
                self.assertEqual(
                    set(created.fields),
                    {
                        "id",
                        "created_at",
                        "updated_at",
                        "deleted",
                        "deleted_at",
                        "retention_days",
                        "last_run_started_at",
                        "last_run_finished_at",
                        "last_run_deleted_rows",
                        "last_run_error",
                        "organization",
                        "updated_by",
                    },
                )
                self.assertEqual(
                    {
                        name: field.remote_field.model
                        for name, field in created.fields.items()
                        if field.remote_field is not None
                    },
                    {
                        "organization": "accounts.organization",
                        "updated_by": "accounts.user",
                    },
                )
                identity = {"app_label": migration.app_label, "name": migration.name}
                # Creating this config table neither alters nor deletes request
                # logs and does not select the webhook-event high-volume seed.
                self.assertEqual(
                    p.select_reference_registry(
                        registry,
                        [
                            dict(
                                identity,
                                tables=[
                                    {"engine": engine, "table": table}
                                    for engine, table in sorted(tables)
                                ],
                            )
                        ],
                        plan=[identity],
                    ),
                    (),
                )

    def test_concurrent_index_parser_rejects_custom_or_unimported_operations(self):
        raw = migration_path("agentcc", "0031_webhook_event_status_indexes").read_text()
        qualified = "django.contrib.postgres.operations.AddIndexConcurrently"
        cases = {
            "custom_module": raw.replace(
                "django.contrib.postgres.operations", "custom.operations"
            ),
            "wrong_django_module": raw.replace(
                "django.contrib.postgres.operations", "django.db.migrations.operations"
            ),
            "wrong_operation": raw.replace(qualified, qualified + "Custom"),
            "missing_import": raw.replace(
                "import django.contrib.postgres.operations", ""
            ),
            "aliased_import": raw.replace(
                "import django.contrib.postgres.operations",
                "import django.contrib.postgres.operations as custom",
            ),
            "unimported_short_name": raw.replace(qualified, "AddIndexConcurrently"),
            "custom_short_import": raw.replace(
                "import django.contrib.postgres.operations",
                "from custom.operations import AddIndexConcurrently",
            ).replace(qualified, "AddIndexConcurrently"),
            "not_exported_by_migrations": raw.replace(
                qualified, "migrations.AddIndexConcurrently"
            ),
        }
        for case, changed in cases.items():
            with (
                self.subTest(case=case),
                mock.patch.object(Path, "read_text", return_value=changed),
                self.assertRaises(AssertionError),
            ):
                pending_plan([WEBHOOK_ADDITIONS[1]], scope_environment(State()))

    def test_unexpected_migration_still_fails_complete_inventory(self):
        paths = subprocess.check_output(
            [
                "git",
                "-C",
                str(ROOT),
                "diff",
                "--name-only",
                "--diff-filter=A",
                REVISIONS[1],
                "HEAD",
                "--",
                "futureagi/**/migrations/*.py",
            ],
            text=True,
        )
        with (
            mock.patch.object(
                subprocess,
                "check_output",
                return_value=paths + "futureagi/tracer/migrations/9999_unexpected.py\n",
            ),
            self.assertRaises(AssertionError),
        ):
            self.assert_complete_inventory(REVISIONS[1], LATEST, 51)

    def test_sampling_layer_has_exact_table_scopes(self):
        expected = {
            "0117_cohort_sampling": {
                "public.tracer_project",
                "public.tracer_eval_task",
                "public.tracer_eval_sampling_cohort",
                "public.tracer_eval_sampling_member",
            },
            # The default ships in 0117_cohort_sampling; this AlterField is
            # identical, so Django emits no SQL and it touches no table.
            "0117_sampling_project_db_default": set(),
            "0119_merge_sampling_default_and_simulation_project": set(),
            "0120_sampling_cohort_lease": {"public.tracer_eval_sampling_cohort"},
            "0121_merge_sampling_and_sql_reuse": set(),
            "0122_merge_sampling_and_alert_issue_state": set(),
            "0123_merge_20261104_0743": set(),
            "0131_merge_20261104_1308": set(),
            "0132_merge_sampling_and_sql_workbench_dev_merge": set(),
            "0133_merge_sampling_and_alert_webhook": set(),
        }
        for revision in REVISIONS:
            state = baseline_state(revision)
            env = scope_environment(state)
            actual = {}
            for migration in pending_plan(SAMPLING_ADDITIONS, env):
                tables = set()
                for op in migration.operations:
                    tables |= env["touched"](op, migration, state)
                    op.state_forwards(migration.app_label, state)
                self.assertTrue(all(engine == "postgres" for engine, table in tables))
                actual[migration.name] = {table for engine, table in tables}
            with self.subTest(revision=revision):
                self.assertEqual(actual, expected)

    def test_complete_latest_and_floor_including_unique_lookup_inbound_edges(self):
        registry = p.load_high_volume_registry()
        for revision, identities, count in INVENTORIES:
            with self.subTest(revision=revision):
                self.assert_complete_inventory(revision, identities, count)
                state = baseline_state(revision)
                env = scope_environment(state)
                plan = pending_plan(identities, env)
                scopes = []
                for migration in plan:
                    tables = set()
                    for op in migration.operations:
                        tables |= env["touched"](op, migration, state)
                        op.state_forwards(migration.app_label, state)
                    scopes.append(
                        {
                            "app_label": migration.app_label,
                            "name": migration.name,
                            "tables": [
                                {"engine": e, "table": t} for e, t in sorted(tables)
                            ],
                        }
                    )
                identities_in_order = [
                    {"app_label": m.app_label, "name": m.name} for m in plan
                ]
                selected = p.select_reference_registry(
                    registry, scopes, plan=identities_in_order
                )
                expected = {
                    "tracer_trace",
                    "tracer_observation_span",
                    "tracer_eval_task",
                    "usage_apicalllog",
                    "agentcc_webhook_event",
                }
                if revision == REVISIONS[0]:
                    expected |= {
                        "tracer_error_cluster_traces",
                        "tracer_eval_logger",
                        "tracer_trace_error_group",
                    }
                self.assertEqual({row["table"] for row in selected}, expected)
                self.assertTrue(all(row["database"] == "postgres" for row in selected))
                self.assertEqual(
                    {
                        (row["model"], row["table"])
                        for row in selected
                        if row.get("app_label") == "prism"
                    },
                    {("AgentccWebhookEvent", "agentcc_webhook_event")},
                )
                self.assertFalse(
                    any(
                        row.get("model") in ("UsageEventLog", "UsageSummary")
                        for row in selected
                    )
                )
                usage = next(row for row in scopes if row["app_label"] == "usage")
                self.assertEqual(
                    {row["table"] for row in usage["tables"]},
                    {
                        "public.usage_apicalltype",
                        "public.usage_apicalllog",
                        "public.usage_pricing",
                        "public.usage_ratelimit",
                    },
                )
                for row in scopes:
                    scoped = p.select_reference_registry(
                        registry,
                        [row],
                        plan=[{k: row[k] for k in ("app_label", "name")}],
                    )
                    names = {item["table"] for item in scoped}
                    if row["name"] == "0030_queue_existing_demo_trace_publications":
                        expected_row = {"tracer_trace", "tracer_observation_span"}
                    elif row["app_label"] == "usage":
                        expected_row = {"usage_apicalllog"}
                    elif row["app_label"] == "prism" and row["name"] in {
                        name for _, name in WEBHOOK_ADDITIONS
                    }:
                        expected_row = {"agentcc_webhook_event"}
                    elif row["name"] in (
                        "0118_instant_eval_answers",
                        "0117_cohort_sampling",
                    ):
                        expected_row = {"tracer_eval_task"}
                    elif row["name"] == "0130_observationspan_internal_type":
                        expected_row = set()  # Choices-only state change.
                    elif row["name"] == "0119_retire_deleted_eval_memberships":
                        expected_row = {
                            "tracer_error_cluster_traces",
                            "tracer_eval_logger",
                            "tracer_trace_error_group",
                        }
                    else:
                        expected_row = set()
                    self.assertEqual(
                        names, expected_row, (row["app_label"], row["name"])
                    )

    def test_unique_name_is_the_non_pk_reason_usage_is_selected(self):
        state = baseline_state(REVISIONS[1])
        env = scope_environment(state)
        migration = pending_plan([("usage", "0030_add_jev_evaluator_call_type")], env)[
            0
        ]
        op = migration.operations[0]
        self.assertFalse(op.field.primary_key)
        self.assertTrue(op.field.unique)
        self.assertIn(
            ("postgres", "public.usage_apicalllog"),
            env["touched"](op, migration, state),
        )
        # Negative control removes BOTH before/after unique flags. The actual
        # source and target keep them; do not "fix" production by changing this.
        state.models[("usage", "apicalltype")].fields["name"].unique = False
        op.field.unique = False
        # Retain a real column alteration: without one this becomes choices-only,
        # which now correctly has no physical-table scope at all.
        op.field.options["max_length"] = ast.dump(ast.Constant(51))
        self.assertEqual(
            env["touched"](op, migration, state),
            {("postgres", "public.usage_apicalltype")},
        )
