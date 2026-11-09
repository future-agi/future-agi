"""619f DB-free regressions executing the complete production resend handler.

ORM, transport and framework boundaries are inert; this is not a Django dispatch,
SQL/concurrency or rendered-email test. The production expiry method also runs.
Standalone runner (under the local slot supervisor):
    python3 -I <this-file> --red-green
This runs the pinned pre-fix handler first, then the working-tree handler, with
sockets denied throughout. No application/settings imports or database needed.
"""

import ast
import base64
import socket
import subprocess
import sys
import unittest
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace as N
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
VIEW = ROOT / "accounts/views/workspace_management.py"
BASELINE = "7e7287a794e900466c2484bf97e3f8ac12595bc2"
VIEW_SOURCE = None


def production_function(path, name, namespace, *, owner=None, source=None):
    tree = ast.parse(source if source is not None else path.read_text())
    nodes = tree.body
    if owner:
        nodes = next(
            node.body
            for node in nodes
            if isinstance(node, ast.ClassDef) and node.name == owner
        )
    node = next(
        node
        for node in nodes
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    node.decorator_list = []  # Boundary validation is not under test here.
    module = ast.Module(body=[node], type_ignores=[])
    exec(compile(module, str(path), "exec"), namespace)
    return namespace[name]


def deny_network(*args, **kwargs):
    raise AssertionError("Network access denied during containment tests")


class InviteQuery:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, **predicates):
        def matches(row):
            for key, value in predicates.items():
                if key.endswith("__iexact"):
                    if (
                        getattr(row, key.removesuffix("__iexact")).casefold()
                        != value.casefold()
                    ):
                        return False
                elif getattr(row, key) != value:
                    return False
            return True

        return InviteQuery([row for row in self.rows if matches(row)])

    def first(self):
        return next(iter(self.rows), None)

    def exists(self):
        return bool(self.rows)


