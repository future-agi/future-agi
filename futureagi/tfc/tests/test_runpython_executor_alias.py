"""Offline migration connection contract; run with python -I FILE [REPO_ROOT].

The recorded inventory is every added migration between v1.56.1 (6840e44914cf)
and a86f6e6c68, including schema-only files. The AST guard requires an enclosing
executor scope in BOTH directions; explicit-alias provenance is secondary.
Runtime probes execute only stdlib code and extracted definitions with doubles,
never Django, database access or migration-module imports. This is not a
substitute for PostgreSQL executor tests.
"""

import ast
import copy
import sys
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

# Frozen repository inventory, generated with git diff --diff-filter=A.
ADDED_MIGRATIONS = """
futureagi/accounts/migrations/0026_host_event_outbox.py
futureagi/accounts/migrations/0026_orgapikey_secret_hash.py
futureagi/accounts/migrations/0027_seal_api_secrets.py
futureagi/accounts/migrations/0028_merge_20261019_1400.py
futureagi/accounts/migrations/0029_omega_purge_lifecycle.py
futureagi/accounts/migrations/0030_merge_omega_purge_and_simulation_agent_key.py
futureagi/accounts/migrations/0031_retire_org_keys.py
futureagi/accounts/migrations/0032_service_grants.py
futureagi/accounts/migrations/0033_batch_submission_intent.py
futureagi/accounts/migrations/0034_retirement_manifest.py
futureagi/accounts/migrations/0035_gateway_retirement_fence.py
futureagi/accounts/migrations/0036_authtoken_password_reset_type.py
futureagi/agentcc/migrations/0029_org_config_managed_models.py
futureagi/agentcc/migrations/0030_request_log_v2.py
futureagi/agentcc/migrations/0031_request_log_v2_indexes.py
futureagi/agentcc/migrations/0032_request_attempt.py
futureagi/agentcc/migrations/0033_managed_upstream_key.py
futureagi/agentcc/migrations/0034_request_outcome.py
futureagi/agentcc/migrations/0035_mcp_credential.py
futureagi/agentcc/migrations/0036_mcp_tool_call.py
futureagi/agentcc/migrations/0037_mcp_tool_revision.py
futureagi/agentcc/migrations/0038_mcp_approval.py
futureagi/agentcc/migrations/0039_mcp_registry.py
futureagi/agentcc/migrations/0040_mcp_oauth.py
futureagi/agentcc/migrations/0041_mcp_indexes.py
futureagi/agentcc/migrations/0042_delegated_grant.py
futureagi/agentcc/migrations/0043_attempt_hollow_request_log_evidence.py
futureagi/agentcc/migrations/0044_memory_forget.py
futureagi/agentcc/migrations/0045_org_config_tools_identity.py
futureagi/agentcc/migrations/0046_org_config_db_defaults.py
futureagi/agentcc/migrations/0047_encrypt_org_config_secrets.py
futureagi/agentcc/migrations/0048_r7_batch_jobs.py
futureagi/agentcc/migrations/0048_r7_email_alert_state.py
futureagi/agentcc/migrations/0049_merge_r7.py
futureagi/agentcc/migrations/0050_encrypt_org_config_secret_aliases.py
futureagi/agentcc/migrations/0051_encrypt_alert_channel_urls.py
futureagi/agentcc/migrations/0052_auto_reload_consent.py
futureagi/agentcc/migrations/0052_cap_policy_activation.py
futureagi/agentcc/migrations/0052_mcp_service_provisioning.py
futureagi/agentcc/migrations/0053_cap_policy_revision.py
futureagi/agentcc/migrations/0053_ee_agent_key_policy.py
futureagi/agentcc/migrations/0054_ee_agent_key_expiry_backfill.py
futureagi/agentcc/migrations/0055_ee_agent_key_mint_idempotency.py
futureagi/agentcc/migrations/0056_mcp_secrets_to_vault.py
futureagi/agentcc/migrations/0057_mcp_credential_origin.py
futureagi/agentcc/migrations/0058_mcp_upstream_oauth.py
futureagi/agentcc/migrations/0058_retire_ee_credentials.py
futureagi/agentcc/migrations/0059_mcp_connect_org_client.py
futureagi/agentcc/migrations/0060_mcp_connect_shared_mode.py
futureagi/agentcc/migrations/0061_mcp_personal_principal_cleanup.py
futureagi/agentcc/migrations/0062_mcp_cleanup_receipts.py
futureagi/agentcc/migrations/0063_mcp_credential_grant_revision.py
futureagi/agentcc/migrations/0063_mcp_grant_org_only.py
futureagi/agentcc/migrations/0064_mcp_vendor_connections.py
futureagi/agentcc/migrations/0064_siem_export.py
futureagi/agentcc/migrations/0065_mcp_vendor_optimistic_lifecycle.py
futureagi/agentcc/migrations/0065_merge_mcp_adapters_siem.py
futureagi/agentcc/migrations/0065_siem_hec.py
futureagi/agentcc/migrations/0066_mcp_vendor_cleanup_obligations.py
futureagi/agentcc/migrations/0067_mcp_vendor_retained_receipts.py
futureagi/agentcc/migrations/0068_mcp_vendor_connect_leases.py
futureagi/agentcc/migrations/0069_merge_mcp_vendor_adapters_siem.py
futureagi/agentcc/migrations/0070_merge_mcp_line_siem_hec.py
futureagi/agentcc/migrations/0071_mcp_connect_cancellation_generation.py
futureagi/agentcc/migrations/0071_merge_request_log_consent_and_mcp.py
futureagi/agentcc/migrations/0072_api_key_ownership_immutable.py
futureagi/agentcc/migrations/0073_merge_708j_round3.py
futureagi/ee/falcon_ai/migrations/_builtin_skills.py
futureagi/ee/usage/migrations/0030_managed_credit_ledger.py
futureagi/ee/usage/migrations/0031_ledger_phase2a.py
futureagi/ee/usage/migrations/0032_ledger_opening_buckets.py
futureagi/ee/usage/migrations/0033_backfill_topup_payment_intent.py
futureagi/mcp_server/migrations/0005_personal_key_reconnect.py
futureagi/model_hub/migrations/0123_datasetversion_datasetversionitem_and_more.py
futureagi/model_hub/migrations/0123_promptlabelhead.py
futureagi/model_hub/migrations/0123_usereval_is_experiment_default.py
futureagi/model_hub/migrations/0124_dataset_description_tags.py
futureagi/model_hub/migrations/0124_dataset_saved_view.py
futureagi/model_hub/migrations/0124_datasetversionitem_unique_dataset_version_external_id.py
futureagi/model_hub/migrations/0124_evaltemplate_eval_type_dag.py
futureagi/model_hub/migrations/0124_experiment_run_control.py
futureagi/model_hub/migrations/0124_experiment_run_scope.py
futureagi/model_hub/migrations/0124_score_value_updated_at.py
futureagi/model_hub/migrations/0124_widen_model_hub_key_columns.py
futureagi/model_hub/migrations/0125_backfill_score_value_updated_at.py
futureagi/model_hub/migrations/0125_dataset_splits.py
futureagi/model_hub/migrations/0125_dataset_stars.py
futureagi/model_hub/migrations/0125_encrypt_model_hub_keys_at_rest.py
futureagi/model_hub/migrations/0125_experiment_run_control_phase.py
futureagi/model_hub/migrations/0125_experiment_run_mvp.py
futureagi/model_hub/migrations/0125_experiment_run_policy.py
futureagi/model_hub/migrations/0125_generated_input_snapshot.py
futureagi/model_hub/migrations/0126_dataset_folders.py
futureagi/model_hub/migrations/0126_evaltemplate_external_eval_type.py
futureagi/model_hub/migrations/0126_experiment_row_budget.py
futureagi/model_hub/migrations/0126_merge_dsx_works_705_r1.py
futureagi/model_hub/migrations/0126_row_source.py
futureagi/model_hub/migrations/0127_experimentrun_ordinal_datasetbaseline.py
futureagi/model_hub/migrations/0127_merge_dsx_works_705_r4.py
futureagi/model_hub/migrations/0127_merge_scope_and_run_control.py
futureagi/model_hub/migrations/0128_merge_keys_at_rest_and_experiment_runs.py
futureagi/model_hub/migrations/0128_merge_scope_and_splits.py
futureagi/model_hub/migrations/0129_merge_prompt_label_heads_and_experiment_runs.py
futureagi/model_hub/migrations/0130_merge_dsx_and_integration.py
futureagi/model_hub/migrations/0134_merge_dataset_works_and_integration.py
futureagi/model_hub/migrations/0135_evaltemplate_eval_type_external_and_dag.py
futureagi/model_hub/migrations/0136_merge_708j_round3.py
futureagi/model_hub/migrations/0137_merge_dsx3e_grid_snapshot.py
futureagi/model_hub/migrations/0137_merge_score_value_clock_and_708j_round3.py
futureagi/model_hub/migrations/0138_merge_dsx3e_c7_and_grid_snapshot.py
futureagi/model_hub/migrations/0138_merge_dsx3e_pgrid_row_source.py
futureagi/model_hub/migrations/0139_merge_dsx3f_c7_and_pgrid.py
futureagi/model_hub/migrations/0140_rebackfill_queueitem_project_upgraded.py
futureagi/omega_hub/migrations/0001_initial.py
futureagi/omega_hub/migrations/0002_version_strict_semver.py
futureagi/omega_hub/migrations/0003_erasure_and_email_verification.py
futureagi/omega_hub/migrations/0003_hub_operations.py
futureagi/omega_hub/migrations/0004_queue_age_and_bundle_locks.py
futureagi/omega_hub/migrations/0005_merge_hub_operations_and_erasure.py
futureagi/omega_hub/migrations/__init__.py
futureagi/omega_rollout/migrations/0001_initial.py
futureagi/omega_rollout/migrations/__init__.py
futureagi/simulate/migrations/0099_harness_versioning.py
futureagi/tracer/migrations/0115_omega_deployment_observe_link.py
futureagi/tracer/migrations/0118_merge_omega_observe_link_and_error_feed.py
futureagi/tracer/migrations/0119_omega_agent_observe_link.py
futureagi/tracer/migrations/0120_merge_sharedlink_and_agent_observe.py
futureagi/tracer/migrations/0121_background_trace_analysis_state.py
futureagi/webhooks/migrations/0001_initial.py
futureagi/webhooks/migrations/0002_api.py
futureagi/webhooks/migrations/0002_operations.py
futureagi/webhooks/migrations/0003_merge.py
futureagi/webhooks/migrations/0004_delivery_endpoint_created.py
futureagi/webhooks/migrations/__init__.py
""".split()
MANAGERS = {"objects", "_base_manager", "_default_manager"}
BINDERS = {"using", "db_manager"}
INSTANCE_IO = {"save", "delete", "refresh_from_db", "asave", "adelete"}
RELATED_IO = {"add", "remove", "clear", "set"}
ROOT = Path(__file__).resolve().parents[3]


