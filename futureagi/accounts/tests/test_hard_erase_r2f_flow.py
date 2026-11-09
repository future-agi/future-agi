"""Ruling 407 offline regression tests: real parser, no application imports."""

import ast
import runpy
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

TESTS = Path(__file__).resolve().parent
SERVICES = TESTS.parent / "services"
SQL = runpy.run_path(str(TESTS / "test_hard_erase_sql_flow.py"))
LIFE = runpy.run_path(str(TESTS / "test_hard_erase_lifecycle_flow.py"))

# Independent source-derived metadata for the eight review omissions.
OMITTED = {
    ("agentcc.models.mcp_vendor", "AgentccMCPVendorBinding"): "encrypted_bearer",
    ("agentcc.models.mcp_vendor", "AgentccMCPVendorConnection"): "encrypted_session",
    ("agentcc.models.mcp_vendor", "AgentccMCPVendorCleanup"): "encrypted_session",
    ("agentcc.models.siem", "AgentccSIEMDestination"): "encrypted_secret",
    ("agentcc.models.webhook", "AgentccWebhook"): "encrypted_secret",
    ("mcp_server.models.oauth_client", "MCPOAuthClient"): "client_secret_hash",
    ("mcp_server.models.oauth_code", "MCPOAuthCode"): "code_challenge",
    ("mcp_server.models.connection", "MCPConnection"): "access_token",
}


def fake_model(module, name, fields, table=None):
    return type(
        name,
        (),
        {
            "__module__": module,
            "_meta": SimpleNamespace(
                abstract=False,
                proxy=False,
                auto_created=False,
                db_table=table or "new_app_" + name.lower(),
                concrete_fields=fields,
                get_fields=lambda: fields,
            ),
        },
    )


def field(name, verbose_name="", kind="CharField"):
    return type(kind, (), {"name": name, "verbose_name": verbose_name})()


class PureReadTests(unittest.TestCase):
    def test_executing_wrappers_and_all_non_reads_refuse(self):
        ns, conn, _ = SQL["SQLBoundaryTests"]().namespace()
        conn.in_atomic_block = False
        statements = [
            "EXPLAIN ANALYZE DELETE FROM accounts_user",
            "EXPLAIN (ANALYZE TRUE, FORMAT JSON) UPDATE accounts_user SET id=id",
            "EXPLAIN (ANALYZE FALSE) SELECT * FROM accounts_user",
            "EXPLAIN DELETE FROM accounts_user",
            "PREPARE erasure AS DELETE FROM accounts_user",
            "EXECUTE erasure",
            "EXPLAIN ANALYZE EXECUTE erasure",
            "EXPLAIN EXECUTE erasure",
            "SELECT * INTO copied_users FROM accounts_user",
            "SELECT * FROM accounts_user FOR SHARE",
            "CALL application_proc()",
            "DO $$ BEGIN PERFORM application_proc(); END $$",
            "SELECT 1; EXECUTE erasure",
            "INSERT INTO accounts_user(id) VALUES (1)",
            "SELECT * FROM accounts_user FOR UPDATE",
        ]
        statements += [
            f"WITH x AS (SELECT * FROM accounts_user) {verb} other_table"
            for verb in (
                "DELETE",
                "UPDATE",
                "INSERT",
                "MERGE",
                "TRUNCATE",
                "ALTER",
                "DROP",
                "CREATE",
                "GRANT",
                "REVOKE",
                "PREPARE",
                "EXECUTE",
                "DEALLOCATE",
                "CALL",
                "DO",
                "COPY",
                "LOCK",
                "VACUUM",
                "CLUSTER",
                "REINDEX",
                "COMMENT",
                "SECURITY LABEL",
                "ANALYZE",
            )
        ]
        for text in statements:
            for many in (False, True):
                with self.subTest(sql=text, many=many):
                    driver = Mock()
                    with self.assertRaises(SQL["Refused"]):
                        ns["guard_execute"](
                            driver, text, [], many, {"connection": conn}
                        )
                    driver.assert_not_called()

    def test_pure_reads_and_nonexecuting_explain_reach_driver(self):
        ns, conn, _ = SQL["SQLBoundaryTests"]().namespace()
        conn.in_atomic_block = False
        for text in (
            "SELECT * FROM accounts_user",
            "WITH x AS (SELECT * FROM accounts_user) SELECT * FROM x",
            'VALUES (1, "accounts_user")',
            "EXPLAIN SELECT * FROM accounts_user",
            # Ruling 584 permits EXPLAIN options only on unprotected statements.
            "EXPLAIN (FORMAT JSON, COSTS TRUE) SELECT * FROM ordinary_table",
            "EXPLAIN WITH x AS (SELECT * FROM accounts_user) SELECT * FROM x",
            "SELECT 'EXECUTE DELETE UPDATE ANALYZE' FROM accounts_user",
            'SELECT "delete", "update" FROM accounts_user',
            "/* EXECUTE */ SELECT * FROM accounts_user -- ANALYZE\n",
            "UPDATE other_table SET name='accounts_user'",
            "INSERT INTO other_table(id) VALUES (1) ON CONFLICT(id) DO NOTHING",
            "INSERT INTO other_table(id) VALUES (1) ON CONFLICT(id) DO UPDATE SET id=excluded.id",
        ):
            for many in (False, True):
                with self.subTest(sql=text, many=many):
                    driver = Mock(return_value="ok")
                    self.assertEqual(
                        ns["guard_execute"](
                            driver, text, [], many, {"connection": conn}
                        ),
                        "ok",
                    )
                    driver.assert_called_once()


