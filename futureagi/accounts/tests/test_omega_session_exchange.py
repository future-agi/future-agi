import json
import os
import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.authentication import generate_encrypted_message
from accounts.models.auth_token import AuthToken
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.tests.test_omega_session_signing import assertion_parts, decode_segment
from omega_rollout.testing import admit_for_tests
from tfc.middleware.workspace_context import clear_workspace_context


@override_settings(ROOT_URLCONF="accounts.tests.omega_session_test_urls")
class ExchangeFixture(TestCase):
    """A signing key, an admitted organization, a member, a workspace and a browser token."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        clear_workspace_context()
        cache.clear()
        self.addCleanup(clear_workspace_context)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        key_file = Path(self.directory.name) / "issuer.pem"
        key_file.write_bytes(
            self.key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        key_file.chmod(0o600)
        self.environ = {
            "OMEGA_SQUAD_DELEGATION_ISSUER": "https://host.example/omega",
            "OMEGA_SQUAD_DELEGATION_KEY_ID": "test-key",
            "OMEGA_SQUAD_DELEGATION_PRIVATE_KEY_FILE": str(key_file),
        }
        environment = patch.dict(os.environ, self.environ)
        environment.start()
        self.addCleanup(environment.stop)
        self.organization = Organization.objects.create(name="Host organization")
        # The products ship dark (READINESS 3.13); this suite tests the exchange, not the rollout
        # (omega_rollout/tests/test_exchange_claims.py does).
        admit_for_tests(self.organization)
        self.user = User.objects.create_user(
            email="member@example.invalid",
            password="disposable",
            organization=self.organization,
        )
        self.membership = OrganizationMembership.no_workspace_objects.create(
            user=self.user, organization=self.organization, role="Member", level=3
        )
        self.workspace = Workspace.no_workspace_objects.create(
            name="Selected workspace",
            organization=self.organization,
            created_by=self.user,
        )
        self.workspace_membership = WorkspaceMembership.no_workspace_objects.create(
            user=self.user,
            workspace=self.workspace,
            organization_membership=self.membership,
            role="workspace_member",
            level=3,
        )
        self.token = AuthToken.no_workspace_objects.create(
            user=self.user,
            auth_type="access",
            is_active=True,
            last_used_at=timezone.now(),
        )
        self.bearer = generate_encrypted_message(
            {"id": str(self.token.pk), "user_id": str(self.user.pk)}
        )
        self.client = APIClient()
        self.headers = {
            "HTTP_AUTHORIZATION": f"Bearer {self.bearer}",
            "HTTP_X_ORGANIZATION_ID": str(self.organization.pk),
            "HTTP_X_WORKSPACE_ID": str(self.workspace.pk),
        }

    def exchange(self, body=None, headers=None, suffix=""):
        return self.client.post(
            "/accounts/omega/session-exchange/" + suffix,
            {"audience": "omega-squad"} if body is None else body,
            format="json",
            **(self.headers if headers is None else headers),
        )

    def claims(self, response):
        self.assertEqual(response.status_code, 200, response.data)
        return json.loads(decode_segment(assertion_parts(response.data)[1]))


class OmegaSessionExchangeTests(ExchangeFixture):
    def test_browser_auth_emits_only_short_lived_assertion(self):
        response = self.exchange()
        claims = self.claims(response)
        self.assertEqual(claims["sub"], str(self.user.pk))
        self.assertEqual(claims["org_id"], str(self.organization.pk))
        self.assertEqual(claims["workspace_id"], str(self.workspace.pk))
        self.assertEqual(claims["role"], "member")
        # SB W2-KEYS: a browser key says it is a person's session, and which one (D2).
        self.assertEqual(claims["auth_method"], "session")
        self.assertEqual(claims["sid"], str(self.token.pk))
        self.assertNotIn("key_id", claims)
        self.assertNotIn("email", claims)
        self.assertEqual(set(response.data), {"accessToken", "tokenType", "expiresIn"})
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_scope_names_come_from_current_authority_not_client_input(self):
        Organization.objects.filter(pk=self.organization.pk).update(
            display_name="Customer organization"
        )
        Workspace.no_workspace_objects.filter(pk=self.workspace.pk).update(
            display_name="Operations workspace"
        )
        claims = self.claims(self.exchange())
        self.assertEqual(claims["org_name"], "Customer organization")
        self.assertEqual(claims["workspace_name"], "Operations workspace")
        self.assertEqual(
            self.exchange(
                body={"audience": "omega-squad", "workspace_name": "Forged"}
            ).status_code,
            400,
        )

    def test_maximum_unicode_labels_exchange_and_pass_gateway_validation(self):
        organization_name = "\U0001f9ea" * 255
        workspace_name = "\U0001f680" * 255
        Organization.objects.filter(pk=self.organization.pk).update(
            display_name=organization_name
        )
        Workspace.no_workspace_objects.filter(pk=self.workspace.pk).update(
            display_name=workspace_name
        )
        WorkspaceMembership.no_workspace_objects.filter(
            pk=self.workspace_membership.pk
        ).update(role="workspace_admin", level=8)
        response = self.exchange()
        claims = self.claims(response)
        self.assertLessEqual(len(response.data["accessToken"]), 8192)
        self.assertEqual(claims["org_name"], organization_name)
        self.assertEqual(claims["workspace_name"], workspace_name)
        self.assertEqual(claims["role"], "member")
        self.assertEqual(claims["emergency_control"], "workspace")
        gateway_check = (
            Path(__file__).resolve().parents[3]
            / "services/omega-gateway/test/check-exchanged-assertion.mjs"
        )
        checked = subprocess.run(
            ["node", str(gateway_check)],
            input=json.dumps(
                {
                    "response": response.data,
                    "issuer": self.environ["OMEGA_SQUAD_DELEGATION_ISSUER"],
                    "kid": self.environ["OMEGA_SQUAD_DELEGATION_KEY_ID"],
                    "publicKey": self.key.public_key()
                    .public_bytes(
                        serialization.Encoding.PEM,
                        serialization.PublicFormat.SubjectPublicKeyInfo,
                    )
                    .decode(),
                    "organizationName": organization_name,
                    "workspaceName": workspace_name,
                }
            ),
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertEqual(checked.stdout.strip(), "accepted")

    def test_display_labels_do_not_break_exchange_for_legacy_whitespace(self):
        Workspace.no_workspace_objects.filter(pk=self.workspace.pk).update(
            display_name=" Operations\nworkspace "
        )
        self.assertEqual(
            self.claims(self.exchange())["workspace_name"], "Operations workspace"
        )

    def test_no_auth_cookie_only_unknown_key_and_mixed_credentials_cannot_exchange(
        self,
    ):
        # A real key's path is in test_omega_session_api_key.py (SB W2-KEYS); an unknown pair,
        # half a pair and a browser token sent with a key are refused.
        for headers in (
            {},
            {"HTTP_COOKIE": "sessionid=opaque"},
            {"HTTP_X_API_KEY": "key", "HTTP_X_SECRET_KEY": "secret"},
            {"HTTP_X_API_KEY": "key"},
            {**self.headers, "HTTP_X_API_KEY": "key"},
        ):
            with self.subTest(headers=list(headers)):
                self.assertEqual(self.exchange(headers=headers).status_code, 401)

    def test_wrong_audience_unknown_body_and_query_are_rejected(self):
        for body in (
            {"audience": "other"},
            {"audience": "omega-squad", "role": "owner"},
            {"audience": "omega-squad", "organization_id": str(self.organization.pk)},
            {},
        ):
            self.assertEqual(self.exchange(body=body).status_code, 400)
        self.assertEqual(
            self.exchange(suffix="?workspace_id=" + str(self.workspace.pk)).status_code,
            400,
        )

    def test_disabled_feature_and_invalid_key_do_not_disclose_configuration(self):
        for path in ("", "/does/not/exist/private.pem"):
            with patch.dict(
                os.environ, {"OMEGA_SQUAD_DELEGATION_PRIVATE_KEY_FILE": path}
            ):
                response = self.exchange()
                self.assertEqual(response.status_code, 503)
                self.assertNotIn("private.pem", str(response.data))
                self.assertNotIn("accessToken", response.data)

    def test_revoked_token_is_denied_even_if_native_cache_is_stale(self):
        cache.set(
            f"access_token_{self.token.pk}", {"token": self.bearer, "user": self.user}
        )
        AuthToken.no_workspace_objects.filter(pk=self.token.pk).update(is_active=False)
        self.assertEqual(self.exchange().status_code, 401)

    def test_expired_refresh_and_wrong_user_tokens_are_denied(self):
        AuthToken.no_workspace_objects.filter(pk=self.token.pk).update(
            last_used_at=timezone.now() - timedelta(days=3)
        )
        self.assertEqual(self.exchange().status_code, 401)
        AuthToken.no_workspace_objects.filter(pk=self.token.pk).update(
            last_used_at=timezone.now(), auth_type="refresh"
        )
        self.assertEqual(self.exchange().status_code, 401)
        AuthToken.no_workspace_objects.filter(pk=self.token.pk).update(
            auth_type="access"
        )
        token = generate_encrypted_message(
            {"id": str(self.token.pk), "user_id": str(uuid4())}
        )
        self.assertEqual(
            self.exchange(
                headers={**self.headers, "HTTP_AUTHORIZATION": "Bearer " + token}
            ).status_code,
            401,
        )

    def test_inactive_user_is_denied_despite_stale_cache(self):
        cache.set(
            f"access_token_{self.token.pk}", {"token": self.bearer, "user": self.user}
        )
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        self.assertEqual(self.exchange().status_code, 401)

    def test_malformed_opaque_tokens_and_payload_ids_are_rejected(self):
        for token in (
            "not-a-token",
            "eyJhbGciOiJSUzI1NiJ9.fake.jwt",
            generate_encrypted_message(
                {"id": "not-a-uuid", "user_id": str(self.user.pk)}
            ),
            generate_encrypted_message({}),
        ):
            self.assertEqual(
                self.exchange(
                    headers={**self.headers, "HTTP_AUTHORIZATION": "Bearer " + token}
                ).status_code,
                401,
            )

    def test_missing_organization_context_is_denied(self):
        OrganizationMembership.no_workspace_objects.filter(
            pk=self.membership.pk
        ).update(is_active=False)
        User.objects.filter(pk=self.user.pk).update(organization=None)
        self.assertEqual(
            self.exchange(
                headers={"HTTP_AUTHORIZATION": "Bearer " + self.bearer}
            ).status_code,
            403,
        )

    def test_default_existing_scope_is_allowed_when_selection_headers_omitted(self):
        self.assertEqual(
            self.claims(
                self.exchange(headers={"HTTP_AUTHORIZATION": "Bearer " + self.bearer})
            )["workspace_id"],
            str(self.workspace.pk),
        )

    def test_removed_and_soft_deleted_org_membership_denies_legacy_fk(self):
        for update in (
            {"is_active": False, "deleted": False},
            {"is_active": True, "deleted": True},
        ):
            OrganizationMembership.all_objects.filter(pk=self.membership.pk).update(
                **update
            )
            self.assertEqual(self.exchange().status_code, 403)

    def test_removed_workspace_membership_is_not_auto_created(self):
        WorkspaceMembership.no_workspace_objects.filter(
            pk=self.workspace_membership.pk
        ).update(is_active=False)
        count = WorkspaceMembership.all_objects.count()
        self.assertEqual(self.exchange().status_code, 403)
        self.assertEqual(WorkspaceMembership.all_objects.count(), count)
        self.workspace_membership.refresh_from_db()
        self.assertFalse(self.workspace_membership.is_active)

    def test_inactive_and_cross_org_workspace_are_denied(self):
        Workspace.no_workspace_objects.filter(pk=self.workspace.pk).update(
            is_active=False
        )
        self.assertEqual(self.exchange().status_code, 403)
        Workspace.no_workspace_objects.filter(pk=self.workspace.pk).update(
            is_active=True
        )
        other_org = Organization.objects.create(name="Other org")
        admit_for_tests(other_org)
        other_workspace = Workspace.no_workspace_objects.create(
            name="Other workspace", organization=other_org, created_by=self.user
        )
        self.assertEqual(
            self.exchange(
                headers={**self.headers, "HTTP_X_WORKSPACE_ID": str(other_workspace.pk)}
            ).status_code,
            403,
        )

    def test_invalid_selection_never_silently_uses_default_scope(self):
        for header in ("HTTP_X_ORGANIZATION_ID", "HTTP_X_WORKSPACE_ID"):
            for value in (str(uuid4()), "invalid", ""):
                with self.subTest(header=header, value=value):
                    self.assertEqual(
                        self.exchange(
                            headers={**self.headers, header: value}
                        ).status_code,
                        403,
                    )

    def test_no_workspace_context_is_denied_without_creating_default(self):
        WorkspaceMembership.no_workspace_objects.filter(
            pk=self.workspace_membership.pk
        ).update(is_active=False)
        headers = {
            key: value
            for key, value in self.headers.items()
            if key != "HTTP_X_WORKSPACE_ID"
        }
        count = Workspace.no_workspace_objects.count()
        self.assertEqual(self.exchange(headers=headers).status_code, 403)
        self.assertEqual(Workspace.no_workspace_objects.count(), count)

    def test_org_owner_admin_override_workspace_viewer(self):
        WorkspaceMembership.no_workspace_objects.filter(
            pk=self.workspace_membership.pk
        ).update(level=1, role="workspace_viewer")
        for level, role in ((15, "owner"), (8, "admin")):
            OrganizationMembership.no_workspace_objects.filter(
                pk=self.membership.pk
            ).update(level=level)
            self.assertEqual(self.claims(self.exchange())["role"], role)

    def test_org_admin_does_not_require_workspace_membership(self):
        OrganizationMembership.no_workspace_objects.filter(
            pk=self.membership.pk
        ).update(level=8)
        WorkspaceMembership.no_workspace_objects.filter(
            pk=self.workspace_membership.pk
        ).update(is_active=False)
        claims = self.claims(self.exchange())
        self.assertEqual(claims["role"], "admin")
        self.assertEqual(claims["emergency_control"], "organization")

    def test_workspace_admin_cannot_elevate_organization_role(self):
        self.assertNotIn("emergency_control", self.claims(self.exchange()))
        WorkspaceMembership.no_workspace_objects.filter(
            pk=self.workspace_membership.pk
        ).update(level=8)
        claims = self.claims(self.exchange())
        self.assertEqual(claims["role"], "member")
        self.assertIs(claims.get("tool_policy_workspace_admin"), True)
        self.assertEqual(claims["emergency_control"], "workspace")
        WorkspaceMembership.no_workspace_objects.filter(
            pk=self.workspace_membership.pk
        ).update(level=3)
        claims = self.claims(self.exchange())
        self.assertNotIn("tool_policy_workspace_admin", claims)
        self.assertNotIn("emergency_control", claims)
        self.assertEqual(
            self.exchange(
                body={"audience": "omega-squad", "emergency_control": "organization"}
            ).status_code,
            400,
        )

    def test_role_downgrade_is_current_and_viewer_is_not_promoted(self):
        OrganizationMembership.no_workspace_objects.filter(
            pk=self.membership.pk
        ).update(level=15)
        self.assertEqual(self.claims(self.exchange())["role"], "owner")
        OrganizationMembership.no_workspace_objects.filter(
            pk=self.membership.pk
        ).update(level=3)
        self.assertEqual(self.claims(self.exchange())["role"], "member")
        OrganizationMembership.no_workspace_objects.filter(
            pk=self.membership.pk
        ).update(level=1)
        self.assertEqual(self.exchange().status_code, 403)

    def test_legacy_role_strings_are_used_only_when_level_is_null(self):
        OrganizationMembership.no_workspace_objects.filter(
            pk=self.membership.pk
        ).update(level=None, role="Admin")
        self.assertEqual(self.claims(self.exchange())["role"], "admin")

    def test_openapi_documents_strict_request_and_raw_response(self):
        from drf_yasg import openapi
        from drf_yasg.generators import OpenAPISchemaGenerator

        schema = OpenAPISchemaGenerator(
            info=openapi.Info(title="Issuer", default_version="v1")
        ).get_schema(public=True)
        operation = next(
            value["post"]
            for path, value in schema.paths.items()
            if path.endswith("/session-exchange/")
        )
        self.assertTrue(operation["x-runtime-request-validation"])
        self.assertTrue(operation["x-runtime-response-validation"])
        self.assertEqual(
            set(schema.definitions["OmegaSessionExchangeResponse"]["properties"]),
            {"accessToken", "tokenType", "expiresIn", "accessTokens"},
        )
        self.assertIn("429", operation["responses"])

    # F-GATE: batched keys (CP P7), the viewer reason (CP P10) and the per-user limit (CP P5).

    def test_batch_mints_count_distinct_one_use_keys_in_one_call(self):
        response = self.exchange({"audience": "omega-squad", "count": 5})
        self.assertEqual(response.status_code, 200, response.data)
        tokens = response.data["accessTokens"]
        self.assertEqual(len(tokens), 5)
        self.assertEqual(response.data["accessToken"], tokens[0])
        claims = [
            json.loads(
                decode_segment(token.removeprefix("omega_delegate_").split(".")[1])
            )
            for token in tokens
        ]
        self.assertEqual(len({claim["jti"] for claim in claims}), 5)
        self.assertEqual(len({claim["nonce"] for claim in claims}), 5)
        for claim in claims:
            self.assertEqual(claim["sub"], str(self.user.pk))
            self.assertEqual(claim["workspace_id"], str(self.workspace.pk))
            self.assertEqual(claim["exp"] - claim["iat"], 45)
        single = self.exchange({"audience": "omega-squad", "count": 1})
        self.assertEqual(len(single.data["accessTokens"]), 1)
        self.assertNotIn("accessTokens", self.exchange().data)

    def test_batch_size_is_bounded(self):
        for count in (0, 9, -1, "eight", 2.5):
            with self.subTest(count=count):
                self.assertEqual(
                    self.exchange(
                        {"audience": "omega-squad", "count": count}
                    ).status_code,
                    400,
                )

    def test_a_viewer_is_refused_with_a_reason_the_host_can_show(self):
        OrganizationMembership.no_workspace_objects.filter(
            pk=self.membership.pk
        ).update(level=1)
        response = self.exchange()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "omega_viewer_not_admitted")
        self.assertNotIn("accessToken", response.data)

    def test_exchange_is_limited_per_user_with_retry_after(self):
        with patch.dict(os.environ, {"OMEGA_SESSION_EXCHANGE_THROTTLE_RATE": "3/min"}):
            for _ in range(3):
                self.assertEqual(self.exchange().status_code, 200)
            refused = self.exchange({"audience": "omega-squad", "count": 8})
            self.assertEqual(refused.status_code, 429)
            self.assertGreaterEqual(int(refused["Retry-After"]), 1)
            self.assertEqual(refused.data["code"], "throttled")
            self.assertNotIn("accessToken", refused.data)
            # A second tenant's user is not affected by the first user's limit.
            other_org = Organization.objects.create(name="Tenant B")
            # Admitted like the first tenant, so only the per-user limit can refuse it (READINESS 3.13).
            admit_for_tests(other_org)
            other = User.objects.create_user(
                email="tenant-b@example.invalid",
                password="disposable",
                organization=other_org,
            )
            other_membership = OrganizationMembership.no_workspace_objects.create(
                user=other, organization=other_org, role="Owner", level=15
            )
            other_workspace = Workspace.no_workspace_objects.create(
                name="B", organization=other_org, created_by=other
            )
            WorkspaceMembership.no_workspace_objects.create(
                user=other,
                workspace=other_workspace,
                organization_membership=other_membership,
                role="workspace_admin",
                level=8,
            )
            token = AuthToken.no_workspace_objects.create(
                user=other,
                auth_type="access",
                is_active=True,
                last_used_at=timezone.now(),
            )
            bearer = generate_encrypted_message(
                {"id": str(token.pk), "user_id": str(other.pk)}
            )
            response = self.exchange(
                headers={
                    "HTTP_AUTHORIZATION": f"Bearer {bearer}",
                    "HTTP_X_ORGANIZATION_ID": str(other_org.pk),
                    "HTTP_X_WORKSPACE_ID": str(other_workspace.pk),
                }
            )
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(self.claims(response)["org_id"], str(other_org.pk))