def value(kind, related=(), model=None):
    return kind, frozenset(related), model


UNKNOWN = value("unknown")
ALIAS = value("alias")
SCHEMA = value("schema")
CONNECTION = value("connection")
UNBOUND = value("unbound")


class AliasAudit:
    def __init__(self, source, relations=(), *, require_scope=True):
        self.tree = ast.parse(source)
        self.require_scope = require_scope
        self.functions = {
            node.name: node
            for node in self.tree.body
            if isinstance(node, ast.FunctionDef)
        }
        self.relations = (
            relations if isinstance(relations, dict) else {None: set(relations)}
        )
        self.delegates = {
            item.asname or item.name
            for node in ast.walk(self.tree)
            if isinstance(node, ast.ImportFrom)
            and node.module == "tfc.migration_database"
            for item in node.names
            if item.name == "migration_database"
        }
        self.errors = set()
        self.visiting = set()

    def fail(self, node, message):
        self.errors.add(f"{node.lineno}: {message}")

    def expression(self, node, env):
        if isinstance(node, ast.Name):
            return env.get(node.id, UNKNOWN)
        if isinstance(node, ast.IfExp):
            # Existing helpers also support schema_editor=None outside migrations.
            test = node.test
            if (isinstance(test, ast.Name) and env.get(test.id) == SCHEMA) or (
                isinstance(test, ast.Compare)
                and self.expression(test.left, env) == SCHEMA
                and len(test.ops) == 1
                and isinstance(test.ops[0], ast.IsNot)
                and len(test.comparators) == 1
                and isinstance(test.comparators[0], ast.Constant)
                and test.comparators[0].value is None
            ):
                return self.expression(node.body, env)
            left = self.expression(node.body, env)
            return left if left == self.expression(node.orelse, env) else UNKNOWN
        if isinstance(node, ast.Attribute):
            base = self.expression(node.value, env)
            if base == SCHEMA and node.attr == "connection":
                return CONNECTION
            if base == CONNECTION and node.attr == "alias":
                return ALIAS
            if node.attr in MANAGERS:
                return value(
                    "bound" if env.get("@delegate") == ALIAS else "unbound",
                    model=base[2],
                )
            if (
                base[0] == "row"
                and isinstance(node.ctx, ast.Load)
                and node.attr in self.relations.get(base[2], set())
            ):
                if env.get("@delegate") != ALIAS and not any(
                    p.split("__")[0] == node.attr for p in base[1]
                ):
                    self.fail(
                        node,
                        f"implicit related read .{node.attr} without select_related",
                    )
                return value(
                    "row",
                    (
                        p.partition("__")[2]
                        for p in base[1]
                        if p.startswith(node.attr + "__")
                    ),
                    self.relations[base[2]][node.attr],
                )
            return UNKNOWN
        if isinstance(node, ast.Subscript):
            if isinstance(node.value, ast.Name) and node.value.id == "connections":
                if self.expression(node.slice, env) != ALIAS:
                    self.fail(node, "connection lookup is not the executor alias")
                return CONNECTION
            base = self.expression(node.value, env)
            return (
                base
                if isinstance(node.slice, ast.Slice)
                else value("row", base[1], base[2])
                if base[0] == "bound"
                else UNKNOWN
            )
        if not isinstance(node, ast.Call):
            return UNKNOWN
        keywords = {kw.arg: self.expression(kw.value, env) for kw in node.keywords}
        if "using" in keywords and keywords["using"] != ALIAS:
            self.fail(node, "using= is not the executor alias")
        # Reviewed imported ORM boundaries; SQL-only install/uninstall receive
        # the schema editor itself. Never silently drop the helpers' alias.
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "backfill_secret_hashes"
            and keywords.get("using") != ALIAS
        ):
            self.fail(node, "backfill_secret_hashes needs executor using=")
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "_process_batch"
            and (not node.args or self.expression(node.args[-1], env) != ALIAS)
        ):
            self.fail(node, "_process_batch needs the executor alias")
        if isinstance(node.func, ast.Name) and node.func.id in self.functions:
            function = self.functions[node.func.id]
            args = {
                arg.arg: self.expression(actual, env)
                for arg, actual in zip(function.args.args, node.args, strict=False)
            }
            args.update(
                {kw.arg: self.expression(kw.value, env) for kw in node.keywords}
            )
            if "@delegate" in env:
                args["@delegate"] = env["@delegate"]
            self.function(function, args)
            return UNKNOWN
        if (
            isinstance(node.func, ast.Name)
            and node.func.id in {"list", "iter"}
            and node.args
        ):
            return self.expression(node.args[0], env)
        if not isinstance(node.func, ast.Attribute):
            return UNKNOWN
        method = node.func.attr
        if (
            method == "get_model"
            and len(node.args) == 2
            and all(isinstance(arg, ast.Constant) for arg in node.args)
        ):
            return value("model", model=tuple(arg.value.lower() for arg in node.args))
        base = self.expression(node.func.value, env)
        keywords = {kw.arg: self.expression(kw.value, env) for kw in node.keywords}
        if method in BINDERS:
            alias = (
                self.expression(node.args[0], env)
                if node.args
                else keywords.get("alias", keywords.get("using"))
            )
            if alias != ALIAS:
                self.fail(
                    node, f"{method} is not bound to schema_editor.connection.alias"
                )
            return value("bound", base[1], base[2])
        if base[0] == "unbound":
            self.fail(node, f"unbound ORM .{method}")
            return UNBOUND
        if (
            method in INSTANCE_IO
            and env.get("@delegate") != ALIAS
            and base[0] != "bound"
            and keywords.get("using") != ALIAS
        ):
            self.fail(node, f"instance .{method} needs explicit executor using=")
        if method in RELATED_IO and base[0] == "row" and env.get("@delegate") != ALIAS:
            self.fail(
                node, f"implicit related .{method} needs a migration_database delegate"
            )
        if base[0] == "bound":
            if method == "select_related":
                return value(
                    "bound",
                    base[1]
                    | {
                        arg.value
                        for arg in node.args
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                    },
                    base[2],
                )
            if method in {"get", "first", "last", "create"}:
                return value("row", base[1], base[2])
            if method in {
                "values",
                "values_list",
                "count",
                "exists",
                "update",
                "delete",
            }:
                return UNKNOWN
            return base
        return UNKNOWN

    def function(self, function, env):
        key = (function.name, tuple(sorted(env.items())))
        if key in self.visiting:
            return
        self.visiting.add(key)
        env = dict(env)
        if env.get("using") == ALIAS and any(
            isinstance(d, ast.Name) and d.id in self.delegates
            for d in function.decorator_list
        ):
            env["@delegate"] = ALIAS
        self.statements(function.body, env)

    def inspect_expression(self, expression, env):
        for node in ast.walk(expression):
            if isinstance(node, (ast.Call, ast.Attribute, ast.Subscript)):
                self.expression(node, env)

    @staticmethod
    def join(env, branches):
        for key in set().union(*(branch.keys() for branch in branches)):
            values = [branch.get(key, UNKNOWN) for branch in branches]
            env[key] = values[0] if all(v == values[0] for v in values) else UNKNOWN

    def statements(self, statements, env):
        # An explicit alias is safe only if all incoming branches agree. The
        # primary scope rule handles implicit managers, not explicit overrides.
        for node in statements:
            if isinstance(node, ast.If):
                self.inspect_expression(node.test, env)
                branches = [dict(env), dict(env)]
                self.statements(node.body, branches[0])
                self.statements(node.orelse, branches[1])
                self.join(env, branches)
            elif isinstance(node, (ast.For, ast.While)):
                before = dict(env)
                if isinstance(node, ast.For):
                    self.inspect_expression(node.iter, env)
                    result = self.expression(node.iter, env)
                    if isinstance(node.target, ast.Name):
                        env[node.target.id] = (
                            value("row", result[1], result[2])
                            if result[0] == "bound"
                            else UNKNOWN
                        )
                else:
                    self.inspect_expression(node.test, env)
                self.statements(node.body, env)
                self.join(env, [before, dict(env)])
                self.statements(node.orelse, env)
            elif isinstance(node, ast.With):
                for item in node.items:
                    self.inspect_expression(item.context_expr, env)
                self.statements(node.body, env)
            elif isinstance(node, ast.Try):
                branches = [dict(env)]
                self.statements(node.body, branches[0])
                self.statements(node.orelse, branches[0])
                for handler in node.handlers:
                    branch = dict(env)
                    self.statements(handler.body, branch)
                    branches.append(branch)
                self.join(env, branches)
                self.statements(node.finalbody, env)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.statements(node.body, dict(env))
            else:
                self.inspect_expression(node, env)
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    result = self.expression(node.value, env)
                    targets = (
                        node.targets if isinstance(node, ast.Assign) else [node.target]
                    )
                    for target in targets:
                        if isinstance(target, ast.Name):
                            env[target.id] = result

    def scoped(self, function):
        """Require the real scope as the sole outer statement, not a nearby token.

        Implicit operations in imported helpers/signals/descriptors inherit this
        context. The older provenance walk remains secondary for explicit aliases;
        it is deliberately NOT the proof of implicit ORM closure.
        """
        name = "executor_alias_scope"
        imports = [
            node
            for node in self.tree.body
            if isinstance(node, ast.ImportFrom)
            and node.module == "tfc.migration_database"
            and node.level == 0
            and any(a.name == name and a.asname is None for a in node.names)
        ]
        shadowed = any(
            isinstance(n, ast.Name)
            and n.id == name
            and isinstance(n.ctx, (ast.Store, ast.Del))
            or isinstance(n, ast.arg)
            and n.arg == name
            or isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and n.name == name
            or isinstance(n, (ast.Import, ast.ImportFrom))
            and n not in imports
            and any(
                (a.asname or a.name.split(".")[0]) == name or a.name == "*"
                for a in n.names
            )
            for n in ast.walk(self.tree)
        )
        body = function.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body = body[1:]
        args = function.args.posonlyargs + function.args.args
        editor = args[1].arg if len(args) >= 2 else None
        wrapper = body[0] if len(body) == 1 and isinstance(body[0], ast.With) else None
        context = (
            wrapper.items[0].context_expr
            if wrapper and len(wrapper.items) == 1
            else None
        )
        valid = (
            len(imports) == 1
            and not shadowed
            and not function.decorator_list
            and isinstance(context, ast.Call)
            and isinstance(context.func, ast.Name)
            and context.func.id == name
            and not context.keywords
            and len(context.args) == 1
            and isinstance(context.args[0], ast.Name)
            and context.args[0].id == editor
            and wrapper.items[0].optional_vars is None
        )
        if not valid:
            self.fail(
                function,
                f"{function.name}: body must be enclosed first by executor_alias_scope({editor})",
            )
            return False
        for node in ast.walk(self.tree):
            if (
                isinstance(node, ast.Name)
                and node.id == function.name
                and isinstance(node.ctx, (ast.Store, ast.Del))
            ) or (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and node.name == function.name
                and node is not function
            ):
                self.fail(
                    node, f"{function.name}: callback binding must not be replaced"
                )
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == name
                and node is not context
            ):
                self.fail(
                    node, f"{function.name}: nested executor scopes are unsupported"
                )
            if isinstance(node, (ast.Yield, ast.YieldFrom, ast.Await)):
                self.fail(
                    node, f"{function.name}: deferred callback body is unsupported"
                )
            if (
                isinstance(node, ast.Name)
                and node.id == editor
                and isinstance(node.ctx, (ast.Store, ast.Del))
            ):
                self.fail(
                    node, f"{function.name}: executor parameter must not be rebound"
                )
            if isinstance(node, ast.Attribute) and isinstance(
                node.ctx, (ast.Store, ast.Del)
            ):
                root = node
                while isinstance(root, ast.Attribute):
                    root = root.value
                if isinstance(root, ast.Name) and root.id == editor:
                    self.fail(
                        node,
                        f"{function.name}: executor connection must not be mutated",
                    )
        return True

    def run(self):
        # Literal aliases override routing even inside a scope. Scan all local
        # helpers too, including dynamic getattr(manager, 'using') call shapes.
        if self.require_scope:
            for node in ast.walk(self.tree):
                if not isinstance(node, ast.Call):
                    continue
                aliases = [kw.value for kw in node.keywords if kw.arg == "using"]
                method = (
                    node.func.attr if isinstance(node.func, ast.Attribute) else None
                )
                if (
                    isinstance(node.func, ast.Call)
                    and isinstance(node.func.func, ast.Name)
                    and node.func.func.id == "getattr"
                    and len(node.func.args) >= 2
                    and isinstance(node.func.args[1], ast.Constant)
                ):
                    method = node.func.args[1].value
                if method in BINDERS:
                    aliases.extend(node.args[:1])
                    aliases.extend(
                        kw.value for kw in node.keywords if kw.arg == "alias"
                    )
                for alias in aliases:
                    if isinstance(alias, ast.Constant) and alias.value is not None:
                        self.fail(
                            alias, "literal alias overrides the migration executor"
                        )
        for node in ast.walk(self.tree):
            if (
                not isinstance(node, ast.Call)
                or not isinstance(node.func, ast.Attribute)
                or node.func.attr != "RunPython"
            ):
                continue
            callbacks = list(node.args[:2]) + [
                kw.value for kw in node.keywords if kw.arg in {"code", "reverse_code"}
            ]
            for callback in callbacks:
                # Django's built-in noop has no body/ORM to wrap. No arbitrary
                # imported callable named noop receives this exemption.
                if ast.unparse(callback) == "migrations.RunPython.noop":
                    continue
                if isinstance(callback, ast.Constant) and callback.value is None:
                    continue
                if (
                    not isinstance(callback, ast.Name)
                    or callback.id not in self.functions
                ):
                    self.fail(callback, "unresolved RunPython callback")
                    continue
                function = self.functions[callback.id]
                args = function.args.posonlyargs + function.args.args
                if len(args) < 2:
                    self.fail(
                        function, "RunPython callback needs an executor parameter"
                    )
                    continue
                env = {args[1].arg: SCHEMA}
                if self.require_scope and self.scoped(function):
                    env["@delegate"] = ALIAS
                self.function(function, env)
        return sorted(self.errors)