class CredentialCensusTests(unittest.TestCase):
    def test_new_credential_models_are_automatically_protected(self):
        policy = runpy.run_path(str(SERVICES / "hard_erase_sql_policy.py"))
        models = [
            fake_model("new.models", "New" + str(i), [field(name)])
            for i, name in enumerate(
                (
                    "token",
                    "secret",
                    "key",
                    "password",
                    "credential",
                    "oauth",
                    "signing",
                    "bearer",
                    "session",
                    "webhook_secret",
                )
            )
        ]
        models += [
            fake_model("new.models", "Verbose", [field("blob", "OAuth credential")]),
            fake_model(
                "new.models", "Encrypted", [field("blob", kind="EncryptedTextField")]
            ),
        ]
        plain = fake_model("new.models", "Plain", [field("name")])
        selected = policy["protected_models"]([*models, plain])
        self.assertEqual(selected, set(models))

    def test_eight_omitted_models_refuse_execute_and_executemany(self):
        policy = runpy.run_path(str(SERVICES / "hard_erase_sql_policy.py"))
        models = []
        for module, name in OMITTED:
            path = TESTS.parents[1] / (module.replace(".", "/") + ".py")
            node = next(
                n
                for n in ast.parse(path.read_text()).body
                if isinstance(n, ast.ClassDef) and n.name == name
            )
            fields = []
            table = (
                ("prism" if module.startswith("agentcc.") else "mcp_server")
                + "_"
                + name.lower()
            )
            for child in node.body:
                if isinstance(child, ast.Assign) and isinstance(child.value, ast.Call):
                    for target in child.targets:
                        if isinstance(target, ast.Name):
                            fields.append(
                                field(target.id, kind=ast.unparse(child.value.func))
                            )
                if isinstance(child, ast.ClassDef) and child.name == "Meta":
                    for assignment in child.body:
                        if isinstance(assignment, ast.Assign) and any(
                            isinstance(t, ast.Name) and t.id == "db_table"
                            for t in assignment.targets
                        ):
                            table = ast.literal_eval(assignment.value)
            self.assertTrue(fields)
            if (module, name) == ("agentcc.models.webhook", "AgentccWebhook"):
                concrete_names = {item.name for item in fields}
                self.assertTrue(
                    {"encrypted_headers", "encrypted_secret"}.issubset(concrete_names)
                )
                self.assertTrue({"headers", "secret"}.isdisjoint(concrete_names))
            models.append(fake_model(module, name, fields, table))
        selected = policy["protected_models"](models)
        self.assertEqual({m.__name__ for m in selected}, {name for _, name in OMITTED})
        ns, conn, _ = SQL["SQLBoundaryTests"]().namespace()
        ns["_TABLES"] = {m._meta.db_table for m in selected}
        conn.in_atomic_block = False
        for model in models:
            for verb in ("DELETE FROM", "UPDATE"):
                text = f"{verb} {model._meta.db_table}" + (
                    " SET id=id" if verb == "UPDATE" else ""
                )
                for many in (False, True):
                    with self.subTest(model=model.__name__, many=many):
                        driver = Mock()
                        with self.assertRaises(SQL["Refused"]):
                            ns["guard_execute"](
                                driver, text, [], many, {"connection": conn}
                            )
                        driver.assert_not_called()


class RoleEntryOrderingTests(unittest.TestCase):
    def test_role_entry_holds_complete_keys_before_any_write_or_row_lock(self):
        tree = ast.parse((SERVICES / "member_role_service.py").read_text())
        function = next(
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "update_member_role"
        )
        function.decorator_list = []
        for arg in function.args.kwonlyargs:
            arg.annotation = None
        function.returns = None
        life, _, cursor = LIFE["LifecycleTests"]().namespace()
        org, actor, target = UUID(int=1), UUID(int=2), UUID(int=3)
        keys = life["identity_keys"](organizations=[org], people=[actor, target])
        events = []

        @contextmanager
        def atomic():
            yield

        def write(**kwargs):
            # Erase takes all lifecycle keys before its membership FOR UPDATE.
            # If role writes first, the independent eraser can own the advisory
            # key and wait on this row while role's subsequent issuance waits
            # on that advisory key: the old two-transaction wait cycle.
            life["require_held"]("default", keys)
            events.append("role-row-write-after-advisory")

        ns = {
            "OrganizationMembership": SimpleNamespace(objects=Mock()),
            "_has_pending_invite": Mock(return_value=False),
            "_validate_workspace_access_in_org": Mock(),
            "_validate_workspace_in_org": Mock(),
            "transaction": SimpleNamespace(atomic=atomic),
            "identity_keys": life["identity_keys"],
            "lifecycle_lock": life["lifecycle_lock"],
            "issuance_entry": runpy.run_path(
                str(TESTS / "hard_erase_entry_support.py")
            )["entry_namespace"](life)["issuance_entry"],
            "_apply_org_level_change": write,
            "_apply_ws_level_change": write,
        }
        ns["OrganizationMembership"].objects.get.return_value = SimpleNamespace(
            is_active=True
        )
        exec(
            compile(
                ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])),
                "role_entry",
                "exec",
            ),
            ns,
        )
        ns["update_member_role"](
            organization=SimpleNamespace(pk=org),
            actor=SimpleNamespace(pk=actor),
            target_user_id=target,
            org_level=40,
        )
        self.assertEqual(events, ["role-row-write-after-advisory"])
        self.assertEqual(
            [call.args[1][0] for call in cursor.execute.call_args_list[1:]],
            [life["advisory_number"](key) for key in sorted(keys)],
        )
        self.assertIsNone(life["_ACTIVE"].get())


if __name__ == "__main__":
    unittest.main()
