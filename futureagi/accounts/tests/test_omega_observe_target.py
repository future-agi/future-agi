"""Real HTTP/key/workspace ORM tests with a narrowly substituted project table.

The full tracer app imports unrelated ClickHouse/model-hub services. This focused SQLite suite
substitutes only `_project_model` with a real ORM table carrying the Project fields and uniqueness
used by discovery. Production Project schema/migration compatibility is a separate check.
"""

import hmac
import os
from unittest.mock import patch
from uuid import uuid4

from django.core.cache import cache
from django.db import connection, models
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Organization, OrgApiKey, User
from accounts.models.workspace import Workspace
from omega_rollout.testing import admit_for_tests
from tfc.middleware.workspace_context import clear_workspace_context


class ObserveTargetProjectFixture(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4)
    # This focused unmanaged table is created only for this class. Do not make unrelated account
    # deletion tests discover a cascade into it after tearDownClass has removed the table.
    organization = models.ForeignKey(
        Organization, on_delete=models.DO_NOTHING, related_name="+"
    )
    workspace = models.ForeignKey(
        Workspace, on_delete=models.DO_NOTHING, related_name="+", null=True
    )
    name = models.CharField(max_length=255)
    trace_type = models.CharField(max_length=20)
    deleted = models.BooleanField(default=False)
    no_workspace_objects = models.Manager()

    class Meta:
        app_label = "accounts"
        managed = False
        db_table = "omega_observe_target_test_project"
        constraints = [
            models.UniqueConstraint(
                fields=["name", "trace_type", "organization", "workspace"],
                condition=models.Q(deleted=False),
                name="omega_observe_fixture_unique_project",
            )
        ]