def audit_repository(root):
    sources = {}
    errors = []
    relations = {}
    for relative in ADDED_MIGRATIONS:
        path = root / relative
        if not path.is_file():
            errors.append(f"{relative}: missing recorded migration")
            continue
        sources[relative] = path.read_text()
        app = path.parent.parent.name
        app = "prism" if app == "agentcc" else app
        for operation in ast.walk(ast.parse(sources[relative])):
            if not isinstance(operation, ast.Call) or not isinstance(
                operation.func, ast.Attribute
            ):
                continue
            kwargs = {kw.arg: kw.value for kw in operation.keywords}
            fields = []
            if operation.func.attr == "CreateModel" and isinstance(
                kwargs.get("fields"), ast.List
            ):
                model = kwargs.get("name")
                fields = [
                    field.elts
                    for field in kwargs["fields"].elts
                    if isinstance(field, ast.Tuple)
                ]
            elif operation.func.attr in {"AddField", "AlterField"}:
                model = kwargs.get("model_name")
                fields = [(kwargs.get("name"), kwargs.get("field"))]
            else:
                continue
            if not isinstance(model, ast.Constant):
                continue
            for name, field in fields:
                if (
                    isinstance(name, ast.Constant)
                    and isinstance(field, ast.Call)
                    and isinstance(field.func, ast.Attribute)
                    and field.func.attr
                    in {"ForeignKey", "OneToOneField", "ManyToManyField"}
                ):
                    target = next(
                        (
                            kw.value.value
                            for kw in field.keywords
                            if kw.arg == "to" and isinstance(kw.value, ast.Constant)
                        ),
                        "",
                    )
                    target = (
                        (app, model.value.lower())
                        if target == "self"
                        else tuple(target.lower().split("."))
                    )
                    relations.setdefault((app, model.value.lower()), {})[name.value] = (
                        target
                    )
    for relative, source in sources.items():
        errors.extend(
            f"{relative}:{error}" for error in AliasAudit(source, relations).run()
        )
    return errors