class ResendInviteContainmentTests(unittest.TestCase):
    def setUp(self):
        for name in ("socket", "create_connection", "getaddrinfo"):
            guard = patch.object(socket, name, deny_network)
            guard.start()
            self.addCleanup(guard.stop)
        self.now = datetime(2026, 11, 3, tzinfo=UTC)
        self.org = N(pk="acting-org", name="Acting org", display_name="Acting display")
        self.other_org = N(pk="other-org", name="Other org")
        self.actor = N(pk="actor")
        self.target = N(
            pk="target",
            email="invitee@example.invalid",
            organization=self.org,
            password="synthetic-existing-password-hash",
            is_active=False,
        )
        self.target.set_password = Mock(
            side_effect=lambda value: setattr(
                self.target, "password", "changed:" + value
            )
        )
        self.target.save = Mock()
        self.foreign_membership = N(
            user=self.target, organization=self.other_org, is_active=True
        )
        self.workspace = N(name="Acting workspace", organization=self.org)
        self.rows = []
        self.invite_writes = []
        self.refresh = production_function(
            ROOT / "accounts/models/organization_invite.py",
            "refresh_expiration",
            {"timezone": N(now=lambda: self.now)},
            owner="OrganizationInvite",
        )
        self.pending = self.offer(self.org)
        self.foreign = self.offer(self.other_org)
        self.unrelated = self.offer(self.org, email="someone-else@example.invalid")
        self.invite_model = N(objects=InviteQuery(self.rows))
        invite_module = ModuleType("accounts.models.organization_invite")
        invite_module.OrganizationInvite = self.invite_model
        invite_module.InviteStatus = N(PENDING="Pending")
        modules = {
            "accounts": ModuleType("accounts"),
            "accounts.models": ModuleType("accounts.models"),
            "accounts.models.organization_invite": invite_module,
        }
        guard = patch.dict(sys.modules, modules)
        guard.start()
        self.addCleanup(guard.stop)
        self.user_model = N(
            objects=N(get=Mock(return_value=self.target)),
            DoesNotExist=type("UserMissing", (Exception,), {}),
        )
        self.role = Mock(return_value="Owner")
        self.email = Mock()
        self.token = Mock(return_value="synthetic-invitation-token")
        self.password_generator = Mock(return_value="synthetic-reset-password")
        self.ns = {
            "User": self.user_model,
            "OrganizationRoles": N(OWNER="Owner", ADMIN="Admin"),
            "OrganizationInvite": self.invite_model,
            "InviteStatus": invite_module.InviteStatus,
            "resolve_org": Mock(return_value=self.org),
            "resolve_org_role": self.role,
            "get_error_message": lambda code: code,
            "get_current_workspace": Mock(return_value=self.workspace),
            "generate_password": self.password_generator,
            "default_token_generator": N(make_token=self.token),
            "urlsafe_base64_encode": lambda data: (
                base64.urlsafe_b64encode(data).decode().rstrip("=")
            ),
            "force_bytes": lambda value: str(value).encode(),
            "settings": N(APP_URL="https://app.example.invalid"),
            "ssl": True,
            "email_helper": self.email,
            "logger": Mock(),
        }
        # Preserve the old helper's real behavior for the RED baseline too.
        self.ns["refresh_pending_invite_expiration"] = production_function(
            ROOT / "accounts/utils.py",
            "refresh_pending_invite_expiration",
            self.ns,
        )
        self.post = production_function(
            VIEW,
            "post",
            self.ns,
            owner="ResendInviteAPIView",
            source=VIEW_SOURCE,
        )
        self.view = N(
            _gm=N(
                success_response=lambda data: N(status_code=200, data=data),
                bad_request=lambda data: N(status_code=400, data=data),
                forbidden_response=lambda data: N(status_code=403, data=data),
                not_found=lambda data: N(status_code=404, data=data),
            )
        )

    def offer(self, organization, *, email="INVITEE@example.invalid"):
        row = N(
            organization=organization,
            target_email=email,
            status="Pending",
            created_at=self.now - timedelta(days=30),
        )
        row.save = Mock(
            side_effect=lambda **kwargs: self.invite_writes.append((row, kwargs))
        )
        row.refresh_expiration = lambda: self.refresh(row)
        self.rows.append(row)
        return row

    def resend(self):
        return self.post(
            self.view, N(user=self.actor, validated_data={"user_id": self.target.pk})
        )

    def assert_identity_untouched(self):
        self.assertEqual(self.target.password, "synthetic-existing-password-hash")
        self.target.set_password.assert_not_called()
        self.target.save.assert_not_called()
        self.password_generator.assert_not_called()
        self.assertTrue(self.foreign_membership.is_active)
        self.foreign.save.assert_not_called()
        self.unrelated.save.assert_not_called()

    def assert_refused(self):
        before = [(row.status, row.created_at) for row in self.rows]
        response = self.resend()
        self.assertIn(response.status_code, (400, 404), response.data)
        self.assert_identity_untouched()
        self.assertEqual(self.invite_writes, [])
        self.assertEqual([(row.status, row.created_at) for row in self.rows], before)
        self.email.assert_not_called()
        self.token.assert_not_called()

    def test_active_shared_identity_pending_invite_never_resets_credentials(self):
        self.target.is_active = True
        response = self.resend()
        self.assertEqual(response.status_code, 200)
        self.assert_identity_untouched()
        self.assertTrue(self.target.is_active)
        self.email.assert_called_once()
        self.pending.save.assert_called_once_with(update_fields=["created_at"])

    def test_active_shared_identity_without_pending_invite_is_refused(self):
        self.target.is_active = True
        self.rows.remove(self.pending)
        self.assert_refused()

    def test_inactive_invitee_resends_link_and_refreshes_only_own_expiry(self):
        response = self.resend()
        self.assertEqual(response.status_code, 200, response.data)
        self.assert_identity_untouched()
        self.assertFalse(self.target.is_active)
        self.assertEqual(self.pending.created_at, self.now)
        self.assertEqual(self.pending.status, "Pending")
        self.assertEqual(
            self.invite_writes, [(self.pending, {"update_fields": ["created_at"]})]
        )
        self.user_model.objects.get.assert_called_once_with(
            id=self.target.pk, organization=self.org
        )
        self.token.assert_called_once_with(self.target)
        self.email.assert_called_once()
        subject, template, context, recipients = self.email.call_args.args
        self.assertEqual(template, "member_invite.html")
        self.assertEqual(recipients, [self.target.email])
        self.assertEqual(
            subject,
            "You are invited to join Acting display - Acting workspace - Future AGI",
        )
        self.assertEqual(
            context,
            {
                "email": self.target.email,
                "org_name": "Acting display",
                "app_url": "https://app.example.invalid",
                "ssl": True,
                "uid": "dGFyZ2V0",
                "token": "synthetic-invitation-token",
                "workspace_name": "Acting workspace",
                "subject": subject,
            },
        )
        self.assertEqual(response.data["workspace"], "Acting workspace")

    def test_other_org_pending_invite_cannot_authorize_resend(self):
        self.rows.remove(self.pending)
        self.assert_refused()

    def test_accepted_invite_cannot_authorize_resend(self):
        self.pending.status = "Accepted"
        self.assert_refused()

    def test_cancelled_invite_cannot_authorize_resend(self):
        self.pending.status = "Cancelled"
        self.assert_refused()

    def test_explicitly_expired_status_cannot_authorize_resend(self):
        self.pending.status = "Expired"
        self.assert_refused()

    def test_admin_can_resend_pending_invite_without_credential_write(self):
        self.role.return_value = "Admin"
        self.assertEqual(self.resend().status_code, 200)
        self.assert_identity_untouched()
        self.email.assert_called_once()

    def test_non_admin_remains_forbidden_without_writes(self):
        self.role.return_value = "Member"
        self.assertEqual(self.resend().status_code, 403)
        self.assert_identity_untouched()
        self.assertEqual(self.invite_writes, [])
        self.user_model.objects.get.assert_not_called()
        self.email.assert_not_called()

    def test_foreign_workspace_is_ignored_as_before(self):
        self.workspace.organization = self.other_org
        response = self.resend()
        self.assertEqual(response.status_code, 200)
        self.assert_identity_untouched()
        self.assertNotIn("workspace", response.data)
        context = self.email.call_args.args[2]
        self.assertNotIn("workspace_name", context)
        self.assertEqual(
            context["subject"], "You are invited to join Acting display - Future AGI"
        )

    def test_email_failure_still_never_writes_shared_identity(self):
        self.email.side_effect = RuntimeError("synthetic transport failure")
        self.assertEqual(self.resend().status_code, 400)
        self.assert_identity_untouched()
        self.assertEqual(self.pending.created_at, self.now)


