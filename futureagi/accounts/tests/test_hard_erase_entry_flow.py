"""Offline entry ordering + independent-thread connection/lock schedule doubles.

Executes the actual key planners and lifecycle implementation for every discovered
entry; AST dominance checks the real callers' first writes. This is protocol and
control-flow evidence, NOT PostgreSQL persistence/deadlock qualification.
"""

import ast
import runpy
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

TESTS = Path(__file__).resolve().parent
GRAPH = runpy.run_path(str(TESTS / "hard_erase_callgraph.py"))
LIFE = runpy.run_path(str(TESTS / "test_hard_erase_lifecycle_flow.py"))
ENTRY = runpy.run_path(str(TESTS / "hard_erase_entry_support.py"))


# Independently specified complete person sets for the inputs in environment().
EXPECTED_PEOPLE = {
    "accounts.gcp_marketplace_utils.process_signup": (7,),
    "accounts.models.organization_invite.OrganizationInvite.accept": (3, 4),
    "accounts.services.default_workspace.provision_default_workspace": (3,),
    "accounts.services.identity_issuance.issue_membership": (3, 4),
    "accounts.services.identity_issuance.save_new_person": (3, 4),
    "accounts.services.member_role_service.update_member_role": (2, 6),
    "accounts.services.workspace_membership.create_workspace_membership": (3, 4, 5),
    "accounts.utils.first_signup": (7,),
    "accounts.views.appsmith.UserApiView.post": (7,),
    "accounts.views.organization_selection.SwitchOrganizationView._resolve_workspace_for_org": (
        3,
    ),
    "accounts.views.organization_views.CreateAdditionalOrganizationView.post": (3,),
    "accounts.views.organization_views.OrganizationCreateAPIView.post": (3,),
    "accounts.views.signup._accept_invitation_mail": (3, 4, 5),
    "accounts.views.signup.activate_account": (3,),
    "model_hub.management.commands.gt_roundtrip_test.Command._build_fixture": (9,),
    "omega_hub.management.commands.omega_hub_seed_dev.Command.handle": (7, 8),
    "omega_hub.management.commands.omega_hub_seed_dev.ensure_member": (7,),
}


class SchedulingDatabase:
    """Distinct connection per thread, shared advisory and row lock resources."""

    def __init__(self):
        self.local = threading.local()
        self.resources = {}
        self.mutex = threading.Lock()
        self.waiting = threading.Event()

    def __getitem__(self, alias):
        if not hasattr(self.local, "connection"):
            self.local.connection = SimpleNamespace(
                vendor="postgresql", in_atomic_block=False, held=[], cursor=self.cursor
            )
        return self.local.connection

    @contextmanager
    def atomic(self, **kwargs):
        connection = self["default"]
        assert not connection.in_atomic_block
        connection.in_atomic_block = True
        try:
            yield
        finally:
            for lock in reversed(connection.held):
                lock.release()
            connection.held.clear()
            connection.in_atomic_block = False

    @contextmanager
    def cursor(self):
        def execute(sql, params=None):
            if params is None:
                return
            with self.mutex:
                lock = self.resources.setdefault(params[0], threading.Lock())
            if not lock.acquire(blocking=False):
                self.waiting.set()
                if not lock.acquire(timeout=3):
                    raise AssertionError("modeled advisory/row lock deadlock")
            self["default"].held.append(lock)

        yield SimpleNamespace(execute=execute, fetchone=lambda: ("read committed",))