def explicit_audit(source, relations=()):
    """Exercise the pre-existing secondary checks without the new scope rule."""
    return AliasAudit(source, relations, require_scope=False)


class ExecutorAliasTests(unittest.TestCase):
    def test_repository_migrations(self):
        errors = audit_repository(ROOT)
        self.assertFalse(errors, "\n" + "\n".join(errors))

    def test_alias_provenance_and_reverse_helpers(self):
        template = """
def helper(Model, using):
    Model._base_manager.db_manager(using).filter(pk=1).update(flag=True)
def forwards(apps, schema_editor):
    db = schema_editor.connection.alias
    helper(apps.get_model("x", "M"), USING)
def backwards(apps, schema_editor):
    db = schema_editor.connection.alias
    helper(apps.get_model("x", "M"), USING)
x = migrations.RunPython(forwards, reverse_code=backwards)
"""
        self.assertFalse(explicit_audit(template.replace("USING", "db")).run())
        self.assertTrue(explicit_audit(template.replace("USING", '"default"')).run())
        self.assertTrue(
            explicit_audit(
                template.replace("USING", "db", 1).replace("USING", '"default"')
            ).run()
        )

    def test_delegate_and_imported_boundaries(self):
        source = """
from tfc.migration_database import migration_database
@migration_database
def delegated(Model, *, using=None):
    Model.objects.update(flag=True)
def forwards(apps, schema_editor):
    db = schema_editor.connection.alias
    delegated(apps.get_model("x", "M"), using=db)
    backfill_secret_hashes(apps.get_model("x", "M"), using=db)
    MigrationRecorder(connections[db]).migration_qs.count()
x = migrations.RunPython(forwards)
"""
        self.assertFalse(explicit_audit(source).run())
        for bad in (
            source.replace("using=db", 'using="default"'),
            source.replace("@migration_database", ""),
            source.replace("connections[db]", 'connections["default"]'),
            source.replace(", using=db)", ")"),
        ):
            self.assertTrue(explicit_audit(bad).run())

    def test_querysets_instances_and_related_reads(self):
        template = """
def forwards(apps, schema_editor):
    db = schema_editor.connection.alias
    Model = apps.get_model("x", "M")
    manager = Model._base_manager
    rows = manager.using(db).select_related("connection__binding")
    for row in rows.iterator():
        connection = row.connection
        row.label = connection.binding.label
        row.save(using=db)
x = migrations.RunPython(forwards, migrations.RunPython.noop)
"""

        def check(source):
            return explicit_audit(
                source,
                {
                    ("x", "m"): {"connection": ("x", "c")},
                    ("x", "c"): {"binding": ("x", "b")},
                },
            ).run()

        self.assertFalse(check(template))
        self.assertTrue(check(template.replace("manager.using(db)", "manager.all()")))
        self.assertTrue(check(template.replace("using=db", 'using="default"')))
        self.assertTrue(check(template.replace("row.save(using=db)", "row.save()")))
        self.assertTrue(check(template.replace("connection__binding", "connection")))
        self.assertTrue(
            check(template.replace('.select_related("connection__binding")', ""))
        )


