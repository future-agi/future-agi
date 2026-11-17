"""Offline ruling-349 regression probes; real sqlparse, no Django/DB imports."""

import ast
import runpy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

TESTS = Path(__file__).resolve().parent
SERVICES = TESTS.parent / "services"
SQL = runpy.run_path(str(TESTS / "test_hard_erase_sql_flow.py"))
KEYS = runpy.run_path(str(TESTS / "test_hard_erase_keys_flow.py"))
LIFE = runpy.run_path(str(TESTS / "test_hard_erase_lifecycle_flow.py"))


class NameBoundaryTests(unittest.TestCase):
    def test_name_based_refusal_variants(self):
        ns, conn, _ = SQL["SQLBoundaryTests"]().namespace()
        conn.in_atomic_block = False
        for text in (
            "TRUNCATE TABLE ONLY accounts_user CASCADE",
            'TRUNCATE /* format */ TABLE ONLY public."ACCOUNTS_USER" CASCADE',
            "UPDATE accounts_user * AS u SET organization_id=NULL",
            'UPDATE public."accounts_user" * u SET organization_id=NULL',
            "UPDATE accounts_user SET name='ordinary'",
            "UPDATE other_table SET id=1 FROM accounts_user",
            "DELETE FROM other_table WHERE id IN (SELECT id FROM accounts_user)",
            "ALTER TABLE ONLY accounts_user ADD COLUMN harmless text",
            "DROP TABLE IF EXISTS public.accounts_user CASCADE",
            "WITH u AS (SELECT * FROM accounts_user) UPDATE other_table SET id=1",
            "WITH u AS (SELECT * FROM accounts_user FOR UPDATE) SELECT * FROM u",
            "INSERT INTO other_table SELECT id FROM accounts_user ON CONFLICT(id) DO UPDATE SET id=1",
            "MERGE INTO other_table USING accounts_user ON true WHEN MATCHED THEN DELETE",
            'DELETE FROM "public"."agentcc_api_key"',
            "DELETE FROM U&\"accounts!005fuser\" UESCAPE '!'",
            "WITH u AS (INSERT INTO accounts_user(id) VALUES (%s) RETURNING id) SELECT * FROM u",
        ):
            for many in (False, True):
                with self.subTest(sql=text, many=many):
                    driver = Mock()
                    with self.assertRaises(SQL["Refused"]):
                        ns["guard_execute"](
                            driver, text, [], many, {"connection": conn}
                        )
                    driver.assert_not_called()

    def test_nonmutating_and_unprotected_statements_reach_driver(self):
        ns, conn, _ = SQL["SQLBoundaryTests"]().namespace()
        conn.in_atomic_block = False
        for text in (
            "SELECT * FROM accounts_user",
            "SELECT 'DELETE FROM accounts_user'",
            "SELECT * FROM U&\"accounts!005fuser\" UESCAPE '!'",
            "UPDATE U&\"other!005ftable\" UESCAPE '!' SET id=1",
            "DELETE FROM accounts_envelope_nonce WHERE exp < %s",
            "UPDATE other_table SET name='accounts_user'",
            "DELETE FROM other_table /* accounts_user */",
            "VALUES (1)",
            "WITH u AS (SELECT * FROM accounts_user) SELECT * FROM u",
        ):
            driver = Mock(return_value="ok")
            self.assertEqual(
                ns["guard_execute"](driver, text, [], False, {"connection": conn}), "ok"
            )
            driver.assert_called_once()

    def test_gateway_class_census_ignores_prism_app_label(self):
        policy = runpy.run_path(str(SERVICES / "hard_erase_sql_policy.py"))
        gateway = TESTS.parents[1] / "agentcc/models"
        names = {
            "AgentccAPIKey",
            "AgentccMCPCredential",
            "AgentccEEServiceCredential",
            "AgentccEEAgentKey",
            "AgentccProviderCredential",
            "AgentccManagedUpstreamKey",
            "AgentccDelegatedGrant",
            "AgentccMCPOAuthClient",
            "AgentccMCPOAuthConsent",
            "AgentccMCPOAuthRefreshToken",
            "AgentccMCPOAuthSigningKey",
            "AgentccMCPPersonalKey",
            "AgentccMCPConnectState",
            "AgentccMCPUpstreamClient",
        }
        models = []
        for path in gateway.glob("*.py"):
            for node in ast.parse(path.read_text()).body:
                if not isinstance(node, ast.ClassDef) or node.name not in names:
                    continue
                table = next(
                    child.value.value
                    for child in ast.walk(node)
                    if isinstance(child, ast.Assign)
                    and any(
                        isinstance(t, ast.Name) and t.id == "db_table"
                        for t in child.targets
                    )
                )
                models.append(
                    type(
                        node.name,
                        (),
                        {
                            "__module__": "agentcc.models." + path.stem,
                            "_meta": SimpleNamespace(
                                label_lower="prism." + node.name.lower(),
                                db_table=table,
                                auto_created=False,
                            ),
                        },
                    )
                )
        self.assertEqual({m.__name__ for m in models}, names)
        selected = policy["protected_models"](models)
        self.assertEqual(
            {m._meta.db_table for m in selected}, {m._meta.db_table for m in models}
        )

    def test_free_form_log_labels_are_not_person_keys_even_during_erase(self):
        ns = KEYS["namespace"]()
        life, _, _ = LIFE["LifecycleTests"]().namespace()
        ns["identity_keys"] = life["identity_keys"]
        org = UUID(int=1)
        ns["_ownership_graph"] = lambda *args: SimpleNamespace(scopes=lambda row: {org})
        for model in ("agentccrequestlog", "agentcctoolcall"):
            for label in ("", "end-user-42", str(UUID(int=2))):
                row = SimpleNamespace(
                    pk=UUID(int=3),
                    user_id=label,
                    _meta=SimpleNamespace(
                        label_lower="prism." + model,
                        concrete_fields=[KEYS["field"]("user_id")],
                    ),
                )
                self.assertEqual(
                    ns["row_keys"]([row], "default"), {("organization", str(org))}
                )

    def test_no_global_orm_lifecycle_wrappers_or_join_snapshot(self):
        tree = ast.parse((SERVICES / "hard_erase_sql.py").read_text())
        self.assertNotIn(
            "_write_wrapper",
            {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)},
        )
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    self.assertNotIn("Compiler.execute_sql", ast.unparse(target))
        self.assertNotIn(
            "write_snapshot", (SERVICES / "hard_erase_keys.py").read_text()
        )


if __name__ == "__main__":
    unittest.main()