def environment(life):
    ids = [UUID(int=n) for n in range(1, 11)]
    org, actor, user, inviter, grantor, target, person_a, person_b, reserved, _ = ids
    existing = {"a@example.test": person_a, "b@example.test": person_b}
    live = set(ids)
    entry = ENTRY["entry_namespace"](life, existing=existing, live=live)
    entry["uuid4"] = lambda: reserved
    data = {
        "email": "a@example.test",
        "emails": ["a@example.test", "b@example.test"],
        "users": [{"email": "a@example.test"}],
        "members": [{"email": "b@example.test"}],
        "user_id": target,
    }
    request = SimpleNamespace(
        user=SimpleNamespace(pk=actor),
        organization=SimpleNamespace(pk=org),
        validated_data=data,
    )
    params = SimpleNamespace(emails=data["emails"], user_id=target)
    context = SimpleNamespace(user=request.user, organization=request.organization)
    ns = {
        "issuance_entry": entry["issuance_entry"],
        "lifecycle_lock": life["lifecycle_lock"],
        "identity_keys": life["identity_keys"],
        "organizations": [org],
        "people": [user],
        "organization": SimpleNamespace(pk=org),
        "org": SimpleNamespace(pk=org),
        "user": SimpleNamespace(pk=user),
        "actor": SimpleNamespace(pk=actor),
        "target_user_id": target,
        "invited_by": SimpleNamespace(pk=inviter),
        "granted_by": SimpleNamespace(pk=grantor),
        "workspace": SimpleNamespace(organization_id=org),
        "gcp_account": SimpleNamespace(organization_id=org),
        "snapshot": SimpleNamespace(organization_id=org),
        "self": SimpleNamespace(organization_id=org, invited_by_id=inviter),
        "uid": user,
        "inviters": [inviter, grantor],
        "email": "a@example.test",
        "email_a": "a@example.test",
        "email_b": "b@example.test",
        "data": data,
        "new_org": False,
        "suffix": "fixture",
        "using": "default",
        "keys": life["identity_keys"](organizations=[org], people=[user, inviter]),
    }
    return entry, ns, request, params, context, live


class EntryPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.functions, cls.edges, cls.reached = GRAPH["graph"]()

    def test_transitive_call_graph_has_no_unguarded_prewrite(self):
        self.assertGreaterEqual(len(self.reached), 55)
        self.assertEqual(
            GRAPH["violations"](self.functions, self.edges, self.reached), []
        )
        support = runpy.run_path(str(TESTS / "hard_erase_dispatch_support.py"))
        support["check_edges"](self, GRAPH)

    def test_new_caller_and_new_prewrite_fail_completeness(self):
        functions, edges = dict(self.functions), dict(self.edges)
        name = "new_app.service.issue_batch"
        function = ast.parse(
            "def issue_batch():\n    row.save()\n    issue_membership()\n"
        ).body[0]
        call = function.body[1].value
        functions[name] = ("new_app/service.py", function, "new_app.service", "")
        edges[name] = [(call, "accounts.services.identity_issuance.issue_membership")]
        self.assertTrue(GRAPH["violations"](functions, edges, self.reached | {name}))
        key = "accounts.services.default_workspace.provision_default_workspace"
        original = functions[key]
        copied = ast.parse(ast.unparse(original[1])).body[0]
        copied.body.insert(0, ast.parse("row.save()").body[0])
        functions[key] = (original[0], copied, original[2], original[3])
        self.assertTrue(GRAPH["violations"](functions, edges, self.reached))
        # Resolving the Falcon closure must not hide a prewrite in its original
        # issuance-capable dispatcher. Keep checking that method's real edges.
        functions, edges, reached = dict(self.functions), dict(self.edges), set(self.reached)
        key = GRAPH["DISPATCH"]["ORIGINAL"]
        present = runpy.run_path(str(TESTS / "hard_erase_source_files.py"))["ee_available"](GRAPH["ROOT"])
        if present:
            self.assertIn(key, functions)
            self.assertIn(key, reached)
        else:
            self.assertNotIn(key, functions)
            self.assertNotIn(key, reached)
            # Test-only surrogate of agent.py:1036's real tool.run dispatch.
            # The actual OSS graph must remain EE-free; only this mutation
            # control receives the surrogate, with the real BaseTool.run edge.
            key = "census_fixture.original_dispatch"
            function = ast.parse("def original_dispatch():\n    tool.run(params, context)\n").body[0]
            functions[key] = ("census_fixture.py", function, "census_fixture", "")
            target = "ai_tools.base.BaseTool.run"
            self.assertIn(target, reached)
            edges[key] = [(function.body[0].value, target)]
            reached.add(key)
        self.assertFalse(any(name == key for name, _ in GRAPH["violations"](functions, edges, reached)))
        original = functions[key]
        copied = ast.parse(ast.unparse(original[1])).body[0]
        copied.body.insert(0, ast.parse("row.save()").body[0])
        functions[key] = (original[0], copied, original[2], original[3])
        # Rebind call locations to the mutated AST, so the inserted write really
        # precedes delegated issuance rather than tying an old source line.
        calls = [n for n in ast.walk(copied) if isinstance(n, ast.Call)]
        edges[key] = [
            (next(n for n in calls if ast.dump(n, include_attributes=False) == ast.dump(node, include_attributes=False)), target)
            for node, target in edges[key]
        ]
        self.assertTrue(
            any(
                failed == key
                for failed, _ in GRAPH["violations"](functions, edges, reached)
            )
        )

    def plans(self, life):
        entry, ns, request, params, context, live = environment(life)
        for key in sorted(self.reached):
            function = self.functions[key][1]
            if GRAPH["decorated"](function):
                decorator = next(
                    ast.unparse(n)
                    for n in function.decorator_list
                    if ast.unparse(n) in GRAPH["DECORATORS"]
                )

                # Avoid acquiring/releasing a discovery transaction in the racing
                # thread: collect once, then use the same real planner directly.
                plans = []

                def capture(*args, plans=plans):
                    plans.append(life["_ACTIVE"].get()[1])

                people, emails = [UUID(int=2)], []
                if "CreateWorkspaceTool" in key:
                    params = SimpleNamespace()
                    expected_people = (2,)
                elif "AddWorkspaceMemberTool" in key:
                    params = SimpleNamespace(user_id=UUID(int=6))
                    people.append(UUID(int=6))
                    expected_people = (2, 6)
                elif "InviteUsersTool" in key:
                    emails = ["a@example.test", "b@example.test"]
                    params = SimpleNamespace(emails=emails)
                    expected_people = (2, 7, 8)
                elif "UserRoleUpdateAPIView" in key:
                    request.validated_data = {"user_id": UUID(int=6)}
                    people.append(UUID(int=6))
                    expected_people = (2, 6)
                elif "ManageTeamView" in key:
                    emails = ["b@example.test"]
                    request.validated_data = {"members": [{"email": emails[0]}]}
                    expected_people = (2, 8)
                elif "WorkspaceMembershipView" in key:
                    emails = ["a@example.test"]
                    request.validated_data = {"users": [{"email": emails[0]}]}
                    expected_people = (2, 7)
                else:
                    emails = ["a@example.test", "b@example.test"]
                    request.validated_data = {"emails": emails}
                    expected_people = (2, 7, 8)
                if decorator == "tool_issuance_entry":
                    entry[decorator](capture)(None, params, context)
                else:
                    entry[decorator](capture)(None, request)
                expected = life["identity_keys"](
                    organizations=[UUID(int=1)],
                    people=[UUID(int=n) for n in expected_people],
                )
                self.assertEqual(plans[0], expected, key)
                yield (
                    key,
                    lambda people=people, emails=emails: entry["issuance_entry"](
                        organizations=[UUID(int=1)],
                        people=people,
                        emails=emails,
                    ),
                    expected,
                )
            for scope in GRAPH["entry_scopes"](function):
                expression = ast.Expression(scope.items[0].context_expr)
                code = compile(ast.fix_missing_locations(expression), key, "eval")

                def factory(code=code):
                    return eval(code, ns)

                with factory():
                    keys = life["_ACTIVE"].get()[1]
                    self.assertEqual(
                        keys,
                        life["identity_keys"](
                            organizations=[UUID(int=1)],
                            people=[UUID(int=n) for n in EXPECTED_PEOPLE[key]],
                        ),
                        key,
                    )
                yield key, factory, keys

    def test_every_entry_serializes_erase_before_first_row_write(self):
        life, _, _ = LIFE["LifecycleTests"]().namespace()
        db = SchedulingDatabase()
        life["connections"] = db
        life["transaction"] = SimpleNamespace(atomic=db.atomic)
        count = 0
        for key, factory, keys in self.plans(life):
            count += 1
            with self.subTest(entry=key):
                for erase_first in (False, True):
                    db.waiting.clear()
                    entered, release = threading.Event(), threading.Event()
                    writes, errors = [], []
                    row_lock = threading.Lock()

                    def operation(
                        erase,
                        keys=keys,
                        factory=factory,
                        row_lock=row_lock,
                        writes=writes,
                        erase_first=erase_first,
                        entered=entered,
                        release=release,
                        errors=errors,
                    ):
                        try:
                            scope = (
                                life["lifecycle_lock"](keys=keys)
                                if erase
                                else factory()
                            )
                            with scope:
                                life["require_held"]("default", keys)
                                # Erase and issuance both take advisory before row.
                                with row_lock:
                                    writes.append("erase" if erase else "issue")
                                    if erase == erase_first:
                                        entered.set()
                                        if not release.wait(3):
                                            raise AssertionError(
                                                "schedule barrier timed out"
                                            )
                        except BaseException as error:
                            errors.append(error)

                    first = threading.Thread(target=operation, args=(erase_first,))
                    second = threading.Thread(target=operation, args=(not erase_first,))
                    first.start()
                    self.assertTrue(entered.wait(3), key)
                    second.start()
                    try:
                        self.assertTrue(db.waiting.wait(3), key)
                        self.assertEqual(len(writes), 1, key)
                    finally:
                        release.set()
                        first.join(4)
                        second.join(4)
                    self.assertFalse(first.is_alive() or second.is_alive(), key)
                    self.assertEqual(errors, [], str(errors))
                    self.assertEqual(
                        writes,
                        ["erase", "issue"] if erase_first else ["issue", "erase"],
                    )
        self.assertEqual(count, 26)

    def test_erased_existing_identity_refuses_before_entry_continuation(self):
        life, _, _ = LIFE["LifecycleTests"]().namespace()
        entry, _, request, params, context, live = environment(life)
        live.remove(UUID(int=7))
        writes = []
        for name, args in (
            ("request_issuance_entry", (None, request)),
            ("tool_issuance_entry", (None, params, context)),
        ):
            with self.subTest(entry=name), self.assertRaises(LIFE["Refused"]):
                entry[name](lambda *args: writes.append("write"))(*args)
        self.assertEqual(writes, [])

    def test_batch_reservations_nested_subset_and_changed_email_recheck(self):
        life, _, _ = LIFE["LifecycleTests"]().namespace()
        entry, _, _, _, _, _ = environment(life)
        with entry["issuance_entry"](
            organizations=[UUID(int=1)], emails=["new@example.test"]
        ):
            person = SimpleNamespace(email="NEW@example.test", pk=UUID(int=99))
            entry["planned_person"](person)
            self.assertEqual(person.pk, UUID(int=9))
            with entry["issuance_entry"](
                organizations=[UUID(int=1)], emails=["new@example.test"]
            ):
                self.assertEqual(entry["_PLANNED"].get()["new@example.test"], person.pk)
            with self.assertRaises(LIFE["Refused"]):
                entry["planned_person"](SimpleNamespace(email="unplanned@example.test"))
        self.assertIsNone(entry["_PLANNED"].get())
        reads = iter([{}, {"new@example.test": UUID(int=10)}])
        entry["_email_people"] = lambda *args: next(reads)
        with self.assertRaises(LIFE["Refused"]):
            with entry["issuance_entry"](emails=["new@example.test"]):
                self.fail("changed identity entered a writer")


if __name__ == "__main__":
    unittest.main()