# Exact lens-A attack sources; parsed only, never imported.
LENS_A_FIXTURES = {
    "branch_alias_leak": "\n"
    "def forwards(apps, schema_editor):\n"
    "    db = schema_editor.connection.alias\n"
    '    Model = apps.get_model("x", "M")\n'
    "    if schema_editor.connection.vendor == "
    '"postgresql":\n'
    "        manager = Model.objects\n"
    "    else:\n"
    "        manager = Model.objects.using(db)\n"
    "    manager.update(flag=True)\n"
    "x = migrations.RunPython(forwards, "
    "migrations.RunPython.noop)\n",
    "create_signal_implicit_read": "from helper_signal import "
    "auto_assign_workspace\n"
    "\n"
    "def forwards(apps, schema_editor):\n"
    "    db = schema_editor.connection.alias\n"
    '    Model = apps.get_model("x", "M")\n'
    "    "
    "Model.objects.using(db).create(organization_id=1, "
    "flag=True)\n"
    "x = migrations.RunPython(forwards, "
    "migrations.RunPython.noop)\n",
    "external_helper_unbound": "from helper_unbound import external_helper\n"
    "\n"
    "def forwards(apps, schema_editor):\n"
    "    db = schema_editor.connection.alias\n"
    '    Model = apps.get_model("x", "M")\n'
    "    external_helper(Model)\n"
    "x = migrations.RunPython(forwards, "
    "migrations.RunPython.noop)\n",
    "getattr_manager_unbound": "\n"
    "def forwards(apps, schema_editor):\n"
    "    db = schema_editor.connection.alias\n"
    '    Model = apps.get_model("x", "M")\n'
    '    manager = getattr(Model, "objects")\n'
    "    manager.filter(pk=1).update(flag=True)\n"
    "x = migrations.RunPython(forwards, "
    "migrations.RunPython.noop)\n",
    "getattr_method_unbound": "\n"
    "def forwards(apps, schema_editor):\n"
    "    db = schema_editor.connection.alias\n"
    '    Model = apps.get_model("x", "M")\n'
    "    manager = Model._base_manager\n"
    '    getattr(manager, "update")(flag=True)\n'
    "x = migrations.RunPython(forwards, "
    "migrations.RunPython.noop)\n",
    "instance_save_unbound": "\n"
    "def forwards(apps, schema_editor):\n"
    "    db = schema_editor.connection.alias\n"
    '    Model = apps.get_model("x", "M")\n'
    "    row = Model.objects.using(db).get(pk=1)\n"
    "    row.save()\n"
    "x = migrations.RunPython(forwards, "
    "migrations.RunPython.noop)\n",
    "local_helper_return_unbound": "def build_rows(Model):\n"
    "    return Model.objects\n"
    "\n"
    "def forwards(apps, schema_editor):\n"
    "    db = schema_editor.connection.alias\n"
    '    Model = apps.get_model("x", "M")\n'
    "    rows = build_rows(Model)\n"
    "    rows.update(flag=True)\n"
    "x = migrations.RunPython(forwards, "
    "migrations.RunPython.noop)\n",
    "manager_variable_unbound": "\n"
    "def forwards(apps, schema_editor):\n"
    "    db = schema_editor.connection.alias\n"
    '    Model = apps.get_model("x", "M")\n'
    "    manager = Model._base_manager\n"
    "    manager.filter(pk=1).update(flag=True)\n"
    "x = migrations.RunPython(forwards, "
    "migrations.RunPython.noop)\n",
    "reverse_related_manager_unbound": "\n"
    "def forwards(apps, schema_editor):\n"
    "    db = schema_editor.connection.alias\n"
    '    Model = apps.get_model("x", "M")\n'
    "    row = "
    "Model.objects.using(db).get(pk=1)\n"
    "    row.children.all().update(flag=True)\n"
    "x = migrations.RunPython(forwards, "
    "migrations.RunPython.noop)\n",
    "save_signal_implicit_read": "from helper_signal import "
    "auto_assign_workspace\n"
    "\n"
    "def forwards(apps, schema_editor):\n"
    "    db = schema_editor.connection.alias\n"
    '    Model = apps.get_model("x", "M")\n'
    "    row = Model.objects.using(db).get(pk=1)\n"
    '    row.save(using=db, update_fields=["flag"])\n'
    "x = migrations.RunPython(forwards, "
    "migrations.RunPython.noop)\n",
}


