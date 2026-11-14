"""Offline default-workspace regression tests; no Django bootstrap or sockets.

Execute the unchanged auth class, decoder and permission helper definitions
from authentication.py. Imports/model/cache/crypto boundaries are test doubles;
this proves resolver control flow, not HTTP dispatch or database permissions.
Run directly with Python/unittest, not the backend pytest bootstrap.
"""

import ast
import re
import unittest
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

SOURCE = Path(__file__).resolve().parents[2] / "futureagi/accounts/authentication.py"


class PermissionDenied(Exception):
    status_code = 403


class AuthenticationFailed(Exception):
    status_code = 401


class DoesNotExist(Exception):
    pass


class DatabaseError(Exception):
    pass


class InterfaceError(Exception):
    pass


def load_auth_definitions():
    """Compile real source bodies without importing application startup code."""
    names = {
        "APIKeyAuthentication",
        "decode_token",
        "_resolve_view_class",
        "_is_workspace_write_exempt_view",
        "_is_annotation_queue_role_scoped_write_path",
    }
    tree = ast.parse(SOURCE.read_text(), filename=str(SOURCE))
    definitions = [node for node in tree.body if getattr(node, "name", None) in names]
    assert {node.name for node in definitions} == names
    patterns = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            getattr(target, "id", None) == "ANNOTATION_QUEUE_ROLE_SCOPED_WRITE_PATHS"
            for target in node.targets
        )
    )
    namespace = {
        "BaseAuthentication": object,
        "PermissionDenied": PermissionDenied,
        "AuthenticationFailed": AuthenticationFailed,
        "DatabaseError": DatabaseError,
        "InterfaceError": InterfaceError,
        "logger": MagicMock(),
        "structlog": MagicMock(),
        "re": re,
        "traceback": MagicMock(),
        "AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES": 60,
        "timezone": SimpleNamespace(now=lambda: datetime(2026, 9, 9, tzinfo=UTC)),
    }
    exec(
        compile(
            ast.Module(body=[patterns, *definitions], type_ignores=[]),
            str(SOURCE),
            "exec",
        ),
        namespace,
    )
    return namespace