@override_settings(ROOT_URLCONF="accounts.tests.omega_session_test_urls")
class OmegaObserveTargetTests(TestCase):
    @classmethod
    def setUpClass(cls):
        with connection.schema_editor() as editor:
            editor.create_model(ObserveTargetProjectFixture)
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        with connection.schema_editor() as editor:
            editor.delete_model(ObserveTargetProjectFixture)

    def setUp(self):
        clear_workspace_context()
        cache.clear()
        self.addCleanup(clear_workspace_context)
        project_model = patch(
            "accounts.views.omega_observe_target._project_model",
            return_value=ObserveTargetProjectFixture,
        )
        self.project_model = project_model.start()
        self.addCleanup(project_model.stop)
        self.org = Organization.objects.create(name="Observe target A")
        # The products ship dark (READINESS 3.13); this suite tests the target lookup, not the rollout.
        admit_for_tests(self.org)
        self.user = User.objects.create_user(
            email="observe-target@example.invalid",
            password="fixture",
            organization=self.org,
        )
        self.workspace = Workspace.no_workspace_objects.create(
            name="Pinned", organization=self.org, created_by=self.user
        )
        self.sibling = Workspace.no_workspace_objects.create(
            name="Sibling", organization=self.org, created_by=self.user
        )
        self.other_org = Organization.objects.create(name="Observe target B")
        admit_for_tests(self.other_org)
        self.other_workspace = Workspace.no_workspace_objects.create(
            name="Other tenant", organization=self.other_org, created_by=self.user
        )
        from accounts.tests.personal_keys import personal_key_for

        self.key = personal_key_for(self.user, self.org, workspace=self.workspace)
        self.project = self.make_project("Exact target")
        self.headers = {
            "HTTP_X_API_KEY": self.key.api_key,
            "HTTP_X_SECRET_KEY": self.key.secret_key,
        }
        self.client = APIClient()

    def make_project(self, name, **kwargs):
        return ObserveTargetProjectFixture.no_workspace_objects.create(
            name=name,
            organization=kwargs.pop("organization", self.org),
            workspace=kwargs.pop("workspace", self.workspace),
            trace_type=kwargs.pop("trace_type", "observe"),
            **kwargs,
        )

    def request(self, project_id=None, headers=None, suffix=""):
        response = self.client.get(
            f"/accounts/omega/observe-target/?project_id={project_id or self.project.pk}{suffix}",
            **(self.headers if headers is None else headers),
        )
        self.assertEqual(response["Cache-Control"], "no-store")
        return response

    def test_returns_only_exact_target_for_workspace_pinned_key(self):
        response = self.request()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            response.data,
            {
                "organizationId": str(self.org.pk),
                "workspaceId": str(self.workspace.pk),
                "projectId": str(self.project.pk),
                "projectName": self.project.name,
            },
        )
        self.assertNotIn(self.key.api_key, str(response.data))
        self.assertNotIn(self.key.secret_key, str(response.data))

    def test_missing_wrong_bearer_cookie_and_whitespace_credentials_are_rejected(self):
        for headers in (
            {},
            {"HTTP_X_API_KEY": self.key.api_key},
            {**self.headers, "HTTP_X_SECRET_KEY": "wrong"},
            {**self.headers, "HTTP_AUTHORIZATION": "Bearer browser"},
            {**self.headers, "HTTP_AUTHORIZATION": ""},
            {**self.headers, "HTTP_COOKIE": "sessionid=browser"},
            {**self.headers, "HTTP_X_API_KEY": " " + self.key.api_key},
            {**self.headers, "HTTP_X_SECRET_KEY": self.key.secret_key + " "},
        ):
            with self.subTest(headers=list(headers)):
                self.assertEqual(self.request(headers=headers).status_code, 401)
        self.project_model.assert_not_called()

    def test_rechecks_disabled_and_soft_deleted_keys_on_every_request(self):
        self.assertEqual(self.request().status_code, 200)
        for update in (
            {"enabled": False, "deleted": False},
            {"enabled": True, "deleted": True},
        ):
            OrgApiKey.all_objects.filter(pk=self.key.pk).update(**update)
            self.assertEqual(self.request().status_code, 401)

    def test_unpinned_inactive_deleted_and_wrong_org_workspace_are_denied(self):
        OrgApiKey.no_workspace_objects.filter(pk=self.key.pk).update(workspace=None)
        self.assertEqual(self.request().status_code, 403)
        OrgApiKey.no_workspace_objects.filter(pk=self.key.pk).update(
            workspace=self.other_workspace
        )
        self.assertEqual(self.request().status_code, 401)
        OrgApiKey.no_workspace_objects.filter(pk=self.key.pk).update(
            workspace=self.workspace
        )
        for update in (
            {"is_active": False, "deleted": False},
            {"is_active": True, "deleted": True},
        ):
            Workspace.all_objects.filter(pk=self.workspace.pk).update(**update)
            self.assertEqual(self.request().status_code, 401)
        self.project_model.assert_not_called()

    def test_scope_header_mismatches_cannot_fall_back_to_the_key_scope(self):
        for header, value in (
            ("HTTP_X_ORGANIZATION_ID", str(self.other_org.pk)),
            ("HTTP_X_WORKSPACE_ID", str(self.sibling.pk)),
            ("HTTP_X_WORKSPACE_ID", "invalid"),
        ):
            self.assertEqual(
                self.request(headers={**self.headers, header: value}).status_code, 403
            )
        self.assertEqual(
            self.request(
                headers={
                    **self.headers,
                    "HTTP_X_ORGANIZATION_ID": str(self.org.pk),
                    "HTTP_X_WORKSPACE_ID": str(self.workspace.pk),
                }
            ).status_code,
            200,
        )

    def test_wrong_tenant_workspace_experiment_unpinned_and_deleted_projects_are_indistinguishable(
        self,
    ):
        cases = [
            self.make_project(
                "Other org", organization=self.other_org, workspace=self.other_workspace
            ),
            self.make_project("Sibling", workspace=self.sibling),
            self.make_project("Experiment", trace_type="experiment"),
            self.make_project("Legacy null workspace", workspace=None),
            self.make_project("Deleted", deleted=True),
        ]
        responses = [self.request(project_id=value.pk) for value in cases] + [
            self.request(project_id=uuid4())
        ]
        self.assertTrue(all(response.status_code == 404 for response in responses))
        self.assertTrue(
            all(response.data == responses[0].data for response in responses)
        )

    def test_same_name_experiment_collision_is_denied_only_in_bound_workspace(self):
        self.make_project(
            self.project.name, workspace=self.sibling, trace_type="experiment"
        )
        self.make_project(
            self.project.name,
            organization=self.other_org,
            workspace=self.other_workspace,
        )
        self.assertEqual(self.request().status_code, 200)
        collision = self.make_project(self.project.name, trace_type="experiment")
        self.assertEqual(self.request().status_code, 404)
        ObserveTargetProjectFixture.no_workspace_objects.filter(pk=collision.pk).update(
            deleted=True
        )
        self.assertEqual(self.request().status_code, 200)

    def test_invalid_unknown_duplicate_queries_and_write_methods_fail_closed(self):
        self.assertEqual(self.request(project_id="not-a-uuid").status_code, 400)
        self.assertEqual(
            self.request(suffix="&workspace_id=" + str(self.workspace.pk)).status_code,
            400,
        )
        self.assertEqual(
            self.request(suffix="&project_id=" + str(self.project.pk)).status_code, 400
        )
        for method in ("post", "put", "patch", "delete"):
            response = getattr(self.client, method)(
                "/accounts/omega/observe-target/", {}, format="json", **self.headers
            )
            self.assertEqual(response.status_code, 405)
            self.assertEqual(response["Cache-Control"], "no-store")

    # Lane F-GATE (READINESS L03): every lookup counts against the key it names, wrong
    # secrets included, and the secret is compared in constant time after a lookup by id.

    def test_lookups_are_limited_per_key_and_failed_guesses_count(self):
        from accounts.tests.personal_keys import personal_key_for

        other_key = personal_key_for(self.user, self.org, workspace=self.workspace)
        with patch.dict(os.environ, {"OMEGA_OBSERVE_TARGET_THROTTLE_RATE": "3/min"}):
            self.assertEqual(self.request().status_code, 200)
            for _ in range(2):
                wrong = {**self.headers, "HTTP_X_SECRET_KEY": "wrong-secret"}
                self.assertEqual(self.request(headers=wrong).status_code, 401)
            refused = self.request()
            self.assertEqual(refused.status_code, 429)
            self.assertGreaterEqual(int(refused["Retry-After"]), 1)
            self.assertNotIn(self.key.secret_key, str(refused.data))
            # Another key is not affected.
            other = {
                "HTTP_X_API_KEY": other_key.api_key,
                "HTTP_X_SECRET_KEY": other_key.secret_key,
            }
            self.assertEqual(self.request(headers=other).status_code, 200)

    def test_secret_is_compared_after_a_lookup_by_public_id(self):
        # The view checks the secret through find_api_key (READINESS L03), whose
        # verify_secret does the one constant-time compare against the keyed hash.
        with patch(
            "accounts.api_secrets.hmac.compare_digest",
            wraps=hmac.compare_digest,
        ) as compare:
            self.assertEqual(
                self.request(
                    headers={**self.headers, "HTTP_X_SECRET_KEY": "wrong"}
                ).status_code,
                401,
            )
            self.assertEqual(
                self.request(
                    headers={"HTTP_X_API_KEY": "no-such-key", "HTTP_X_SECRET_KEY": "x"}
                ).status_code,
                401,
            )
            self.assertEqual(self.request().status_code, 200)
        # A miss, a wrong secret and a match all take the same constant-time compare.
        self.assertEqual(compare.call_count, 3)