def scope_fixture(source):
    """Wrap local RunPython bodies without altering any adversarial statement."""
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in {"forwards", "backwards"}:
            node.body = [
                ast.With(
                    items=[
                        ast.withitem(
                            context_expr=ast.Call(
                                func=ast.Name(
                                    id="executor_alias_scope", ctx=ast.Load()
                                ),
                                args=[
                                    ast.Name(id=node.args.args[1].arg, ctx=ast.Load())
                                ],
                                keywords=[],
                            )
                        )
                    ],
                    body=node.body,
                )
            ]
    tree.body.insert(
        0,
        ast.ImportFrom(
            module="tfc.migration_database",
            names=[ast.alias(name="executor_alias_scope")],
            level=0,
        ),
    )
    return ast.unparse(ast.fix_missing_locations(tree))


def scope_namespace():
    """Execute only the stdlib affinity module, never import Django/settings."""
    path = ROOT / "futureagi/tfc/migration_database.py"
    namespace = {"__name__": "executor_affinity_source_probe"}
    exec(compile(path.read_text(), str(path), "exec"), namespace)
    return namespace


def source_definitions(relative, names, namespace):
    path = ROOT / relative
    tree = ast.parse(path.read_text())
    nodes = [
        copy.deepcopy(n)
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names
    ]
    if {n.name for n in nodes} != set(names):
        raise AssertionError(f"Missing source definitions in {relative}: {names}")
    for node in nodes:
        node.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)