class WorkspaceDefaultPreferencesTests(unittest.TestCase):
    def setUp(self):
        self.ns = load_auth_definitions()
        self.org = SimpleNamespace(id="org-a")
        self.foreign_org = SimpleNamespace(id="org-b")
        self.a1 = SimpleNamespace(id="a1", organization=self.org, is_active=True)
        self.a2 = SimpleNamespace(id="a2", organization=self.org, is_active=True)
        self.foreign = SimpleNamespace(
            id="b1", organization=self.foreign_org, is_active=True
        )
        self.inactive = SimpleNamespace(
            id="inactive", organization=self.org, is_active=False
        )
        self.denied = SimpleNamespace(
            id="denied", organization=self.org, is_active=True
        )
        self.allowed = {"a1", "a2"}
        self.user = SimpleNamespace(
            id="owner",
            pk="owner",
            email="synthetic@example.invalid",
            is_active=True,
            organization=self.org,
            organization_id=self.org.id,
            config={},
            _state=SimpleNamespace(fields_cache={"organization": self.org}),
            can_access_organization=MagicMock(
                side_effect=lambda org: org.id == self.org.id
            ),
            can_access_workspace=MagicMock(
                side_effect=lambda ws: ws.id in self.allowed
            ),
            can_write_to_workspace=MagicMock(return_value=True),
        )
        self.persisted_config = {}
        self.users = MagicMock()
        self.users.filter.return_value.values_list.return_value.first.side_effect = (
            lambda: deepcopy(self.persisted_config)
        )
        self.workspaces = MagicMock()

        def get_workspace(**lookup):
            # Enforce the scope supplied to the ORM, not just return any ID.
            self.assertEqual(set(lookup), {"id", "organization", "is_active"})
            self.assertIs(lookup["organization"], self.org)
            self.assertIs(lookup["is_active"], True)
            if lookup["id"] == "malformed":
                raise ValueError("invalid identifier")
            for ws in (self.a1, self.a2, self.foreign, self.inactive, self.denied):
                if (
                    ws.id == lookup["id"]
                    and ws.organization is lookup["organization"]
                    and ws.is_active
                ):
                    return ws
            raise DoesNotExist()

        self.workspaces.get.side_effect = get_workspace
        self.memberships = MagicMock()
        self.memberships.filter.return_value.select_related.return_value.first.return_value = SimpleNamespace(
            workspace=self.a2
        )
        self.memberships.filter.return_value.exists.return_value = True
        self.orgs = MagicMock()
        self.orgs.get.side_effect = lambda **kw: (
            self.org if kw["id"] == "org-a" else self.foreign_org
        )
        self.org_memberships = MagicMock()
        self.org_memberships.filter.return_value.select_related.return_value.first.return_value = SimpleNamespace(
            organization=self.org, organization_id=self.org.id
        )
        self.org_memberships.filter.return_value.exists.return_value = True
        self.keys = MagicMock()
        self.tokens = MagicMock()
        self.cache = MagicMock()
        self.cache_entry = {"user": self.user, "token": "existing-token"}
        self.cache.get.side_effect = lambda _key: self.cache_entry
        self.ns.update(
            User=SimpleNamespace(objects=self.users),
            Workspace=SimpleNamespace(
                objects=self.workspaces, DoesNotExist=DoesNotExist
            ),
            WorkspaceMembership=SimpleNamespace(no_workspace_objects=self.memberships),
            Organization=SimpleNamespace(objects=self.orgs, DoesNotExist=DoesNotExist),
            OrgApiKey=SimpleNamespace(objects=self.keys, DoesNotExist=DoesNotExist),
            AuthToken=SimpleNamespace(objects=self.tokens),
            cache=self.cache,
            decrypt_message=MagicMock(
                return_value={"id": "old-token-id", "user_id": "owner"}
            ),
        )
        self.context = MagicMock()
        modules = {}
        for name, members in {
            "accounts.models": {"User": self.ns["User"]},
            "accounts.models.organization_membership": {
                "OrganizationMembership": SimpleNamespace(
                    no_workspace_objects=self.org_memberships
                ),
            },
            "tfc.middleware.workspace_context": {"set_workspace_context": self.context},
        }.items():
            module = ModuleType(name)
            module.__dict__.update(members)
            modules[name] = module
        self.modules = patch.dict("sys.modules", modules)
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.auth = self.ns["APIKeyAuthentication"]()
        # No membership creation or model write is permitted by this suite.
        self.auth._get_or_create_default_workspace = MagicMock(
            side_effect=AssertionError("unexpected default workspace creation")
        )

    def request(self, workspace=None, query=None, method="GET", implicit_org=False):
        headers = {} if implicit_org else {"X-Organization-Id": self.org.id}
        if workspace:
            headers["X-Workspace-Id"] = workspace
        return SimpleNamespace(
            headers=headers,
            GET=query or {},
            META={"HTTP_AUTHORIZATION": "Bearer existing-token"},
            path="/tracer/project/list_projects/",
            method=method,
            resolver_match=None,
        )

    def authenticate(self, request):
        result = self.auth.authenticate(request)
        self.assertEqual(result, (self.user, "existing-token"))
        self.assertIs(request.organization, self.org)
        self.context.assert_called_with(
            workspace=request.workspace, organization=self.org, user=self.user
        )
        return request.workspace

    def assert_fresh_preference(self, config):
        self.user.config = {
            "orgWorkspaceMap": {"org-a": "a2"},
            "currentWorkspaceId": "a2",
        }
        cached_config = deepcopy(self.user.config)
        self.persisted_config = config
        self.assertIs(self.authenticate(self.request()), self.a1)
        self.users.filter.assert_called_once_with(pk=self.user.pk)
        self.users.filter.return_value.values_list.assert_called_once_with(
            "config", flat=True
        )
        self.users.filter.return_value.values_list.return_value.first.assert_called_once_with()
        self.users.select_related.assert_not_called()
        self.assertEqual(self.user.config, cached_config)

    def test_existing_cached_token_observes_persisted_switch_on_next_request(self):
        self.persisted_config = {"orgWorkspaceMap": {"org-a": "a2"}}
        self.assertIs(self.authenticate(self.request()), self.a2)
        # Simulate the committed state written by the public switch handler;
        # keep both the encrypted token and its cached user object unchanged.
        self.persisted_config = {
            "orgWorkspaceMap": {"org-a": "a1"},
            "currentWorkspaceId": "a1",
            "defaultWorkspaceId": "a1",
        }
        self.assertIs(self.authenticate(self.request()), self.a1)
        self.assertEqual(self.user.config, {})
        self.assertEqual(self.users.filter.call_count, 2)
        self.assertEqual(self.cache.get.call_count, 2)
        self.cache.get.assert_called_with("access_token_old-token-id")
        self.assertEqual(self.cache.set.call_count, 2)
        self.assertEqual(self.tokens.filter.return_value.update.call_count, 2)
        self.users.select_related.assert_not_called()

    def test_fresh_org_map_precedes_both_legacy_preferences(self):
        self.assert_fresh_preference(
            {
                "orgWorkspaceMap": {"org-a": "a1"},
                "currentWorkspaceId": "a2",
                "defaultWorkspaceId": "a2",
            }
        )

    def test_fresh_current_workspace_precedes_default(self):
        self.assert_fresh_preference(
            {"currentWorkspaceId": "a1", "defaultWorkspaceId": "a2"}
        )

    def test_fresh_default_workspace_without_current(self):
        self.assert_fresh_preference({"defaultWorkspaceId": "a1"})

    def test_other_org_map_does_not_override_current_org_legacy_preference(self):
        self.assert_fresh_preference(
            {"orgWorkspaceMap": {"org-b": "b1"}, "currentWorkspaceId": "a1"}
        )

    def test_both_org_and_workspace_implicit_resolve_fresh_preferences(self):
        self.user.config = {
            "currentOrganizationId": "org-b",
            "currentWorkspaceId": "a2",
        }
        self.persisted_config = {
            "currentOrganizationId": "org-a",
            "orgWorkspaceMap": {"org-a": "a1"},
        }
        self.assertIs(self.authenticate(self.request(implicit_org=True)), self.a1)
        # Existing organization fallback SELECT plus the workspace fallback SELECT.
        self.assertEqual(self.users.filter.call_count, 2)

    def test_explicit_workspace_header_wins_without_config_read(self):
        self.persisted_config = {"currentWorkspaceId": "a1"}
        self.assertIs(self.authenticate(self.request(workspace="a2")), self.a2)
        self.users.filter.assert_not_called()

    def test_explicit_workspace_query_wins_without_config_read(self):
        self.persisted_config = {"currentWorkspaceId": "a1"}
        self.assertIs(
            self.authenticate(self.request(query={"workspace_id": "a2"})), self.a2
        )
        self.users.filter.assert_not_called()

    def test_header_precedes_query(self):
        self.assertIs(
            self.authenticate(
                self.request(workspace="a2", query={"workspace_id": "a1"})
            ),
            self.a2,
        )
        self.users.filter.assert_not_called()

    def test_api_key_binding_precedes_headers_query_and_preference(self):
        self.persisted_config = {"currentWorkspaceId": "a1"}
        key = SimpleNamespace(
            organization=self.org,
            organization_id=self.org.id,
            workspace=self.a2,
            enabled=True,
            type="user",
            user=self.user,
        )
        self.keys.select_related.return_value.get.return_value = key
        request = self.request(workspace="b1", query={"workspace_id": "a1"})
        request.META = {}
        request.headers.update(
            {
                "X-Organization-Id": "org-b",
                "X-Api-Key": "synthetic-key",
                "X-Secret-Key": "synthetic-secret",
            }
        )
        self.assertEqual(self.auth.authenticate(request), (self.user, None))
        self.assertIs(request.organization, self.org)
        self.assertIs(request.workspace, self.a2)
        self.users.filter.assert_not_called()
        self.orgs.get.assert_not_called()
        self.cache.get.assert_not_called()
        self.keys.select_related.return_value.get.assert_called_once_with(
            api_key="synthetic-key",
            secret_key="synthetic-secret",
            enabled=True,
            deleted=False,
        )

    def test_unusable_persisted_preferences_use_only_active_own_membership(self):
        for preference in ("b1", "inactive", "denied", "missing", "malformed"):
            with self.subTest(preference=preference):
                self.persisted_config = {"orgWorkspaceMap": {"org-a": preference}}
                # Neither stale cached preference nor a legacy alternative may
                # replace the existing membership-fallback policy.
                self.user.config = {"currentWorkspaceId": "a1"}
                self.persisted_config["currentWorkspaceId"] = "a1"
                self.assertIs(self.authenticate(self.request()), self.a2)
                self.memberships.filter.assert_called_with(
                    user=self.user,
                    workspace__organization=self.org,
                    workspace__is_active=True,
                    is_active=True,
                )
                self.auth._get_or_create_default_workspace.assert_not_called()

    def test_empty_persisted_config_does_not_resurrect_cached_preference(self):
        for config in ({}, None):
            with self.subTest(config=config):
                self.persisted_config = config
                self.user.config = {"currentWorkspaceId": "a1"}
                self.assertIs(self.authenticate(self.request()), self.a2)

    def test_foreign_explicit_workspace_keeps_existing_own_org_fallback_policy(self):
        self.persisted_config = {"currentWorkspaceId": "a1"}
        self.assertIs(self.authenticate(self.request(workspace="b1")), self.a1)

    def test_explicit_inaccessible_workspace_propagates_permission_denied(self):
        self.persisted_config = {"currentWorkspaceId": "a1"}
        with self.assertRaises(PermissionDenied) as raised:
            self.auth.authenticate(self.request(workspace="denied"))
        self.assertEqual(str(raised.exception), "Access denied to this workspace")
        self.users.filter.assert_not_called()
        self.context.assert_not_called()

    def test_write_denial_unchanged_after_fresh_fallback(self):
        self.persisted_config = {"currentWorkspaceId": "a1"}
        self.user.can_write_to_workspace.return_value = False
        with self.assertRaises(PermissionDenied) as raised:
            self.auth.authenticate(self.request(method="POST"))
        self.assertEqual(str(raised.exception), "Write access denied to this workspace")
        self.user.can_write_to_workspace.assert_called_once_with(self.a1)
        self.context.assert_not_called()

    def test_viewer_read_allowed_without_write_access(self):
        self.persisted_config = {"currentWorkspaceId": "a1"}
        self.user.can_write_to_workspace.return_value = False
        self.assertIs(self.authenticate(self.request()), self.a1)
        self.user.can_write_to_workspace.assert_not_called()

    def test_existing_read_only_post_exemption_is_preserved(self):
        self.persisted_config = {"currentWorkspaceId": "a1"}
        self.user.can_write_to_workspace.return_value = False
        request = self.request(method="POST")
        request.resolver_match = SimpleNamespace(
            func=SimpleNamespace(cls=SimpleNamespace(workspace_write_exempt=True))
        )
        self.assertIs(self.authenticate(request), self.a1)
        self.user.can_write_to_workspace.assert_not_called()

    def test_no_organization_returns_none_without_preference_query(self):
        self.assertIsNone(self.auth._get_user_default_workspace(self.user, None))
        self.users.filter.assert_not_called()
        self.workspaces.get.assert_not_called()
        self.auth._get_or_create_default_workspace.assert_not_called()

    def test_missing_membership_keeps_existing_last_resort_call(self):
        self.memberships.filter.return_value.select_related.return_value.first.return_value = None
        self.auth._get_or_create_default_workspace.side_effect = None
        self.auth._get_or_create_default_workspace.return_value = self.a1
        self.assertIs(
            self.auth._get_user_default_workspace(self.user, self.org), self.a1
        )
        self.auth._get_or_create_default_workspace.assert_called_once_with(
            self.user, self.org
        )

    def test_failed_config_read_does_not_fall_back_to_cached_authorization_context(
        self,
    ):
        self.user.config = {"currentWorkspaceId": "a1"}
        self.users.filter.return_value.values_list.return_value.first.side_effect = (
            RuntimeError("config read failed")
        )
        with self.assertRaises(AuthenticationFailed):
            self.auth.authenticate(self.request())
        self.workspaces.get.assert_not_called()
        self.context.assert_not_called()

    def test_final_access_check_is_still_enforced(self):
        # Access was valid at explicit selection, but is denied at final binding.
        self.user.can_access_workspace.side_effect = [True, False]
        with self.assertRaises(PermissionDenied) as raised:
            self.auth.authenticate(self.request(workspace="a1"))
        self.assertEqual(str(raised.exception), "Access denied to this workspace")
        self.users.filter.assert_not_called()
        self.context.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