if __name__ == "__main__":
    with ExitStack() as stack:
        for name in ("socket", "create_connection", "getaddrinfo"):
            stack.enter_context(patch.object(socket, name, deny_network))
        if sys.argv[1:] == ["--red-green"]:
            VIEW_SOURCE = subprocess.check_output(
                [
                    "git",
                    "-C",
                    str(ROOT.parent),
                    "show",
                    f"{BASELINE}:futureagi/accounts/views/workspace_management.py",
                ],
                text=True,
            )
            print(f"RED baseline {BASELINE}", flush=True)
            red = unittest.TextTestRunner(verbosity=2).run(
                unittest.defaultTestLoader.loadTestsFromTestCase(
                    ResendInviteContainmentTests
                )
            )
            expected_failures = {
                "test_active_shared_identity_pending_invite_never_resets_credentials",
                "test_inactive_invitee_resends_link_and_refreshes_only_own_expiry",
                "test_other_org_pending_invite_cannot_authorize_resend",
            }
            failed_names = {case._testMethodName for case, _ in red.failures}
            if not expected_failures <= failed_names or red.errors or red.skipped:
                raise SystemExit(
                    "Expected all three containment baseline failures, no errors/skips"
                )
            VIEW_SOURCE = None
            print(f"GREEN candidate {VIEW}", flush=True)
            green = unittest.TextTestRunner(verbosity=2).run(
                unittest.defaultTestLoader.loadTestsFromTestCase(
                    ResendInviteContainmentTests
                )
            )
            print(
                f"RESULT red_tests={red.testsRun} red_failures={len(red.failures)} red_errors={len(red.errors)} green_tests={green.testsRun} green_failures={len(green.failures)} green_errors={len(green.errors)} green_skips={len(green.skipped)}",
                flush=True,
            )
            raise SystemExit(0 if green.wasSuccessful() and not green.skipped else 1)
        unittest.main()