class StructuralScopeTests(unittest.TestCase):
    def test_lens_a_attacks_require_scope(self):
        for name, source in LENS_A_FIXTURES.items():
            with self.subTest(attack=name):
                errors = AliasAudit(source).run()
                self.assertTrue(
                    any("body must be enclosed" in e for e in errors), errors
                )
                self.assertEqual(AliasAudit(scope_fixture(source)).run(), [])

    def test_scope_must_enclose_every_statement_and_both_directions(self):
        source = textwrap.dedent("""
            def forwards(apps, schema_editor):
                imported_helper(apps)
            def backwards(apps, schema_editor):
                imported_helper(apps)
            x = migrations.RunPython(forwards, reverse_code=backwards)
        """)
        good = scope_fixture(source)
        self.assertEqual(AliasAudit(good).run(), [])
        bad_sources = [
            good.replace(
                "executor_alias_scope(schema_editor)",
                "executor_alias_scope(other_editor)",
                1,
            ),
            good.replace(
                "with executor_alias_scope(schema_editor):",
                "with unrelated_scope(schema_editor):",
                1,
            ),
            good.replace(
                "    with executor_alias_scope(schema_editor):",
                "    imported_helper(apps)\n    with executor_alias_scope(schema_editor):",
                1,
            ),
            good.replace(
                "        imported_helper(apps)",
                "        pass\n    imported_helper(apps)",
                1,
            ),
            good.replace("        imported_helper(apps)", "        pass", 1).replace(
                "    with executor_alias_scope(schema_editor):\n        imported_helper(apps)",
                "    imported_helper(apps)",
            ),
            good.replace("from tfc.migration_database", "from lookalike"),
            good + "\nexecutor_alias_scope = unrelated_scope\n",
            good + "\nforwards = imported_helper\n",
            good.replace(
                "        imported_helper(apps)",
                "        with executor_alias_scope(other_editor):\n            imported_helper(apps)",
                1,
            ),
            good.replace("def forwards", "@unrelated_decorator\ndef forwards"),
            good.replace(
                "        imported_helper(apps)",
                "        schema_editor = other_editor",
                1,
            ),
            good.replace(
                "        imported_helper(apps)",
                "        schema_editor.connection.alias = 'replica'",
                1,
            ),
            good.replace(
                "        imported_helper(apps)",
                "        yield imported_helper(apps)",
                1,
            ),
            source.replace("backwards)", "external.noop)"),
        ]
        for bad in bad_sources:
            with self.subTest(source=bad):
                self.assertTrue(AliasAudit(bad).run())

    def test_scoped_explicit_alias_overrides_are_rejected(self):
        template = """
def forwards(apps, schema_editor):
    db = schema_editor.connection.alias if schema_editor is not None else DEFAULT_DB_ALIAS
    Model = apps.get_model("x", "M")
    STATEMENT
x = migrations.RunPython(forwards, migrations.RunPython.noop)
"""
        for statement in (
            "Model.objects.using(db).update(flag=True)",
            "Model.objects.db_manager(db).get(pk=1).save(using=db)",
        ):
            self.assertEqual(
                AliasAudit(
                    scope_fixture(template.replace("STATEMENT", statement))
                ).run(),
                [],
            )
        for statement in (
            'Model.objects.using("default").update(flag=True)',
            'Model.objects.db_manager("replica").update(flag=True)',
            'Model.objects.get(pk=1).save(using="default_direct")',
            'getattr(Model.objects, "using")("replica").update(flag=True)',
            'helper(Model, using="default")',
            'connections["default"].cursor()',
            'if condition:\n        alias = "replica"\n    else:\n        alias = db\n    Model.objects.using(alias).update(flag=True)',
            'if condition:\n        alias = db\n    else:\n        alias = "replica"\n    Model.objects.using(alias).update(flag=True)',
        ):
            with self.subTest(statement=statement):
                self.assertTrue(
                    AliasAudit(
                        scope_fixture(template.replace("STATEMENT", statement))
                    ).run()
                )


