"""DB-free, whole-source lifecycle tripwire. Run this file directly with Python.

Exceptions are function-level, not modules: adding a tenant route beside a
platform login/identity flow must not inherit its exemption. Test fixtures and
migrations are not served code. Settings/secret files are never opened.
"""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Explicit platform identity lifecycle; never tenant administrator operations.
ALLOWLIST = {
    ("accounts/signals/membership_signals.py", "revoke_deactivated_person_keys"),
    ("accounts/signals/membership_signals.py", "revoke_deleted_person_keys"),
    ("accounts/views/signup.py", "activate_account"),
    ("accounts/views/signup.py", "reset_password_confirm"),
    ("accounts/views/signup.py", "_accept_invitation_mail"),
    ("accounts/utils.py", "first_signup"),
    ("accounts/management/commands/reset_password.py", "Command.handle"),
    ("model_hub/management/commands/gt_roundtrip_test.py", "Command._teardown"),
}


def source_files():
    for path in sorted(ROOT.rglob("*.py")):
        parts = path.relative_to(ROOT).parts
        if "tests" in parts or "migrations" in parts or path.name.startswith("test"):
            continue
        # A virtualenv or tool cache inside the tree is not served code.
        if any(part.startswith(".") or part == "site-packages" for part in parts[:-1]):
            continue
        if any(
            word in str(path.relative_to(ROOT)).lower()
            for word in (
                "settings",
                "secret",
                "values",
                ".env",
                "__pycache__",
            )
        ):
            continue
        yield path


def violations(source, filename):
    """Track common User aliases and queryset aliases, not membership variables."""
    tree = ast.parse(source)
    found = []
    user_types = {"User"}
    revoke_names = {"revoke_personal_keys"}
    clear_names = {"clear_user_redis_cache"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "User":
                    user_types.add(alias.asname or alias.name)
                if alias.name == "revoke_personal_keys":
                    revoke_names.add(alias.asname or alias.name)
                if alias.name == "clear_user_redis_cache":
                    clear_names.add(alias.asname or alias.name)

    def visit_scope(node, scope=""):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope = f"{scope}.{node.name}".strip(".")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            users = {"user", "target_user", "new_user", "member", "owner"}
            if any(
                "accounts.User" in ast.unparse(decorator)
                for decorator in node.decorator_list
            ):
                users.add("instance")
            user_chains = set(user_types)
            # Propagate aliases from model manager queries, including User aliases.
            for _ in range(3):
                for child in ast.walk(node):
                    if not isinstance(child, (ast.Assign, ast.AnnAssign)):
                        continue
                    value = child.value
                    if value is None:
                        continue
                    text = ast.unparse(value)
                    if (
                        any(text.startswith(f"{name}.") for name in user_chains)
                        or text in users
                    ):
                        targets = (
                            child.targets
                            if isinstance(child, ast.Assign)
                            else [child.target]
                        )
                        aliases = {t.id for t in targets if isinstance(t, ast.Name)}
                        users.update(aliases)
                        if any(text.startswith(f"{name}.") for name in user_chains):
                            user_chains.update(aliases)
            for child in ast.walk(node):
                reason = None
                if isinstance(child, (ast.Assign, ast.AnnAssign)):
                    targets = (
                        child.targets
                        if isinstance(child, ast.Assign)
                        else [child.target]
                    )
                    for target in targets:
                        if (
                            isinstance(target, ast.Attribute)
                            and target.attr == "is_active"
                            and ast.unparse(target.value) in users
                        ):
                            reason = "global User activity write"
                if isinstance(child, ast.Call):
                    called = ast.unparse(child.func)
                    name = called.rsplit(".", 1)[-1]
                    receiver = called.rsplit(".", 1)[0]
                    user_receiver = receiver in users or any(
                        receiver.startswith(f"{model}.") for model in user_chains
                    )
                    if name == "delete" and user_receiver:
                        reason = "global User deletion"
                    if (
                        name == "update"
                        and user_receiver
                        and any(kw.arg == "is_active" for kw in child.keywords)
                    ):
                        reason = "global User activity update"
                    if name in clear_names:
                        reason = "global session invalidation"
                    if name in revoke_names:
                        scoped = (
                            len(child.args) > 1
                            and not (
                                isinstance(child.args[1], ast.Constant)
                                and child.args[1].value is None
                            )
                        ) or any(
                            kw.arg in {"organization_id", "organization"}
                            and not (
                                isinstance(kw.value, ast.Constant)
                                and kw.value.value is None
                            )
                            for kw in child.keywords
                        )
                        if not scoped:
                            reason = "unscoped personal-key revocation"
                if reason and (filename, scope) not in ALLOWLIST:
                    found.append(f"{filename}:{child.lineno} {scope}: {reason}")
            return
        for child in ast.iter_child_nodes(node):
            visit_scope(child, scope)

    visit_scope(tree)
    return found


class TenantLifecycleGuard(unittest.TestCase):
    def test_all_served_modules(self):
        failures = []
        for path in source_files():
            failures.extend(
                violations(path.read_text(encoding="utf-8-sig"), path.relative_to(ROOT).as_posix())
            )
        self.assertEqual(failures, [], "\n".join(failures))

    def test_guard_detects_aliases_and_bulk_writes(self):
        source = """
from accounts.models.user import User as Person
from accounts.services.personal_key_lifecycle import revoke_personal_keys as revoke

def tenant_action():
    person = Person.objects.get(pk=1)
    person.is_active = False
    person.delete()
    Person.objects.filter(pk=1).update(is_active=False)
    clear_user_redis_cache(1)
    revoke(1)
"""
        self.assertEqual(len(violations(source, "new_tenant_route.py")), 5)


if __name__ == "__main__":
    unittest.main()