class ScopeRuntimeTests(unittest.TestCase):
    def test_all_callback_alias_expressions_accept_none(self):
        checked = 0
        for relative in ADDED_MIGRATIONS:
            audit = AliasAudit((ROOT / relative).read_text())
            names = {
                callback.id
                for node in ast.walk(audit.tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "RunPython"
                for callback in list(node.args[:2])
                + [
                    kw.value
                    for kw in node.keywords
                    if kw.arg in {"code", "reverse_code"}
                ]
                if isinstance(callback, ast.Name)
            }
            for name in names:
                function = audit.functions[name]
                parents = {
                    child: node
                    for node in ast.walk(function)
                    for child in ast.iter_child_nodes(node)
                }
                for node in ast.walk(function):
                    if (
                        not isinstance(node, ast.Attribute)
                        or ast.unparse(node) != "schema_editor.connection.alias"
                    ):
                        continue
                    fallback = parents[node]
                    with self.subTest(
                        migration=relative, callback=name, line=node.lineno
                    ):
                        self.assertIsInstance(fallback, ast.IfExp)
                        self.assertEqual(
                            ast.unparse(fallback.test), "schema_editor is not None"
                        )
                        self.assertEqual(
                            ast.unparse(fallback.orelse), "DEFAULT_DB_ALIAS"
                        )
                        expression = compile(
                            ast.Expression(body=fallback), relative, "eval"
                        )
                        for alias in (None, "default", "default_direct"):
                            editor = (
                                None
                                if alias is None
                                else SimpleNamespace(
                                    connection=SimpleNamespace(alias=alias)
                                )
                            )
                            self.assertEqual(
                                eval(
                                    expression,
                                    {
                                        "schema_editor": editor,
                                        "DEFAULT_DB_ALIAS": "default",
                                    },
                                ),
                                alias or "default",
                            )
                    checked += 1
        self.assertGreater(checked, 0)

    def test_alias_validation_nesting_none_and_exception_reset(self):
        ns = scope_namespace()
        scope = ns["executor_alias_scope"]
        current = ns["delegated_database_alias"]
        self.assertIsNone(current())
        for alias in ("default", "default_direct"):
            with scope(SimpleNamespace(connection=SimpleNamespace(alias=alias))):
                self.assertEqual(current(), alias)
                with scope(None):
                    self.assertEqual(current(), alias)
                with scope(
                    SimpleNamespace(connection=SimpleNamespace(alias="default"))
                ):
                    self.assertEqual(current(), "default")
                self.assertEqual(current(), alias)
                with self.assertRaisesRegex(RuntimeError, "probe"):
                    with scope(
                        SimpleNamespace(
                            connection=SimpleNamespace(alias="default_direct")
                        )
                    ):
                        raise RuntimeError("probe")
                self.assertEqual(current(), alias)
                for invalid in ("replica", "other", "", None):
                    with self.assertRaises(ValueError):
                        with scope(
                            SimpleNamespace(connection=SimpleNamespace(alias=invalid))
                        ):
                            self.fail("invalid alias entered scope")
                    self.assertEqual(current(), alias)
            self.assertIsNone(current())
        with scope(None):
            self.assertIsNone(current())

    def test_actual_router_and_generic_signal_source_inherit_scope(self):
        ns = scope_namespace()
        ns.update(
            _replica_configured=lambda: True,
            _is_force_primary=lambda: False,
            _is_locking_read=lambda hints: bool(hints.get("for_update")),
        )
        source_definitions("futureagi/tfc/routers.py", ["ReadReplicaRouter"], ns)
        router = ns["ReadReplicaRouter"](opt_in=["ALL_MODELS_USE_READ_REPLICA"])
        ns.update(
            get_current_workspace=lambda: None, get_current_organization=lambda: None
        )
        source_definitions(
            "futureagi/tfc/utils/signals.py", ["auto_assign_workspace"], ns
        )
        reads = []

        class Related:
            def __get__(self, instance, owner):
                reads.append(router.db_for_read(owner, instance=instance))
                return SimpleNamespace(pk=1)

        class HistoricalRow:
            pk = 1
            workspace_id = organization_id = 1
            workspace = Related()
            organization = Related()

        row = HistoricalRow()
        signal = ns["auto_assign_workspace"]
        signal(HistoricalRow, row, using="default_direct")
        self.assertEqual(reads, ["replica", "replica"])
        for alias in ("default_direct", "default"):
            reads.clear()
            with ns["executor_alias_scope"](
                SimpleNamespace(connection=SimpleNamespace(alias=alias))
            ):
                signal(HistoricalRow, row, using=alias)
                self.assertEqual(reads, [alias, alias])
                self.assertEqual(router.db_for_write(HistoricalRow), alias)
            self.assertEqual(router.db_for_read(HistoricalRow), "replica")
            self.assertEqual(router.db_for_write(HistoricalRow), "default")

    def test_none_editor_callbacks_preserve_writes(self):
        from datetime import timedelta
        from urllib.parse import urlsplit

        ns = scope_namespace()
        ns.update(
            DEFAULT_DB_ALIAS="default",
            LIFETIME=timedelta(days=90),
            F=MagicMock(),
            urlsplit=urlsplit,
            _DEFAULT_PORTS={"http": 80, "https": 443},
            _CREDENTIAL_ID_KEYS=("credential_id", "credentialId", "credentialID"),
        )
        cases = [
            (
                "futureagi/agentcc/migrations/0054_ee_agent_key_expiry_backfill.py",
                ["backfill_agent_key_expiry"],
            ),
            (
                "futureagi/agentcc/migrations/0057_mcp_credential_origin.py",
                ["bind_sent_credentials", "_origin", "_reference_id"],
            ),
            (
                "futureagi/model_hub/migrations/0127_experimentrun_ordinal_datasetbaseline.py",
                ["assign_ordinals"],
            ),
        ]
        for path, names in cases:
            source_definitions(path, names, ns)
        for alias in (None, "default", "default_direct"):
            with self.subTest(alias=alias):
                editor = (
                    None
                    if alias is None
                    else SimpleNamespace(connection=SimpleNamespace(alias=alias))
                )
                expected = alias or "default"
                apps = MagicMock()
                model = apps.get_model.return_value
                rows = model._base_manager.using.return_value
                ns["backfill_agent_key_expiry"](apps, editor)
                model._base_manager.using.assert_called_once_with(expected)
                rows.filter.assert_called_once_with(
                    ee_agent_key__isnull=False, expires_at__isnull=True
                )
                rows.filter.return_value.update.assert_called_once_with(
                    expires_at=ns["F"]("created_at") + ns["LIFETIME"]
                )

                apps = MagicMock()
                model = apps.get_model.return_value
                runs = model._base_manager.using.return_value
                ordered = runs.filter.return_value.order_by.return_value.values_list.return_value
                ordered.iterator.return_value = [(11, 1), (12, 1), (13, 2)]
                ns["assign_ordinals"](apps, editor)
                model._base_manager.using.assert_called_once_with(expected)
                ordered.iterator.assert_called_once_with(chunk_size=1000)
                updates = runs.filter.return_value.update.call_args_list
                self.assertEqual(
                    [c.kwargs for c in updates],
                    [{"ordinal": 1}, {"ordinal": 2}, {"ordinal": 1}],
                )
                self.assertEqual(
                    [c.kwargs for c in runs.filter.call_args_list],
                    [{"ordinal__isnull": True}, {"pk": 11}, {"pk": 12}, {"pk": 13}],
                )

                credentials, configs = MagicMock(), MagicMock()
                apps = MagicMock()
                apps.get_model.side_effect = [credentials, configs]
                rows = credentials._base_manager.using.return_value.filter.return_value.exclude.return_value
                rows.values_list.return_value = [7]
                rows.filter.return_value.__iter__.return_value = [
                    SimpleNamespace(pk=9, server_id="service")
                ]
                configs._base_manager.using.return_value.filter.return_value.values_list.return_value.first.return_value = {
                    "servers": {
                        "service": {
                            "url": "https://example.invalid/mcp",
                            "credential": {"credential_id": "9", "state": "active"},
                        }
                    }
                }
                ns["bind_sent_credentials"](apps, editor)
                self.assertEqual(
                    [c.args for c in credentials._base_manager.using.call_args_list],
                    [(expected,), (expected,)],
                )
                configs._base_manager.using.assert_called_once_with(expected)
                credentials._base_manager.using.return_value.filter.return_value.update.assert_called_once_with(
                    origin="https://example.invalid:443"
                )
                self.assertIsNone(ns["delegated_database_alias"]())


if __name__ == "__main__":
    if len(sys.argv) > 1:
        ROOT = Path(sys.argv.pop(1)).resolve()
    unittest.main(verbosity=2)
