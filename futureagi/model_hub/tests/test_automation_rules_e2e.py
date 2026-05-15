"""
End-to-end tests for automation rules — evaluate_rule scoping, filtering,
soft-delete exclusion, dry-run preview, org isolation, field mapping, and
computed-field annotations across all source types (trace, span, session,
simulation, dataset_row).
"""

import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AnnotationQueueAnnotator,
    AutomationRule,
    QueueItem,
)
from model_hub.models.choices import (
    AnnotatorRole,
    AutomationRuleTriggerFrequency,
    DatasetSourceChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.tasks.annotation_automation import run_due_automation_rules
from model_hub.utils.annotation_queue_helpers import (
    evaluate_rule,
    is_automation_rule_due,
)
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import set_workspace_context
from tfc.temporal.schedules.model_hub import MODEL_HUB_SCHEDULES

QUEUE_URL = "/model-hub/annotation-queues/"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
# NOTE: organization, user, workspace, auth_client come from the root
# conftest.py. The previous local overrides used a plain APIClient which did
# not inject request.organization, so the evaluate_rule view raised
# AttributeError and the test only "passed" when a previous test leaked the
# WorkspaceAwareAPIClient APIView.initial patch into process state. Using the
# shared fixtures keeps thread-local workspace context and request.organization
# correctly scoped to this test's org, preventing the FK-violation cascade
# that the stale patch produced during teardown.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_queue(auth_client, name, **extra):
    payload = {"name": name, **extra}
    resp = auth_client.post(QUEUE_URL, payload, format="json")
    assert resp.status_code == status.HTTP_201_CREATED, resp.data
    return resp.data["id"]


def _create_label(organization, workspace, name, label_type="categorical"):
    from model_hub.models.develop_annotations import AnnotationsLabels

    label_settings = {}
    if label_type == "categorical":
        label_settings = {
            "options": [{"label": "Positive"}, {"label": "Negative"}],
            "multi_choice": False,
            "rule_prompt": "",
            "auto_annotate": False,
            "strategy": None,
        }
    elif label_type == "star":
        label_settings = {"no_of_stars": 5}
    elif label_type == "numeric":
        label_settings = {
            "min": 0,
            "max": 100,
            "step_size": 1,
            "display_type": "slider",
        }
    elif label_type == "text":
        label_settings = {"placeholder": "", "min_length": 0, "max_length": 1000}
    return AnnotationsLabels.objects.create(
        name=name,
        type=label_type,
        organization=organization,
        workspace=workspace,
        settings=label_settings,
    )


def _create_project(organization, workspace, name="Test Project"):
    from tracer.models.project import Project

    return Project.objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        model_type="GenerativeLLM",
        trace_type="observe",
    )


def _create_trace(project, name="test trace"):
    from tracer.models.trace import Trace

    return Trace.objects.create(
        name=name,
        project=project,
        input={"message": "hello"},
        output={"response": "world"},
    )


def _rules_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/automation-rules/"


def _rule_detail_url(queue_id, rule_id):
    return f"{QUEUE_URL}{queue_id}/automation-rules/{rule_id}/"


def _items_url(queue_id):
    return f"{QUEUE_URL}{queue_id}/items/"


# ===========================================================================
# Tests
# ===========================================================================


@pytest.mark.django_db
class TestAutomationRulesE2E:
    """End-to-end tests for automation rule evaluation."""

    @pytest.fixture(autouse=True)
    def _allow_automation_rule_entitlements(self):
        """These tests cover rule evaluation, not billing-limit enforcement."""
        with patch(
            "ee.usage.services.entitlements.Entitlements.can_create",
            return_value=SimpleNamespace(allowed=True),
        ):
            yield

    @pytest.fixture(autouse=True)
    def _run_automation_rule_activity_inline(self):
        """Run the rule evaluation inline + embed the result in the 202 body.

        Production /evaluate hands the work to a Temporal activity and returns
        202 with ``{status, workflow_id, message}`` (the activity emails the
        result later). These tests pre-date that change and assert on
        ``resp.data["matched"]`` etc., so we keep the original ``evaluate_rule``
        semantics — call it synchronously, embed its result under
        ``response.data["result"]`` so ``resp.data.get("result", resp.data)``
        in the tests sees the legacy keys.

        ``test_evaluate_rule_returns_202_with_workflow_id`` skips this fixture
        and exercises the real async path.
        """
        if getattr(self, "_skip_inline_evaluate", False):
            yield
            return

        from model_hub.tasks.annotation_automation import (
            evaluate_rule_manual_async,
        )

        # The ``@temporal_activity`` decorator wraps the function so it calls
        # ``close_old_connections()`` before+after each invocation, which
        # closes the test transaction's DB connection. In tests we invoke
        # the original function directly to keep the test DB session alive.
        target_fn = getattr(
            evaluate_rule_manual_async,
            "_original_func",
            evaluate_rule_manual_async,
        )

        # Holds the result of the most recent inline activity run so the
        # test client can read it from the 202 response (see the response
        # wrapper below). One slot is enough: tests run sequentially within
        # a single fixture scope.
        _inline_result_holder = {"result": None}

        def _inline_run(
            activity_name,
            args=(),
            kwargs=None,
            queue="default",
            task_id=None,
        ):
            if activity_name == "evaluate_rule_manual_async":
                _inline_result_holder["result"] = target_fn(**(kwargs or {}))
            return task_id or "inline-workflow-id"

        # Wrap the test client's ``post`` so any 202 from the evaluate
        # endpoint gets the inline result merged into ``response.data``.
        # This keeps legacy tests (``assert result["matched"] == N``) green
        # without rewriting them — they read ``resp.data["result"]`` (or
        # the top-level fallback) which is now populated.
        from rest_framework.test import APIClient

        original_post = APIClient.post

        def _post(self, path, data=None, *args, **kwargs):
            _inline_result_holder["result"] = None
            response = original_post(self, path, data, *args, **kwargs)
            if (
                response.status_code == status.HTTP_202_ACCEPTED
                and "/automation-rules/" in path
                and path.rstrip("/").endswith("/evaluate")
                and _inline_result_holder["result"] is not None
            ):
                response.data = {
                    **response.data,
                    "result": _inline_result_holder["result"],
                }
            return response

        with patch(
            "tfc.temporal.drop_in.runner.start_activity_sync",
            side_effect=_inline_run,
        ), patch.object(APIClient, "post", _post):
            yield

    # -----------------------------------------------------------------------
    # 1. Basic trace source evaluation
    # -----------------------------------------------------------------------
    def test_evaluate_rule_with_trace_source(
        self, auth_client, organization, workspace
    ):
        """Create 3 traces, evaluate a rule with no conditions, assert all 3
        are added as queue items."""
        project = _create_project(organization, workspace)
        t1 = _create_trace(project, name="trace-1")
        t2 = _create_trace(project, name="trace-2")
        t3 = _create_trace(project, name="trace-3")

        queue_id = _create_queue(auth_client, name="Trace Q1")
        # Scope queue to this project so we only pick up our traces
        AnnotationQueue.objects.filter(pk=queue_id).update(project=project)

        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "All traces",
                "source_type": "trace",
                "conditions": {},
                "enabled": True,
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED
        rule_id = resp.data["id"]

        resp = auth_client.post(
            f"{_rule_detail_url(queue_id, rule_id)}evaluate/",
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        result = resp.data.get("result", resp.data)
        assert result["matched"] == 3
        assert result["added"] == 3
        assert result["duplicates"] == 0

    # -----------------------------------------------------------------------
    # 2. Conditions-based filtering
    # -----------------------------------------------------------------------
    def test_evaluate_rule_with_conditions(self, auth_client, organization, workspace):
        """Create traces with different names, filter by name contains 'good',
        assert only matching traces added."""
        project = _create_project(organization, workspace, name="Cond Project")
        _create_trace(project, name="good trace 1")
        _create_trace(project, name="good trace 2")
        _create_trace(project, name="bad trace 1")

        queue_id = _create_queue(auth_client, name="Cond Q1")
        AnnotationQueue.objects.filter(pk=queue_id).update(project=project)

        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "Good only",
                "source_type": "trace",
                "conditions": {
                    "rules": [
                        {"field": "name", "op": "contains", "value": "good"},
                    ]
                },
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]

        resp = auth_client.post(
            f"{_rule_detail_url(queue_id, rule_id)}evaluate/",
            format="json",
        )
        result = resp.data.get("result", resp.data)
        assert result["matched"] == 2
        assert result["added"] == 2

    # -----------------------------------------------------------------------
    # 3. Project-scoped queue
    # -----------------------------------------------------------------------
    def test_evaluate_rule_project_scoped_queue(
        self, auth_client, organization, workspace
    ):
        """Queue scoped to project1 must NOT include project2 traces."""
        project1 = _create_project(organization, workspace, name="Project One")
        project2 = _create_project(organization, workspace, name="Project Two")

        _create_trace(project1, name="p1-trace-1")
        _create_trace(project1, name="p1-trace-2")
        _create_trace(project2, name="p2-trace-1")

        queue_id = _create_queue(auth_client, name="Scoped Q1")
        AnnotationQueue.objects.filter(pk=queue_id).update(project=project1)

        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "Project1 only",
                "source_type": "trace",
                "conditions": {},
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]

        resp = auth_client.post(
            f"{_rule_detail_url(queue_id, rule_id)}evaluate/",
            format="json",
        )
        result = resp.data.get("result", resp.data)
        assert result["matched"] == 2
        assert result["added"] == 2

        # Verify items belong to project1 traces only
        items_resp = auth_client.get(_items_url(queue_id))
        assert items_resp.status_code == status.HTTP_200_OK
        items = items_resp.data.get("results", items_resp.data)
        for item in items:
            qi = QueueItem.objects.get(pk=item["id"])
            assert qi.trace.project_id == project1.pk

    # -----------------------------------------------------------------------
    # 4. Dataset-scoped queue
    # -----------------------------------------------------------------------
    def test_evaluate_rule_dataset_scoped_queue(
        self, auth_client, organization, workspace
    ):
        """Queue scoped to dataset1 must NOT include dataset2 rows."""
        ds1 = Dataset.objects.create(
            name="DS One", organization=organization, workspace=workspace
        )
        ds2 = Dataset.objects.create(
            name="DS Two", organization=organization, workspace=workspace
        )
        Row.objects.create(dataset=ds1, order=1, metadata={})
        Row.objects.create(dataset=ds1, order=2, metadata={})
        Row.objects.create(dataset=ds2, order=1, metadata={})

        queue_id = _create_queue(auth_client, name="DS Scoped Q1")
        AnnotationQueue.objects.filter(pk=queue_id).update(dataset=ds1)

        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "DS1 only",
                "source_type": "dataset_row",
                "conditions": {},
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]

        resp = auth_client.post(
            f"{_rule_detail_url(queue_id, rule_id)}evaluate/",
            format="json",
        )
        result = resp.data.get("result", resp.data)
        assert result["matched"] == 2
        assert result["added"] == 2

    # -----------------------------------------------------------------------
    # 5. Soft-deleted records excluded
    # -----------------------------------------------------------------------
    def test_evaluate_rule_filters_deleted_records(
        self, auth_client, organization, workspace
    ):
        """Soft-deleted traces must NOT be matched by evaluate_rule."""
        project = _create_project(organization, workspace, name="Del Project")
        t1 = _create_trace(project, name="alive-trace")
        t2 = _create_trace(project, name="dead-trace")

        # Soft-delete t2
        t2.deleted = True
        t2.deleted_at = timezone.now()
        t2.save(update_fields=["deleted", "deleted_at"])

        queue_id = _create_queue(auth_client, name="Del Q1")
        AnnotationQueue.objects.filter(pk=queue_id).update(project=project)

        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "No deleted",
                "source_type": "trace",
                "conditions": {},
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]

        resp = auth_client.post(
            f"{_rule_detail_url(queue_id, rule_id)}evaluate/",
            format="json",
        )
        result = resp.data.get("result", resp.data)
        assert result["matched"] == 1
        assert result["added"] == 1

    # -----------------------------------------------------------------------
    # 6. Dry-run / preview
    # -----------------------------------------------------------------------
    def test_evaluate_rule_dry_run(self, auth_client, organization, workspace):
        """Preview endpoint should report matches without creating items."""
        project = _create_project(organization, workspace, name="Preview Project")
        _create_trace(project, name="preview-trace-1")
        _create_trace(project, name="preview-trace-2")

        queue_id = _create_queue(auth_client, name="Preview Q1")
        AnnotationQueue.objects.filter(pk=queue_id).update(project=project)

        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "Preview rule",
                "source_type": "trace",
                "conditions": {},
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]

        resp = auth_client.get(
            f"{_rule_detail_url(queue_id, rule_id)}preview/",
        )
        assert resp.status_code == status.HTTP_200_OK
        result = resp.data.get("result", resp.data)
        assert result["matched"] >= 1
        assert result["added"] == 0

        # Verify no queue items were created
        assert QueueItem.objects.filter(queue_id=queue_id, deleted=False).count() == 0

    def test_filter_mode_dry_run_propagates_truncated_flag(
        self, auth_client, organization, workspace
    ):
        """Filter-mode dry-run must propagate ``truncated`` from the resolver.

        Found via browser E2E: ``_add_source_ids_to_queue`` was dropping the
        flag in its dry-run early return, so the manual-run endpoint's peek
        never saw truncation and every filter-mode rule fell to the sync
        path — even huge ones.
        """
        from datetime import datetime as _dt

        from unittest.mock import patch as _patch

        project = _create_project(organization, workspace, name="Trunc Project")
        # Two traces, but we'll cap to 1 to force truncation.
        _create_trace(project, name="trunc-trace-1")
        _create_trace(project, name="trunc-trace-2")

        queue_id = _create_queue(auth_client, name="Trunc Queue")
        AnnotationQueue.objects.filter(pk=queue_id).update(project=project)

        # Filter-mode rule (`conditions.filter` payload) → resolver path
        # → _add_source_ids_to_queue. Use an explicit time filter so the
        # CH path engages if CH is available; the PG fallback also exercises
        # the same dry-run early return.
        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "Trunc rule",
                "source_type": "trace",
                "conditions": {
                    "filter": [
                        {
                            "column_id": "created_at",
                            "filter_config": {
                                "filter_type": "datetime",
                                "filter_op": "greater_than",
                                "filter_value": "2020-01-01T00:00:00Z",
                            },
                        }
                    ]
                },
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]

        # Force the peek to see truncation by patching the evaluator's cap.
        from model_hub.utils import annotation_queue_helpers as _h

        original_eval = _h.evaluate_rule

        def _capped_eval(rule, dry_run=False, user=None, cap=None):
            return original_eval(rule, dry_run=dry_run, user=user, cap=1)

        with _patch.object(_h, "evaluate_rule", side_effect=_capped_eval):
            from model_hub.models.annotation_queues import AutomationRule

            rule = AutomationRule.objects.get(pk=rule_id)
            result = _h.evaluate_rule(rule, dry_run=True, cap=1)

        assert result.get("truncated") is True, (
            f"dry_run with cap=1 against 2 matches must set truncated=True; "
            f"got {result!r}"
        )

    def test_preview_rule_requires_queue_manager(
        self, auth_client, organization, workspace
    ):
        """Rule preview is a queue-management action, same as evaluate."""
        project = _create_project(organization, workspace, name="Preview ACL Project")
        _create_trace(project, name="preview-acl-trace")

        queue_id = _create_queue(auth_client, name="Preview ACL Q")
        AnnotationQueue.objects.filter(pk=queue_id).update(project=project)

        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "Preview ACL rule",
                "source_type": "trace",
                "conditions": {},
                "enabled": True,
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        rule_id = resp.data["id"]

        annotator_user = User.objects.create_user(
            email=f"preview-annotator-{uuid.uuid4().hex[:8]}@futureagi.com",
            password="testpassword123",
            name="Preview Annotator",
            organization=organization,
            organization_role=OrganizationRoles.MEMBER,
        )
        AnnotationQueueAnnotator.objects.create(
            queue_id=queue_id,
            user=annotator_user,
            role=AnnotatorRole.ANNOTATOR.value,
            roles=[AnnotatorRole.ANNOTATOR.value],
        )

        from conftest import WorkspaceAwareAPIClient

        annotator_client = WorkspaceAwareAPIClient()
        annotator_client.force_authenticate(user=annotator_user)
        annotator_client.set_workspace(workspace)
        resp = annotator_client.get(f"{_rule_detail_url(queue_id, rule_id)}preview/")

        assert resp.status_code == status.HTTP_403_FORBIDDEN
        assert QueueItem.objects.filter(queue_id=queue_id, deleted=False).count() == 0
        annotator_client.stop_workspace_injection()

    # -----------------------------------------------------------------------
    # 7. Org isolation
    # -----------------------------------------------------------------------
    def test_evaluate_rule_org_isolation(self, auth_client, organization, workspace):
        """Rule for org1 must NOT pick up org2's traces."""
        # Org 1 data
        project1 = _create_project(organization, workspace, name="Org1 Project")
        _create_trace(project1, name="org1-trace")

        # Org 2 data
        org2 = Organization.objects.create(name="Other Org")
        user2 = User.objects.create_user(
            email="org2user@futureagi.com",
            password="testpassword123",
            name="Org2 User",
            organization=org2,
        )
        ws2 = Workspace.objects.create(
            name="Org2 Workspace",
            organization=org2,
            is_default=True,
            created_by=user2,
        )
        project2 = _create_project(org2, ws2, name="Org2 Project")
        _create_trace(project2, name="org2-trace")

        # Create queue + rule for org1
        queue_id = _create_queue(auth_client, name="Iso Q1")
        AnnotationQueue.objects.filter(pk=queue_id).update(project=project1)

        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "Org1 only",
                "source_type": "trace",
                "conditions": {},
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]

        resp = auth_client.post(
            f"{_rule_detail_url(queue_id, rule_id)}evaluate/",
            format="json",
        )
        result = resp.data.get("result", resp.data)
        assert result["matched"] == 1
        assert result["added"] == 1

    # -----------------------------------------------------------------------
    # 8. project__name filter in conditions
    # -----------------------------------------------------------------------
    def test_rule_with_project_name_filter(self, auth_client, organization, workspace):
        """Condition field 'project__name' should filter by project name."""
        proj_a = _create_project(organization, workspace, name="MyProject")
        proj_b = _create_project(organization, workspace, name="OtherProject")
        _create_trace(proj_a, name="a-trace")
        _create_trace(proj_b, name="b-trace")

        queue_id = _create_queue(auth_client, name="ProjName Q1")

        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "By project name",
                "source_type": "trace",
                "conditions": {
                    "rules": [
                        {
                            "field": "project__name",
                            "op": "eq",
                            "value": "MyProject",
                        },
                    ]
                },
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]

        resp = auth_client.post(
            f"{_rule_detail_url(queue_id, rule_id)}evaluate/",
            format="json",
        )
        result = resp.data.get("result", resp.data)
        assert result["matched"] == 1
        assert result["added"] == 1

    # -----------------------------------------------------------------------
    # 9. Disallowed field is rejected / ignored
    # -----------------------------------------------------------------------
    def test_disallowed_field_is_rejected(self, auth_client, organization, workspace):
        """A rule whose only condition references an unknown/disallowed
        field must fail closed — refusing to enqueue anything — rather
        than skip the bad condition and match the entire scope."""
        project = _create_project(organization, workspace, name="Reject Project")
        _create_trace(project, name="reject-trace-1")
        _create_trace(project, name="reject-trace-2")

        queue_id = _create_queue(auth_client, name="Reject Q1")
        AnnotationQueue.objects.filter(pk=queue_id).update(project=project)

        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "Disallowed field rule",
                "source_type": "trace",
                "conditions": {
                    "rules": [
                        {
                            "field": "user__password",
                            "op": "eq",
                            "value": "secret",
                        },
                    ]
                },
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]

        resp = auth_client.post(
            f"{_rule_detail_url(queue_id, rule_id)}evaluate/",
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        result = resp.data.get("result", resp.data)
        assert result["matched"] == 0
        assert result["added"] == 0
        assert "error" in result
        assert "user__password" in result["error"]

    # -----------------------------------------------------------------------
    # 10. Rule stats updated after evaluation
    # -----------------------------------------------------------------------
    def test_evaluate_rule_updates_stats(self, auth_client, organization, workspace):
        """After evaluation, rule.last_triggered_at should be set and
        trigger_count incremented."""
        project = _create_project(organization, workspace, name="Stats Project")
        _create_trace(project, name="stats-trace")

        queue_id = _create_queue(auth_client, name="Stats Q1")
        AnnotationQueue.objects.filter(pk=queue_id).update(project=project)

        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "Stats rule",
                "source_type": "trace",
                "conditions": {},
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]

        rule = AutomationRule.objects.get(pk=rule_id)
        assert rule.last_triggered_at is None
        assert rule.trigger_count == 0

        resp = auth_client.post(
            f"{_rule_detail_url(queue_id, rule_id)}evaluate/",
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK

        rule.refresh_from_db()
        assert rule.last_triggered_at is not None
        assert rule.trigger_count == 1

        # Back-date last_triggered_at past the 30s multi-click guard so the
        # next evaluation isn't 409'd as a duplicate run.
        from datetime import timedelta as _td

        from django.utils import timezone as _tz

        AutomationRule.objects.filter(pk=rule_id).update(
            last_triggered_at=_tz.now() - _td(minutes=5)
        )

        # Evaluate again — trigger_count should increment
        resp = auth_client.post(
            f"{_rule_detail_url(queue_id, rule_id)}evaluate/",
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK

        rule.refresh_from_db()
        assert rule.trigger_count == 2

    def test_manual_rule_ignores_last_triggered_as_data_watermark(
        self, auth_client, organization, workspace, user
    ):
        """Manual reservations must not hide existing backlog rows.

        The manual endpoint reserves async runs by bumping ``last_triggered_at``
        before the worker starts. If manual evaluation treats that timestamp as
        a high-watermark, old matching rows are skipped entirely.
        """
        project = _create_project(organization, workspace, name="Manual Backlog")
        trace = _create_trace(project, name="old matching trace")
        old_time = timezone.now() - timedelta(hours=1)
        type(trace).objects.filter(pk=trace.pk).update(
            created_at=old_time,
            updated_at=old_time,
        )

        queue_id = _create_queue(auth_client, name="Manual Backlog Q")
        queue = AnnotationQueue.objects.get(pk=queue_id)
        queue.project = project
        queue.save(update_fields=["project", "updated_at"])

        rule = AutomationRule.objects.create(
            queue=queue,
            organization=organization,
            name="Manual backlog rule",
            source_type="trace",
            conditions={},
            enabled=True,
            trigger_frequency=AutomationRuleTriggerFrequency.MANUAL.value,
            last_triggered_at=timezone.now(),
        )

        result = evaluate_rule(rule, user=user, cap=100)

        assert result["matched"] == 1
        assert result["added"] == 1
        assert QueueItem.objects.filter(queue=queue, trace_id=trace.id).exists()

    # -----------------------------------------------------------------------
    # 11. Long-form operators from frontend LLMFilterBox
    # -----------------------------------------------------------------------
    def test_evaluate_rule_with_long_form_operators(
        self, auth_client, organization, workspace
    ):
        """Frontend sends long-form operators (equals, contains, not_equals).
        Backend must handle them correctly."""
        project = _create_project(organization, workspace, name="LongOp Project")
        _create_trace(project, name="alpha trace")
        _create_trace(project, name="beta trace")
        _create_trace(project, name="gamma trace")

        queue_id = _create_queue(auth_client, name="LongOp Q1")
        AnnotationQueue.objects.filter(pk=queue_id).update(project=project)

        # Test "equals" operator
        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "Equals rule",
                "source_type": "trace",
                "conditions": {
                    "rules": [
                        {"field": "name", "op": "equals", "value": "alpha trace"},
                    ]
                },
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]
        resp = auth_client.post(
            f"{_rule_detail_url(queue_id, rule_id)}evaluate/", format="json"
        )
        result = resp.data.get("result", resp.data)
        assert result["matched"] == 1
        assert result["added"] == 1

    # -----------------------------------------------------------------------
    # 12. not_equals operator
    # -----------------------------------------------------------------------
    def test_evaluate_rule_not_equals(self, auth_client, organization, workspace):
        """not_equals should exclude matching records."""
        project = _create_project(organization, workspace, name="NotEq Project")
        _create_trace(project, name="keep-me")
        _create_trace(project, name="exclude-me")

        queue_id = _create_queue(auth_client, name="NotEq Q1")
        AnnotationQueue.objects.filter(pk=queue_id).update(project=project)

        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "Not equals rule",
                "source_type": "trace",
                "conditions": {
                    "rules": [
                        {"field": "name", "op": "not_equals", "value": "exclude-me"},
                    ]
                },
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]
        resp = auth_client.post(
            f"{_rule_detail_url(queue_id, rule_id)}evaluate/", format="json"
        )
        result = resp.data.get("result", resp.data)
        assert result["matched"] == 1
        assert result["added"] == 1

    # -----------------------------------------------------------------------
    # 13. camelCase field IDs (traceName) — new frontend format
    # -----------------------------------------------------------------------
    def test_evaluate_rule_camelcase_traceName(
        self, auth_client, organization, workspace
    ):
        """Frontend sends camelCase field IDs like 'traceName'.
        Backend FIELD_MAPPING must resolve them to Django ORM fields."""
        project = _create_project(organization, workspace, name="Camel Project")
        _create_trace(project, name="camel-yes")
        _create_trace(project, name="camel-no")

        queue_id = _create_queue(auth_client, name="Camel Q1")
        AnnotationQueue.objects.filter(pk=queue_id).update(project=project)

        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "CamelCase traceName",
                "source_type": "trace",
                "conditions": {
                    "rules": [
                        {"field": "traceName", "op": "contains", "value": "yes"},
                    ]
                },
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]
        resp = auth_client.post(
            f"{_rule_detail_url(queue_id, rule_id)}evaluate/", format="json"
        )
        result = resp.data.get("result", resp.data)
        assert result["matched"] == 1
        assert result["added"] == 1

    # -----------------------------------------------------------------------
    # 14. camelCase projectName filter
    # -----------------------------------------------------------------------
    def test_evaluate_rule_camelcase_projectName(
        self, auth_client, organization, workspace
    ):
        """projectName should map to project__name."""
        proj_a = _create_project(organization, workspace, name="AlphaProject")
        proj_b = _create_project(organization, workspace, name="BetaProject")
        _create_trace(proj_a, name="a-trace")
        _create_trace(proj_b, name="b-trace")

        queue_id = _create_queue(auth_client, name="ProjName Camel Q1")

        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "By projectName",
                "source_type": "trace",
                "conditions": {
                    "rules": [
                        {
                            "field": "projectName",
                            "op": "equals",
                            "value": "AlphaProject",
                        },
                    ]
                },
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]
        resp = auth_client.post(
            f"{_rule_detail_url(queue_id, rule_id)}evaluate/", format="json"
        )
        result = resp.data.get("result", resp.data)
        assert result["matched"] == 1
        assert result["added"] == 1

    # -----------------------------------------------------------------------
    # 15. Annotated trace fields: nodeType and status
    # -----------------------------------------------------------------------
    def test_evaluate_rule_trace_node_type_and_status(
        self, auth_client, organization, workspace
    ):
        """nodeType and status are annotated from root spans.
        Filtering by these computed fields must work."""
        from tracer.models.observation_span import ObservationSpan

        project = _create_project(organization, workspace, name="Annotated Project")
        t1 = _create_trace(project, name="chain-trace")
        t2 = _create_trace(project, name="llm-trace")

        # Create root spans with different types/statuses
        ObservationSpan.objects.create(
            id=str(uuid.uuid4()),
            trace=t1,
            name="root-1",
            observation_type="chain",
            status="OK",
            project=project,
            parent_span_id=None,
        )
        ObservationSpan.objects.create(
            id=str(uuid.uuid4()),
            trace=t2,
            name="root-2",
            observation_type="llm",
            status="ERROR",
            project=project,
            parent_span_id=None,
        )

        queue_id = _create_queue(auth_client, name="NodeType Q1")
        AnnotationQueue.objects.filter(pk=queue_id).update(project=project)

        # Filter by nodeType = chain
        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "Chain only",
                "source_type": "trace",
                "conditions": {
                    "rules": [
                        {"field": "nodeType", "op": "equals", "value": "chain"},
                    ]
                },
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]
        resp = auth_client.post(
            f"{_rule_detail_url(queue_id, rule_id)}evaluate/", format="json"
        )
        result = resp.data.get("result", resp.data)
        assert result["matched"] == 1
        assert result["added"] == 1

        # Filter by status = ERROR
        queue_id2 = _create_queue(auth_client, name="Status Q1")
        AnnotationQueue.objects.filter(pk=queue_id2).update(project=project)

        resp = auth_client.post(
            _rules_url(queue_id2),
            {
                "name": "Errors only",
                "source_type": "trace",
                "conditions": {
                    "rules": [
                        {"field": "status", "op": "equals", "value": "ERROR"},
                    ]
                },
                "enabled": True,
            },
            format="json",
        )
        rule_id2 = resp.data["id"]
        resp = auth_client.post(
            f"{_rule_detail_url(queue_id2, rule_id2)}evaluate/", format="json"
        )
        result = resp.data.get("result", resp.data)
        assert result["matched"] == 1
        assert result["added"] == 1

    # -----------------------------------------------------------------------
    # 16. Span source type with camelCase filters
    # -----------------------------------------------------------------------
    def test_evaluate_rule_span_source_with_filters(
        self, auth_client, organization, workspace
    ):
        """Span rules should filter by observation_type via nodeType mapping,
        and traceName should resolve to trace__name."""
        from tracer.models.observation_span import ObservationSpan

        project = _create_project(organization, workspace, name="Span Project")
        t1 = _create_trace(project, name="my-trace")
        t2 = _create_trace(project, name="other-trace")

        ObservationSpan.objects.create(
            id=str(uuid.uuid4()),
            trace=t1,
            name="span-1",
            observation_type="llm",
            status="OK",
            project=project,
            parent_span_id=None,
        )
        ObservationSpan.objects.create(
            id=str(uuid.uuid4()),
            trace=t1,
            name="span-2",
            observation_type="tool",
            status="OK",
            project=project,
            parent_span_id=None,
        )
        ObservationSpan.objects.create(
            id=str(uuid.uuid4()),
            trace=t2,
            name="span-3",
            observation_type="llm",
            status="ERROR",
            project=project,
            parent_span_id=None,
        )

        queue_id = _create_queue(auth_client, name="Span Q1")
        AnnotationQueue.objects.filter(pk=queue_id).update(project=project)

        # Filter spans by nodeType = llm
        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "LLM spans only",
                "source_type": "observation_span",
                "conditions": {
                    "rules": [
                        {"field": "nodeType", "op": "equals", "value": "llm"},
                    ]
                },
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]
        resp = auth_client.post(
            f"{_rule_detail_url(queue_id, rule_id)}evaluate/", format="json"
        )
        result = resp.data.get("result", resp.data)
        assert result["matched"] == 2  # span-1 and span-3
        assert result["added"] == 2

        # Filter spans by traceName
        queue_id2 = _create_queue(auth_client, name="Span TraceName Q1")
        AnnotationQueue.objects.filter(pk=queue_id2).update(project=project)

        resp = auth_client.post(
            _rules_url(queue_id2),
            {
                "name": "Spans from my-trace",
                "source_type": "observation_span",
                "conditions": {
                    "rules": [
                        {"field": "traceName", "op": "equals", "value": "my-trace"},
                    ]
                },
                "enabled": True,
            },
            format="json",
        )
        rule_id2 = resp.data["id"]
        resp = auth_client.post(
            f"{_rule_detail_url(queue_id2, rule_id2)}evaluate/", format="json"
        )
        result = resp.data.get("result", resp.data)
        assert result["matched"] == 2  # span-1 and span-2
        assert result["added"] == 2

    # -----------------------------------------------------------------------
    # 17. Session source type with computed filters
    # -----------------------------------------------------------------------
    def test_evaluate_rule_session_source(self, auth_client, organization, workspace):
        """Session rules should work with basic evaluation and projectName."""
        from tracer.models.trace_session import TraceSession

        project = _create_project(organization, workspace, name="Session Project")
        s1 = TraceSession.objects.create(project=project, name="session-1")
        s2 = TraceSession.objects.create(project=project, name="session-2")

        queue_id = _create_queue(auth_client, name="Session Q1")
        AnnotationQueue.objects.filter(pk=queue_id).update(project=project)

        # No conditions — should match all sessions
        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "All sessions",
                "source_type": "trace_session",
                "conditions": {},
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]
        resp = auth_client.post(
            f"{_rule_detail_url(queue_id, rule_id)}evaluate/", format="json"
        )
        result = resp.data.get("result", resp.data)
        assert result["matched"] == 2
        assert result["added"] == 2

    # -----------------------------------------------------------------------
    # 18. Session projectName filter
    # -----------------------------------------------------------------------
    def test_evaluate_rule_session_project_name(
        self, auth_client, organization, workspace
    ):
        """Session rules with projectName filter."""
        from tracer.models.trace_session import TraceSession

        proj_a = _create_project(organization, workspace, name="SessionProjA")
        proj_b = _create_project(organization, workspace, name="SessionProjB")
        TraceSession.objects.create(project=proj_a, name="s-a")
        TraceSession.objects.create(project=proj_b, name="s-b")

        queue_id = _create_queue(auth_client, name="Session ProjName Q1")

        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "Sessions in ProjA",
                "source_type": "trace_session",
                "conditions": {
                    "rules": [
                        {
                            "field": "projectName",
                            "op": "equals",
                            "value": "SessionProjA",
                        },
                    ]
                },
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]
        resp = auth_client.post(
            f"{_rule_detail_url(queue_id, rule_id)}evaluate/", format="json"
        )
        result = resp.data.get("result", resp.data)
        assert result["matched"] == 1
        assert result["added"] == 1

    # -----------------------------------------------------------------------
    # 19. Session computed fields (totalCost, startTime)
    # -----------------------------------------------------------------------
    def test_evaluate_rule_session_computed_fields(
        self, auth_client, organization, workspace
    ):
        """Session computed fields (totalCost, startTime) are annotated
        from span aggregates and should be filterable."""
        from tracer.models.observation_span import ObservationSpan
        from tracer.models.trace_session import TraceSession

        project = _create_project(organization, workspace, name="SessComp Project")
        s1 = TraceSession.objects.create(project=project, name="expensive-session")
        s2 = TraceSession.objects.create(project=project, name="cheap-session")

        # Create traces in each session
        t1 = _create_trace(project, name="s1-trace")
        t1.session = s1
        t1.save(update_fields=["session"])
        t2 = _create_trace(project, name="s2-trace")
        t2.session = s2
        t2.save(update_fields=["session"])

        now = timezone.now()
        # Expensive session spans
        ObservationSpan.objects.create(
            id=str(uuid.uuid4()),
            trace=t1,
            name="expensive-span",
            observation_type="llm",
            project=project,
            cost=5.0,
            start_time=now - timedelta(hours=1),
            end_time=now,
        )
        # Cheap session spans
        ObservationSpan.objects.create(
            id=str(uuid.uuid4()),
            trace=t2,
            name="cheap-span",
            observation_type="llm",
            project=project,
            cost=0.01,
            start_time=now - timedelta(minutes=5),
            end_time=now,
        )

        queue_id = _create_queue(auth_client, name="SessComp Q1")
        AnnotationQueue.objects.filter(pk=queue_id).update(project=project)

        # Filter sessions with totalCost > 1.0
        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "Expensive sessions",
                "source_type": "trace_session",
                "conditions": {
                    "rules": [
                        {
                            "field": "totalCost",
                            "op": "greater_than",
                            "value": "1.0",
                        },
                    ]
                },
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]
        resp = auth_client.post(
            f"{_rule_detail_url(queue_id, rule_id)}evaluate/", format="json"
        )
        result = resp.data.get("result", resp.data)
        assert result["matched"] == 1
        assert result["added"] == 1

    # -----------------------------------------------------------------------
    # 20. Simulation (call_execution) source with filters
    # -----------------------------------------------------------------------
    def test_evaluate_rule_simulation_source(
        self, auth_client, organization, workspace
    ):
        """CallExecution rules should filter by status and callType."""
        from simulate.models import AgentDefinition, Scenarios
        from simulate.models.run_test import RunTest
        from simulate.models.simulator_agent import SimulatorAgent
        from simulate.models.test_execution import CallExecution, TestExecution

        # Build the FK chain: AgentDefinition → SimulatorAgent → RunTest →
        #   TestExecution → CallExecution
        agent_def = AgentDefinition.objects.create(
            agent_name="Test Agent",
            agent_type=AgentDefinition.AgentTypeChoices.VOICE,
            contact_number="+1234567890",
            inbound=True,
            description="Test agent",
            organization=organization,
            workspace=workspace,
            languages=["en"],
        )
        sim_agent = SimulatorAgent.objects.create(
            name="Test Sim Agent",
            prompt="You are a test sim agent.",
            voice_provider="elevenlabs",
            voice_name="marissa",
            model="gpt-4",
            organization=organization,
            workspace=workspace,
        )
        ds = Dataset.objects.create(
            name="Sim DS",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.SCENARIO.value,
        )
        col = Column.objects.create(
            dataset=ds,
            name="situation",
            data_type="text",
            source=SourceChoices.OTHERS.value,
        )
        row = Row.objects.create(dataset=ds, order=0)
        Cell.objects.create(dataset=ds, column=col, row=row, value="Test sit")
        scenario = Scenarios.objects.create(
            name="Test Scenario",
            description="desc",
            source="test",
            scenario_type=Scenarios.ScenarioTypes.DATASET,
            organization=organization,
            workspace=workspace,
            dataset=ds,
            agent_definition=agent_def,
            status=StatusType.COMPLETED.value,
        )
        run_test = RunTest.objects.create(
            name="Test Run",
            description="desc",
            agent_definition=agent_def,
            simulator_agent=sim_agent,
            organization=organization,
            workspace=workspace,
        )
        run_test.scenarios.add(scenario)
        test_exec = TestExecution.objects.create(
            run_test=run_test,
            status=TestExecution.ExecutionStatus.PENDING,
            total_scenarios=1,
            total_calls=3,
            simulator_agent=sim_agent,
            agent_definition=agent_def,
        )

        # Create call executions with different statuses and types
        CallExecution.objects.create(
            test_execution=test_exec,
            scenario=scenario,
            status="completed",
            simulation_call_type="voice",
        )
        CallExecution.objects.create(
            test_execution=test_exec,
            scenario=scenario,
            status="failed",
            simulation_call_type="voice",
        )
        CallExecution.objects.create(
            test_execution=test_exec,
            scenario=scenario,
            status="completed",
            simulation_call_type="text",
        )

        queue_id = _create_queue(auth_client, name="Sim Q1")
        AnnotationQueue.objects.filter(pk=queue_id).update(agent_definition=agent_def)

        # Filter by status = completed
        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "Completed calls",
                "source_type": "call_execution",
                "conditions": {
                    "rules": [
                        {"field": "status", "op": "equals", "value": "completed"},
                    ]
                },
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]
        resp = auth_client.post(
            f"{_rule_detail_url(queue_id, rule_id)}evaluate/", format="json"
        )
        result = resp.data.get("result", resp.data)
        assert result["matched"] == 2
        assert result["added"] == 2

        # Filter by callType = voice
        queue_id2 = _create_queue(auth_client, name="Sim CallType Q1")
        AnnotationQueue.objects.filter(pk=queue_id2).update(agent_definition=agent_def)

        resp = auth_client.post(
            _rules_url(queue_id2),
            {
                "name": "Voice calls",
                "source_type": "call_execution",
                "conditions": {
                    "rules": [
                        {"field": "callType", "op": "equals", "value": "voice"},
                    ]
                },
                "enabled": True,
            },
            format="json",
        )
        rule_id2 = resp.data["id"]
        resp = auth_client.post(
            f"{_rule_detail_url(queue_id2, rule_id2)}evaluate/", format="json"
        )
        result = resp.data.get("result", resp.data)
        assert result["matched"] == 2
        assert result["added"] == 2

    # -----------------------------------------------------------------------
    # 21. Dataset row with camelCase filters
    # -----------------------------------------------------------------------
    def test_evaluate_rule_dataset_row_camelcase(
        self, auth_client, organization, workspace
    ):
        """Dataset row rules with camelCase field IDs (datasetName)."""
        ds1 = Dataset.objects.create(
            name="FilterableDS", organization=organization, workspace=workspace
        )
        ds2 = Dataset.objects.create(
            name="OtherDS", organization=organization, workspace=workspace
        )
        Row.objects.create(dataset=ds1, order=1, metadata={})
        Row.objects.create(dataset=ds2, order=1, metadata={})

        queue_id = _create_queue(auth_client, name="DS Camel Q1")

        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "FilterableDS rows",
                "source_type": "dataset_row",
                "conditions": {
                    "rules": [
                        {
                            "field": "datasetName",
                            "op": "equals",
                            "value": "FilterableDS",
                        },
                    ]
                },
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]
        resp = auth_client.post(
            f"{_rule_detail_url(queue_id, rule_id)}evaluate/", format="json"
        )
        result = resp.data.get("result", resp.data)
        assert result["matched"] == 1
        assert result["added"] == 1

    # -----------------------------------------------------------------------
    # 22. Dataset row filter payload from rules UI
    # -----------------------------------------------------------------------
    def test_evaluate_rule_dataset_filter_payload(
        self, auth_client, organization, workspace
    ):
        """Dataset rule filters support DevelopFilterRow-style column filters."""
        dataset = Dataset.objects.create(
            name="Dataset Filter Payload",
            organization=organization,
            workspace=workspace,
        )
        column = Column.objects.create(
            name="quality",
            data_type="text",
            dataset=dataset,
            source=SourceChoices.OTHERS.value,
        )
        row_match = Row.objects.create(dataset=dataset, order=1, metadata={})
        row_skip = Row.objects.create(dataset=dataset, order=2, metadata={})
        Cell.objects.create(
            dataset=dataset,
            row=row_match,
            column=column,
            value="very good",
        )
        Cell.objects.create(
            dataset=dataset,
            row=row_skip,
            column=column,
            value="bad",
        )

        queue_id = _create_queue(auth_client, name="Dataset filter payload Q")
        AnnotationQueue.objects.filter(pk=queue_id).update(dataset=dataset)

        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "Good rows",
                "source_type": "dataset_row",
                "conditions": {
                    "operator": "and",
                    "rules": [],
                    "filter": [
                        {
                            "column_id": str(column.id),
                            "filter_config": {
                                "filter_type": "text",
                                "filter_op": "contains",
                                "filter_value": "good",
                            },
                        }
                    ],
                    "scope": {"dataset_id": str(dataset.id)},
                },
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]
        resp = auth_client.post(
            f"{_rule_detail_url(queue_id, rule_id)}evaluate/", format="json"
        )
        result = resp.data.get("result", resp.data)
        assert result["matched"] == 1
        assert result["added"] == 1
        assert (
            QueueItem.objects.filter(queue_id=queue_id).first().dataset_row_id
            == row_match.id
        )

    def test_evaluate_rule_trace_observe_filter_payload(
        self, auth_client, organization, workspace, user
    ):
        """Trace rules support Observe filters for span attrs, evals, annotations."""
        from model_hub.models.evals_metric import EvalTemplate
        from model_hub.models.score import Score
        from tracer.models.custom_eval_config import CustomEvalConfig
        from tracer.models.observation_span import EvalLogger, ObservationSpan

        project = _create_project(organization, workspace, name="Trace Filter Rule")
        label = _create_label(organization, workspace, name="trace_rule_quality", label_type="numeric")
        template = EvalTemplate.objects.create(
            name="trace_rule_eval",
            organization=organization,
            workspace=workspace,
        )
        config = CustomEvalConfig.objects.create(
            name="Trace Rule Eval",
            eval_template=template,
            project=project,
        )
        match = _create_trace(project, "match-trace")
        skip = _create_trace(project, "skip-trace")
        match_span = ObservationSpan.objects.create(
            id=f"rule-root-{match.id.hex}",
            project=project,
            trace=match,
            name="match-root",
            observation_type="chain",
            span_attributes={"customer_tier": "vip"},
            parent_span_id=None,
        )
        skip_span = ObservationSpan.objects.create(
            id=f"rule-root-{skip.id.hex}",
            project=project,
            trace=skip,
            name="skip-root",
            observation_type="chain",
            span_attributes={"customer_tier": "free"},
            parent_span_id=None,
        )
        EvalLogger.objects.create(
            trace=match,
            observation_span=match_span,
            custom_eval_config=config,
            output_float=0.93,
        )
        EvalLogger.objects.create(
            trace=skip,
            observation_span=skip_span,
            custom_eval_config=config,
            output_float=0.95,
        )
        Score.objects.create(
            source_type="trace",
            trace=match,
            label=label,
            value={"value": 91},
            annotator=user,
            organization=organization,
            workspace=workspace,
        )
        Score.objects.create(
            source_type="trace",
            trace=skip,
            label=label,
            value={"value": 95},
            annotator=user,
            organization=organization,
            workspace=workspace,
        )

        queue_id = _create_queue(auth_client, name="Trace observe filter rule")
        AnnotationQueue.objects.filter(pk=queue_id).update(project=project)
        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "VIP high quality traces",
                "source_type": "trace",
                "conditions": {
                    "operator": "and",
                    "rules": [],
                    "filter": [
                        {
                            "column_id": "customer_tier",
                            "filter_config": {
                                "filter_type": "text",
                                "filter_op": "equals",
                                "filter_value": "vip",
                                "col_type": "SPAN_ATTRIBUTE",
                            },
                        },
                        {
                            "column_id": str(config.id),
                            "filter_config": {
                                "filter_type": "number",
                                "filter_op": "greater_than_or_equal",
                                "filter_value": 80,
                                "col_type": "EVAL_METRIC",
                            },
                        },
                        {
                            "column_id": str(label.id),
                            "filter_config": {
                                "filter_type": "number",
                                "filter_op": "greater_than",
                                "filter_value": 80,
                                "col_type": "ANNOTATION",
                            },
                        },
                    ],
                    "scope": {"project_id": str(project.id)},
                },
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]
        resp = auth_client.post(
            f"{_rule_detail_url(queue_id, rule_id)}evaluate/", format="json"
        )

        result = resp.data.get("result", resp.data)
        assert result["matched"] == 1
        assert result["added"] == 1
        assert QueueItem.objects.get(queue_id=queue_id).trace_id == match.id

    def test_evaluate_rule_trace_eval_choice_multiselect_filter(
        self, auth_client, organization, workspace
    ):
        """Trace rules must honor eval choice filters sent as multi-select `in`.

        This is the payload produced when a user checks one or more choices in
        the Observe-style filter picker. It must not collapse to zero matches.
        """
        from model_hub.models.evals_metric import EvalTemplate
        from tracer.models.custom_eval_config import CustomEvalConfig
        from tracer.models.observation_span import EvalLogger, ObservationSpan

        project = _create_project(organization, workspace, name="Trace Choice Rule")
        template = EvalTemplate.objects.create(
            name="Trace Choice Eval",
            organization=organization,
            workspace=workspace,
            config={"output": "choices"},
            choices=["Fast", "Slow"],
        )
        config = CustomEvalConfig.objects.create(
            name="Trace Choice Config",
            eval_template=template,
            project=project,
        )
        match = _create_trace(project, "choice-match")
        skip = _create_trace(project, "choice-skip")
        match_span = ObservationSpan.objects.create(
            id=f"choice-root-{match.id.hex}",
            project=project,
            trace=match,
            name="choice-match-root",
            observation_type="chain",
            parent_span_id=None,
        )
        skip_span = ObservationSpan.objects.create(
            id=f"choice-root-{skip.id.hex}",
            project=project,
            trace=skip,
            name="choice-skip-root",
            observation_type="chain",
            parent_span_id=None,
        )
        EvalLogger.objects.create(
            trace=match,
            observation_span=match_span,
            custom_eval_config=config,
            output_str_list=["Fast"],
        )
        EvalLogger.objects.create(
            trace=skip,
            observation_span=skip_span,
            custom_eval_config=config,
            output_str_list=["Slow"],
        )

        queue_id = _create_queue(auth_client, name="Trace choice filter rule")
        AnnotationQueue.objects.filter(pk=queue_id).update(project=project)
        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "Fast traces",
                "source_type": "trace",
                "conditions": {
                    "operator": "and",
                    "rules": [],
                    "filter": [
                        {
                            "column_id": str(config.id),
                            "filter_config": {
                                "filter_type": "categorical",
                                "filter_op": "in",
                                "filter_value": ["Fast"],
                                "col_type": "EVAL_METRIC",
                            },
                        },
                    ],
                    "scope": {"project_id": str(project.id)},
                },
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]
        resp = auth_client.post(
            f"{_rule_detail_url(queue_id, rule_id)}evaluate/", format="json"
        )

        result = resp.data.get("result", resp.data)
        assert result["matched"] == 1
        assert result["added"] == 1
        assert QueueItem.objects.get(queue_id=queue_id).trace_id == match.id

    def test_clickhouse_eval_choice_filter_accepts_config_id_and_multiselect(
        self, organization, workspace
    ):
        """The CH fallback path must not turn eval choice `in` filters into 0=1."""
        from model_hub.models.evals_metric import EvalTemplate
        from tracer.models.custom_eval_config import CustomEvalConfig
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        project = _create_project(organization, workspace, name="CH Choice Rule")
        template = EvalTemplate.objects.create(
            name="CH Choice Eval",
            organization=organization,
            workspace=workspace,
            config={"output": "choices"},
            choices=["Fast", "Slow"],
        )
        config = CustomEvalConfig.objects.create(
            name="CH Choice Config",
            eval_template=template,
            project=project,
        )

        builder = ClickHouseFilterBuilder(table="spans")
        where, params = builder.translate(
            [
                {
                    "column_id": str(config.id),
                    "filter_config": {
                        "filter_type": "categorical",
                        "filter_op": "in",
                        "filter_value": ["Fast", "Slow"],
                        "col_type": "EVAL_METRIC",
                    },
                }
            ]
        )

        assert "0 = 1" not in where
        assert "custom_eval_config_id IN" in where
        assert "has(JSONExtract(output_str_list, 'Array(String)')" in where
        assert tuple(params["eval_cfg_1"]) == (str(config.id),)

    def test_evaluate_rule_voice_trace_scope_has_no_implicit_time_window(
        self, auth_client, organization, workspace
    ):
        """Voice rules should scan all matching calls unless date is explicit."""
        from tracer.models.observation_span import ObservationSpan
        from tracer.models.trace import Trace

        project = _create_project(organization, workspace, name="Voice All Time Rule")
        old_time = timezone.now() - timedelta(days=4000)
        old_trace = _create_trace(project, "old-voice-call")
        new_trace = _create_trace(project, "new-voice-call")
        old_span = ObservationSpan.objects.create(
            id=str(uuid.uuid4()),
            project=project,
            trace=old_trace,
            name="old-root",
            observation_type="conversation",
            parent_span_id=None,
            start_time=old_time,
            end_time=old_time + timedelta(minutes=1),
        )
        ObservationSpan.objects.create(
            id=str(uuid.uuid4()),
            project=project,
            trace=new_trace,
            name="new-root",
            observation_type="conversation",
            parent_span_id=None,
            start_time=timezone.now(),
            end_time=timezone.now() + timedelta(minutes=1),
        )
        Trace.objects.filter(id=old_trace.id).update(created_at=old_time)
        ObservationSpan.objects.filter(id=old_span.id).update(created_at=old_time)

        queue_id = _create_queue(auth_client, name="Voice all time rule")
        AnnotationQueue.objects.filter(pk=queue_id).update(project=project)
        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "All voice calls",
                "source_type": "trace",
                "conditions": {
                    "operator": "and",
                    "rules": [],
                    "filter": [],
                    "scope": {
                        "project_id": str(project.id),
                        "is_voice_call": True,
                    },
                },
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]

        with patch(
            "model_hub.services.bulk_selection._resolve_voice_call_ids_clickhouse",
            side_effect=AssertionError("rules without date must not use CH time range"),
        ):
            resp = auth_client.post(
                f"{_rule_detail_url(queue_id, rule_id)}evaluate/", format="json"
            )

        result = resp.data.get("result", resp.data)
        assert result["matched"] == 2
        assert result["added"] == 2
        assert set(
            QueueItem.objects.filter(queue_id=queue_id).values_list(
                "trace_id", flat=True
            )
        ) == {old_trace.id, new_trace.id}

    def test_evaluate_rule_span_observe_filter_payload(
        self, auth_client, organization, workspace, user
    ):
        """Span rules support the same Observe-style filter payloads."""
        from model_hub.models.score import Score
        from tracer.models.observation_span import ObservationSpan

        project = _create_project(organization, workspace, name="Span Filter Rule")
        trace = _create_trace(project, "span-rule-trace")
        label = _create_label(organization, workspace, name="span_rule_quality", label_type="numeric")
        match = ObservationSpan.objects.create(
            id=str(uuid.uuid4()),
            project=project,
            trace=trace,
            name="match-span",
            observation_type="llm",
            span_attributes={"customer_tier": "vip"},
            parent_span_id=None,
        )
        skip = ObservationSpan.objects.create(
            id=str(uuid.uuid4()),
            project=project,
            trace=trace,
            name="skip-span",
            observation_type="tool",
            span_attributes={"customer_tier": "free"},
            parent_span_id=None,
        )
        Score.objects.create(
            source_type="observation_span",
            observation_span=match,
            label=label,
            value={"value": 92},
            annotator=user,
            organization=organization,
            workspace=workspace,
        )
        Score.objects.create(
            source_type="observation_span",
            observation_span=skip,
            label=label,
            value={"value": 96},
            annotator=user,
            organization=organization,
            workspace=workspace,
        )

        queue_id = _create_queue(auth_client, name="Span observe filter rule")
        AnnotationQueue.objects.filter(pk=queue_id).update(project=project)
        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "VIP spans",
                "source_type": "observation_span",
                "conditions": {
                    "operator": "and",
                    "rules": [],
                    "filter": [
                        {
                            "column_id": "customer_tier",
                            "filter_config": {
                                "filter_type": "text",
                                "filter_op": "equals",
                                "filter_value": "vip",
                                "col_type": "SPAN_ATTRIBUTE",
                            },
                        },
                        {
                            "column_id": str(label.id),
                            "filter_config": {
                                "filter_type": "number",
                                "filter_op": "greater_than",
                                "filter_value": 80,
                                "col_type": "ANNOTATION",
                            },
                        },
                    ],
                    "scope": {"project_id": str(project.id)},
                },
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]
        resp = auth_client.post(
            f"{_rule_detail_url(queue_id, rule_id)}evaluate/", format="json"
        )

        result = resp.data.get("result", resp.data)
        assert result["matched"] == 1
        assert result["added"] == 1
        assert QueueItem.objects.get(queue_id=queue_id).observation_span_id == match.id

    def test_evaluate_rule_span_eval_choice_multiselect_filter(
        self, auth_client, organization, workspace
    ):
        """Span rules must honor eval choice filters sent as multi-select `in`."""
        from model_hub.models.evals_metric import EvalTemplate
        from tracer.models.custom_eval_config import CustomEvalConfig
        from tracer.models.observation_span import EvalLogger, ObservationSpan

        project = _create_project(organization, workspace, name="Span Choice Rule")
        trace = _create_trace(project, "span-choice-trace")
        template = EvalTemplate.objects.create(
            name="Span Choice Eval",
            organization=organization,
            workspace=workspace,
            config={"output": "choices"},
            choices=["Fast", "Slow"],
        )
        config = CustomEvalConfig.objects.create(
            name="Span Choice Config",
            eval_template=template,
            project=project,
        )
        match = ObservationSpan.objects.create(
            id=str(uuid.uuid4()),
            project=project,
            trace=trace,
            name="span-choice-match",
            observation_type="llm",
            parent_span_id=None,
        )
        skip = ObservationSpan.objects.create(
            id=str(uuid.uuid4()),
            project=project,
            trace=trace,
            name="span-choice-skip",
            observation_type="tool",
            parent_span_id=None,
        )
        EvalLogger.objects.create(
            trace=trace,
            observation_span=match,
            custom_eval_config=config,
            output_str_list=["Fast"],
        )
        EvalLogger.objects.create(
            trace=trace,
            observation_span=skip,
            custom_eval_config=config,
            output_str_list=["Slow"],
        )

        queue_id = _create_queue(auth_client, name="Span choice filter rule")
        AnnotationQueue.objects.filter(pk=queue_id).update(project=project)
        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "Fast spans",
                "source_type": "observation_span",
                "conditions": {
                    "operator": "and",
                    "rules": [],
                    "filter": [
                        {
                            "column_id": str(config.id),
                            "filter_config": {
                                "filter_type": "categorical",
                                "filter_op": "in",
                                "filter_value": ["Fast"],
                                "col_type": "EVAL_METRIC",
                            },
                        },
                    ],
                    "scope": {"project_id": str(project.id)},
                },
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]
        resp = auth_client.post(
            f"{_rule_detail_url(queue_id, rule_id)}evaluate/", format="json"
        )

        result = resp.data.get("result", resp.data)
        assert result["matched"] == 1
        assert result["added"] == 1
        assert QueueItem.objects.get(queue_id=queue_id).observation_span_id == match.id

    # -----------------------------------------------------------------------
    # 23. Filter-mode scope without explicit filter rows
    # -----------------------------------------------------------------------
    def test_evaluate_rule_trace_filter_scope_without_filter_rows(
        self, auth_client, organization, workspace
    ):
        """Rules UI can save scope with an empty filter array; scope must apply."""
        project1 = _create_project(organization, workspace, name="Scope Only One")
        project2 = _create_project(organization, workspace, name="Scope Only Two")
        _create_trace(project1, "scope-only-1")
        _create_trace(project1, "scope-only-2")
        _create_trace(project2, "scope-only-other")

        queue_id = _create_queue(auth_client, name="Trace scope-only Q")

        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "Scoped empty filter",
                "source_type": "trace",
                "conditions": {
                    "operator": "and",
                    "rules": [],
                    "filter": [],
                    "scope": {"project_id": str(project1.id)},
                },
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]
        resp = auth_client.post(
            f"{_rule_detail_url(queue_id, rule_id)}evaluate/", format="json"
        )
        result = resp.data.get("result", resp.data)
        assert result["matched"] == 2
        assert result["added"] == 2
        assert QueueItem.objects.filter(
            queue_id=queue_id,
            trace__project=project2,
            deleted=False,
        ).count() == 0

    def test_evaluate_rule_dataset_filter_scope_without_filter_rows(
        self, auth_client, organization, workspace
    ):
        """Dataset scope must still apply when the filter list is empty."""
        ds1 = Dataset.objects.create(
            name="Scope Dataset One", organization=organization, workspace=workspace
        )
        ds2 = Dataset.objects.create(
            name="Scope Dataset Two", organization=organization, workspace=workspace
        )
        Row.objects.create(dataset=ds1, order=1, metadata={})
        Row.objects.create(dataset=ds1, order=2, metadata={})
        Row.objects.create(dataset=ds2, order=1, metadata={})

        queue_id = _create_queue(auth_client, name="Dataset scope-only Q")

        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "Dataset scoped empty filter",
                "source_type": "dataset_row",
                "conditions": {
                    "operator": "and",
                    "rules": [],
                    "filter": [],
                    "scope": {"dataset_id": str(ds1.id)},
                },
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]
        resp = auth_client.post(
            f"{_rule_detail_url(queue_id, rule_id)}evaluate/", format="json"
        )
        result = resp.data.get("result", resp.data)
        assert result["matched"] == 2
        assert result["added"] == 2
        assert QueueItem.objects.filter(
            queue_id=queue_id,
            dataset_row__dataset=ds2,
            deleted=False,
        ).count() == 0

    # -----------------------------------------------------------------------
    # 24. Scheduled frequency evaluator
    # -----------------------------------------------------------------------
    def test_due_task_evaluates_hourly_rules(
        self, auth_client, organization, workspace
    ):
        project = _create_project(organization, workspace, name="Scheduled Project")
        _create_trace(project, "scheduled trace")
        queue_id = _create_queue(auth_client, name="Scheduled Rule Q")
        AnnotationQueue.objects.filter(pk=queue_id).update(project=project)

        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "Hourly trace intake",
                "source_type": "trace",
                "conditions": {},
                "enabled": True,
                "trigger_frequency": "hourly",
            },
            format="json",
        )
        rule_id = resp.data["id"]

        summary = run_due_automation_rules()
        assert summary["checked"] == 1
        assert summary["evaluated"] == 1
        assert summary["added"] == 1

        rule = AutomationRule.objects.get(pk=rule_id)
        assert rule.trigger_count == 1
        assert rule.last_triggered_at is not None

        second_summary = run_due_automation_rules()
        assert second_summary["checked"] == 1
        assert second_summary["evaluated"] == 0

    # -----------------------------------------------------------------------
    # 25. Serializer persists trigger frequency
    # -----------------------------------------------------------------------
    def test_create_rule_persists_trigger_frequency(
        self, auth_client, organization, workspace
    ):
        queue_id = _create_queue(auth_client, name="Frequency serializer Q")

        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "Weekly intake",
                "source_type": "trace",
                "conditions": {},
                "enabled": True,
                "trigger_frequency": AutomationRuleTriggerFrequency.WEEKLY.value,
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        assert resp.data["trigger_frequency"] == "weekly"
        assert resp.data["trigger_count"] == 0
        assert resp.data["last_triggered_at"] is None

        rule = AutomationRule.objects.get(pk=resp.data["id"])
        assert rule.trigger_frequency == AutomationRuleTriggerFrequency.WEEKLY.value

    def test_create_rule_rejects_unknown_trigger_frequency(
        self, auth_client, organization, workspace
    ):
        queue_id = _create_queue(auth_client, name="Bad frequency Q")

        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "Bad frequency",
                "source_type": "trace",
                "conditions": {},
                "enabled": True,
                "trigger_frequency": "every_minute",
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert AutomationRule.objects.filter(queue_id=queue_id).count() == 0

    # -----------------------------------------------------------------------
    # 26. Frequency due checks
    # -----------------------------------------------------------------------
    def test_is_automation_rule_due_respects_frequency_intervals(
        self, auth_client, organization, workspace
    ):
        queue_id = _create_queue(auth_client, name="Due interval Q")
        rule = AutomationRule.objects.create(
            queue_id=queue_id,
            organization=organization,
            name="Interval rule",
            source_type="trace",
            conditions={},
            enabled=True,
            trigger_frequency=AutomationRuleTriggerFrequency.MANUAL.value,
        )
        now = timezone.now()

        assert is_automation_rule_due(rule, now=now) is False

        rule.trigger_frequency = AutomationRuleTriggerFrequency.HOURLY.value
        rule.last_triggered_at = None
        assert is_automation_rule_due(rule, now=now) is True

        rule.last_triggered_at = now - timedelta(minutes=59)
        assert is_automation_rule_due(rule, now=now) is False

        rule.last_triggered_at = now - timedelta(hours=1, seconds=1)
        assert is_automation_rule_due(rule, now=now) is True

        rule.trigger_frequency = AutomationRuleTriggerFrequency.DAILY.value
        rule.last_triggered_at = now - timedelta(hours=23, minutes=59)
        assert is_automation_rule_due(rule, now=now) is False

        rule.last_triggered_at = now - timedelta(days=1, seconds=1)
        assert is_automation_rule_due(rule, now=now) is True

        rule.trigger_frequency = AutomationRuleTriggerFrequency.WEEKLY.value
        rule.last_triggered_at = now - timedelta(days=6, hours=23)
        assert is_automation_rule_due(rule, now=now) is False

        rule.last_triggered_at = now - timedelta(weeks=1, seconds=1)
        assert is_automation_rule_due(rule, now=now) is True

        rule.trigger_frequency = AutomationRuleTriggerFrequency.MONTHLY.value
        rule.last_triggered_at = now - timedelta(days=29, hours=23)
        assert is_automation_rule_due(rule, now=now) is False

        rule.last_triggered_at = now - timedelta(days=30, seconds=1)
        assert is_automation_rule_due(rule, now=now) is True

    # -----------------------------------------------------------------------
    # 27. Scheduled evaluator skips manual rules
    # -----------------------------------------------------------------------
    def test_due_task_skips_manual_rules(self, auth_client, organization, workspace):
        queue_id = _create_queue(auth_client, name="Manual skip Q")
        for name, frequency in (
            ("Manual rule", AutomationRuleTriggerFrequency.MANUAL.value),
            ("Hourly rule", AutomationRuleTriggerFrequency.HOURLY.value),
        ):
            resp = auth_client.post(
                _rules_url(queue_id),
                {
                    "name": name,
                    "source_type": "trace",
                    "conditions": {},
                    "enabled": True,
                    "trigger_frequency": frequency,
                },
                format="json",
            )
            assert resp.status_code == status.HTTP_201_CREATED, resp.data

        with patch(
            "model_hub.tasks.annotation_automation.evaluate_rule",
            return_value={"matched": 0, "added": 0, "duplicates": 0},
        ) as mocked_evaluate_rule:
            summary = run_due_automation_rules()

        assert summary["checked"] == 1
        assert summary["evaluated"] == 1
        assert mocked_evaluate_rule.call_count == 1
        assert mocked_evaluate_rule.call_args.args[0].name == "Hourly rule"

    # -----------------------------------------------------------------------
    # 28. Temporal schedule wiring
    # -----------------------------------------------------------------------
    def test_temporal_schedule_registered_for_annotation_rules(self, db):
        schedule = next(
            (
                item
                for item in MODEL_HUB_SCHEDULES
                if item.schedule_id == "annotation-automation-rules"
            ),
            None,
        )

        assert schedule is not None
        assert schedule.activity_name == "evaluate_due_automation_rules"
        assert schedule.interval_seconds == 3600
        assert schedule.queue == "default"

    # -----------------------------------------------------------------------
    # 29. Scheduled evaluator isolates per-rule failures
    # -----------------------------------------------------------------------
    def test_due_task_continues_after_rule_exception(
        self, auth_client, organization, workspace
    ):
        queue_id = _create_queue(auth_client, name="Scheduled exception Q")
        for name in ("Boom", "Still runs"):
            resp = auth_client.post(
                _rules_url(queue_id),
                {
                    "name": name,
                    "source_type": "trace",
                    "conditions": {},
                    "enabled": True,
                    "trigger_frequency": "hourly",
                },
                format="json",
            )
            assert resp.status_code == status.HTTP_201_CREATED

        with patch(
            "model_hub.tasks.annotation_automation.evaluate_rule",
            side_effect=[
                RuntimeError("boom"),
                {"matched": 1, "added": 1, "duplicates": 0},
            ],
        ):
            summary = run_due_automation_rules()

        assert summary["checked"] == 2
        assert summary["errors"] == 1
        assert summary["evaluated"] == 1
        assert summary["added"] == 1

    def test_evaluate_rule_returns_error_for_bad_dataset_filter(
        self, auth_client, organization, workspace
    ):
        dataset = Dataset.objects.create(
            name="Bad Dataset Filter",
            organization=organization,
            workspace=workspace,
        )
        column = Column.objects.create(
            name="score",
            data_type="float",
            dataset=dataset,
            source=SourceChoices.OTHERS.value,
        )
        Row.objects.create(dataset=dataset, order=1, metadata={})

        queue_id = _create_queue(auth_client, name="Bad dataset filter Q")
        AnnotationQueue.objects.filter(pk=queue_id).update(dataset=dataset)

        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "Bad numeric filter",
                "source_type": "dataset_row",
                "conditions": {
                    "operator": "and",
                    "rules": [],
                    "filter": [
                        {
                            "column_id": str(column.id),
                            "filter_config": {
                                "filter_type": "number",
                                "filter_op": "greater_than",
                                "filter_value": "not-a-number",
                            },
                        }
                    ],
                    "scope": {"dataset_id": str(dataset.id)},
                },
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]
        resp = auth_client.post(
            f"{_rule_detail_url(queue_id, rule_id)}evaluate/", format="json"
        )
        assert resp.status_code == status.HTTP_200_OK
        result = resp.data.get("result", resp.data)
        assert result["matched"] == 0
        assert result["added"] == 0
        assert result["error"]

    # -----------------------------------------------------------------------
    # 30. FIELD_MAPPING completeness — verify all source types have mappings
    # -----------------------------------------------------------------------
    def test_field_mapping_covers_all_source_types(self, db):
        """Every source type in SOURCE_MODEL_MAP must have a FIELD_MAPPING."""
        from model_hub.utils.annotation_queue_helpers import (
            FIELD_MAPPING,
            SOURCE_MODEL_MAP,
        )

        for source_type in SOURCE_MODEL_MAP:
            assert (
                source_type in FIELD_MAPPING
            ), f"FIELD_MAPPING missing for source_type={source_type}"
            assert (
                len(FIELD_MAPPING[source_type]) > 0
            ), f"FIELD_MAPPING for {source_type} is empty"

    # -----------------------------------------------------------------------
    # 31. Queue scope is authoritative — rule scope can't redirect inserts
    # -----------------------------------------------------------------------
    def test_rule_scope_cannot_override_queue_project(
        self, auth_client, organization, workspace
    ):
        """A rule that passes scope.project_id pointing at a different
        project than the queue's bound project must be rejected at
        evaluation time, not silently followed."""
        project_a = _create_project(organization, workspace, name="Bound Project A")
        project_b = _create_project(organization, workspace, name="Other Project B")
        _create_trace(project_b, name="b-trace-1")
        _create_trace(project_b, name="b-trace-2")

        queue_id = _create_queue(auth_client, name="Bound to A queue")
        AnnotationQueue.objects.filter(pk=queue_id).update(project=project_a)

        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "Cross-project rule",
                "source_type": "trace",
                "conditions": {
                    "filter": [],
                    "scope": {"project_id": str(project_b.id)},
                },
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]

        resp = auth_client.post(
            f"{_rule_detail_url(queue_id, rule_id)}evaluate/", format="json"
        )
        assert resp.status_code == status.HTTP_200_OK
        result = resp.data.get("result", resp.data)
        assert result["matched"] == 0
        assert result["added"] == 0
        assert "queue's bound project" in result.get("error", "")

    # -----------------------------------------------------------------------
    # 32. Queue scope authoritative for dataset_row source too
    # -----------------------------------------------------------------------
    def test_rule_scope_cannot_override_queue_dataset(
        self, auth_client, organization, workspace
    ):
        """Same invariant as test #31, applied to dataset-bound queues."""
        ds_a = Dataset.objects.create(
            name="DS Bound A",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.BUILD.value,
        )
        ds_b = Dataset.objects.create(
            name="DS Other B",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.BUILD.value,
        )
        Row.objects.create(dataset=ds_b, order=0)
        Row.objects.create(dataset=ds_b, order=1)

        queue_id = _create_queue(auth_client, name="Bound to DS A")
        AnnotationQueue.objects.filter(pk=queue_id).update(dataset=ds_a)

        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "Cross-dataset rule",
                "source_type": "dataset_row",
                "conditions": {
                    "filter": [],
                    "scope": {"dataset_id": str(ds_b.id)},
                },
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]

        resp = auth_client.post(
            f"{_rule_detail_url(queue_id, rule_id)}evaluate/", format="json"
        )
        assert resp.status_code == status.HTTP_200_OK
        result = resp.data.get("result", resp.data)
        assert result["matched"] == 0
        assert result["added"] == 0
        assert "queue's bound dataset" in result.get("error", "")

    # -----------------------------------------------------------------------
    # 33. Default queues can add from a selected source outside their default
    # -----------------------------------------------------------------------
    def test_default_queue_rule_scope_can_target_selected_project(
        self, auth_client, organization, workspace
    ):
        """Default queues are flexible: their bound project is only the
        automatic direct-annotation landing source, not a rule hard limit."""
        project_a = _create_project(organization, workspace, name="Default Project A")
        project_b = _create_project(organization, workspace, name="Selected Project B")
        match = _create_trace(project_b, name="default-queue-cross-project")

        queue_id = _create_queue(auth_client, name="Default bound to A")
        AnnotationQueue.objects.filter(pk=queue_id).update(
            project=project_a,
            is_default=True,
        )

        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "Selected project rule",
                "source_type": "trace",
                "conditions": {
                    "filter": [],
                    "scope": {"project_id": str(project_b.id)},
                },
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]

        resp = auth_client.post(
            f"{_rule_detail_url(queue_id, rule_id)}evaluate/", format="json"
        )
        assert resp.status_code == status.HTTP_200_OK
        result = resp.data.get("result", resp.data)
        assert result["matched"] == 1
        assert result["added"] == 1
        assert QueueItem.objects.get(queue_id=queue_id).trace_id == match.id

    def test_default_queue_rule_scope_can_target_selected_dataset(
        self, auth_client, organization, workspace
    ):
        ds_a = Dataset.objects.create(
            name="Default DS A",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.BUILD.value,
        )
        ds_b = Dataset.objects.create(
            name="Selected DS B",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.BUILD.value,
        )
        row = Row.objects.create(dataset=ds_b, order=0)

        queue_id = _create_queue(auth_client, name="Default bound to DS A")
        AnnotationQueue.objects.filter(pk=queue_id).update(
            dataset=ds_a,
            is_default=True,
        )

        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "Selected dataset rule",
                "source_type": "dataset_row",
                "conditions": {
                    "filter": [],
                    "scope": {"dataset_id": str(ds_b.id)},
                },
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]

        resp = auth_client.post(
            f"{_rule_detail_url(queue_id, rule_id)}evaluate/", format="json"
        )
        assert resp.status_code == status.HTTP_200_OK
        result = resp.data.get("result", resp.data)
        assert result["matched"] == 1
        assert result["added"] == 1
        assert QueueItem.objects.get(queue_id=queue_id).dataset_row_id == row.id

    def test_default_queue_rule_scope_can_target_selected_agent_definition(
        self, auth_client, organization, workspace
    ):
        from simulate.models import AgentDefinition, Scenarios
        from simulate.models.run_test import RunTest
        from simulate.models.simulator_agent import SimulatorAgent
        from simulate.models.test_execution import CallExecution, TestExecution

        agent_a = AgentDefinition.objects.create(
            agent_name="Default Agent A",
            agent_type=AgentDefinition.AgentTypeChoices.VOICE,
            contact_number="+10000000000",
            inbound=True,
            description="Default agent",
            organization=organization,
            workspace=workspace,
            languages=["en"],
        )
        agent_b = AgentDefinition.objects.create(
            agent_name="Selected Agent B",
            agent_type=AgentDefinition.AgentTypeChoices.VOICE,
            contact_number="+10000000001",
            inbound=True,
            description="Selected agent",
            organization=organization,
            workspace=workspace,
            languages=["en"],
        )
        sim_agent = SimulatorAgent.objects.create(
            name="Default Queue Sim Agent",
            prompt="You are a test sim agent.",
            voice_provider="elevenlabs",
            voice_name="marissa",
            model="gpt-4",
            organization=organization,
            workspace=workspace,
        )
        ds = Dataset.objects.create(
            name="Selected Agent Scenario DS",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.SCENARIO.value,
        )
        scenario = Scenarios.objects.create(
            name="Selected Agent Scenario",
            description="desc",
            source="test",
            scenario_type=Scenarios.ScenarioTypes.DATASET,
            organization=organization,
            workspace=workspace,
            dataset=ds,
            agent_definition=agent_b,
            status=StatusType.COMPLETED.value,
        )
        run_test = RunTest.objects.create(
            name="Selected Agent Run",
            description="desc",
            agent_definition=agent_b,
            simulator_agent=sim_agent,
            organization=organization,
            workspace=workspace,
        )
        run_test.scenarios.add(scenario)
        test_exec = TestExecution.objects.create(
            run_test=run_test,
            status=TestExecution.ExecutionStatus.PENDING,
            total_scenarios=1,
            total_calls=1,
            simulator_agent=sim_agent,
            agent_definition=agent_b,
        )
        call = CallExecution.objects.create(
            test_execution=test_exec,
            scenario=scenario,
            status="completed",
            simulation_call_type="voice",
        )

        queue_id = _create_queue(auth_client, name="Default bound to Agent A")
        AnnotationQueue.objects.filter(pk=queue_id).update(
            agent_definition=agent_a,
            is_default=True,
        )

        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "Selected agent rule",
                "source_type": "call_execution",
                "conditions": {
                    "filter": [],
                    "scope": {"project_id": str(agent_b.id)},
                },
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]

        resp = auth_client.post(
            f"{_rule_detail_url(queue_id, rule_id)}evaluate/", format="json"
        )
        assert resp.status_code == status.HTTP_200_OK
        result = resp.data.get("result", resp.data)
        assert result["matched"] == 1
        assert result["added"] == 1
        assert QueueItem.objects.get(queue_id=queue_id).call_execution_id == call.id

    # -----------------------------------------------------------------------
    # 34. Concurrent evaluators of the same rule don't double-add
    # -----------------------------------------------------------------------
    @pytest.mark.xfail(
        reason="Pre-existing: concurrent evaluators raise Organization "
        "DoesNotExist due to thread-local workspace context not being "
        "set on the spawned threads. Test infra issue, not a real backend "
        "race condition."
    )
    def test_concurrent_evaluators_serialise(
        self, auth_client, organization, workspace
    ):
        """select_for_update on the rule must serialise concurrent fires.
        Two simultaneous evaluations of the same rule are expected to add
        each matching row exactly once and not raise IntegrityError."""
        from threading import Thread
        from django.db import close_old_connections

        from model_hub.models.annotation_queues import AutomationRule, QueueItem
        from model_hub.utils.annotation_queue_helpers import evaluate_rule

        project = _create_project(organization, workspace, name="Race Project")
        for i in range(5):
            _create_trace(project, name=f"race-trace-{i}")

        queue_id = _create_queue(auth_client, name="Race Q")
        AnnotationQueue.objects.filter(pk=queue_id).update(project=project)

        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "Race rule",
                "source_type": "trace",
                "conditions": {"rules": []},
                "enabled": True,
            },
            format="json",
        )
        rule = AutomationRule.objects.get(pk=resp.data["id"])

        results: list[dict] = []
        errors: list[str] = []

        def fire():
            try:
                results.append(evaluate_rule(rule))
            except Exception as exc:  # pragma: no cover - shouldn't fire
                errors.append(repr(exc))
            finally:
                close_old_connections()

        threads = [Thread(target=fire) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"concurrent evaluators raised: {errors}"
        # Exactly one of the racers should report 5 added; the others see 0
        # added (because the locked-out runs hit "all matching items already
        # in queue"). The DB must end up with exactly 5 distinct queue
        # items either way.
        item_count = QueueItem.objects.filter(
            queue_id=queue_id, deleted=False
        ).count()
        assert item_count == 5, (
            f"expected 5 queue items, got {item_count}; results={results}"
        )

    # -----------------------------------------------------------------------
    # 34. call_execution filter-mode honours non-created_at filters
    # -----------------------------------------------------------------------
    def test_call_execution_filter_mode_status_filter(
        self, auth_client, organization, workspace
    ):
        """A simulation rule with a status=='completed' filter must only
        enqueue completed call executions (not all calls under the agent)."""
        from simulate.models import AgentDefinition, Scenarios
        from simulate.models.run_test import RunTest
        from simulate.models.simulator_agent import SimulatorAgent
        from simulate.models.test_execution import CallExecution, TestExecution

        agent_def = AgentDefinition.objects.create(
            agent_name="Filter Agent",
            agent_type=AgentDefinition.AgentTypeChoices.VOICE,
            contact_number="+1235550199",
            inbound=True,
            description="filter test",
            organization=organization,
            workspace=workspace,
            languages=["en"],
        )
        sim_agent = SimulatorAgent.objects.create(
            name="Filter Sim",
            prompt="x",
            voice_provider="elevenlabs",
            voice_name="marissa",
            model="gpt-4",
            organization=organization,
            workspace=workspace,
        )
        ds = Dataset.objects.create(
            name="FilterDS",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.SCENARIO.value,
        )
        scenario = Scenarios.objects.create(
            name="Filter Scenario",
            description="desc",
            source="test",
            scenario_type=Scenarios.ScenarioTypes.DATASET,
            organization=organization,
            workspace=workspace,
            dataset=ds,
            agent_definition=agent_def,
            status=StatusType.COMPLETED.value,
        )
        run_test = RunTest.objects.create(
            name="Filter Run",
            description="d",
            agent_definition=agent_def,
            simulator_agent=sim_agent,
            organization=organization,
            workspace=workspace,
        )
        run_test.scenarios.add(scenario)
        test_exec = TestExecution.objects.create(
            run_test=run_test,
            status=TestExecution.ExecutionStatus.PENDING,
            total_scenarios=1,
            total_calls=3,
            simulator_agent=sim_agent,
            agent_definition=agent_def,
        )
        CallExecution.objects.create(
            test_execution=test_exec, scenario=scenario,
            status="completed", simulation_call_type="voice",
        )
        CallExecution.objects.create(
            test_execution=test_exec, scenario=scenario,
            status="failed", simulation_call_type="voice",
        )
        CallExecution.objects.create(
            test_execution=test_exec, scenario=scenario,
            status="completed", simulation_call_type="text",
        )

        queue_id = _create_queue(auth_client, name="Filter Q")
        AnnotationQueue.objects.filter(pk=queue_id).update(
            agent_definition=agent_def
        )

        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "Status=completed",
                "source_type": "call_execution",
                "conditions": {
                    "filter": [
                        {
                            "column_id": "status",
                            "filter_config": {
                                "filter_type": "categorical",
                                "filter_op": "equals",
                                "filter_value": "completed",
                            },
                        }
                    ],
                    "scope": {},
                },
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]
        resp = auth_client.post(
            f"{_rule_detail_url(queue_id, rule_id)}evaluate/", format="json"
        )
        assert resp.status_code == status.HTTP_200_OK
        result = resp.data.get("result", resp.data)
        assert result["matched"] == 2, result
        assert result["added"] == 2, result

    # -----------------------------------------------------------------------
    # 35. call_execution filter-mode fails closed on unsupported field
    # -----------------------------------------------------------------------
    def test_call_execution_filter_mode_unsupported_column_fails(
        self, auth_client, organization, workspace
    ):
        from simulate.models import AgentDefinition

        agent_def = AgentDefinition.objects.create(
            agent_name="UnsupAgent",
            agent_type=AgentDefinition.AgentTypeChoices.VOICE,
            contact_number="+1235550299",
            inbound=True,
            description="x",
            organization=organization,
            workspace=workspace,
            languages=["en"],
        )
        queue_id = _create_queue(auth_client, name="Unsup Q")
        AnnotationQueue.objects.filter(pk=queue_id).update(
            agent_definition=agent_def
        )

        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "Unsupported col",
                "source_type": "call_execution",
                "conditions": {
                    "filter": [
                        {
                            "column_id": "totally_made_up_column",
                            "filter_config": {
                                "filter_type": "text",
                                "filter_op": "equals",
                                "filter_value": "x",
                            },
                        }
                    ],
                    "scope": {},
                },
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]
        resp = auth_client.post(
            f"{_rule_detail_url(queue_id, rule_id)}evaluate/", format="json"
        )
        assert resp.status_code == status.HTTP_200_OK
        result = resp.data.get("result", resp.data)
        assert result["matched"] == 0
        assert result["added"] == 0
        assert "totally_made_up_column" in result.get("error", "")


@pytest.mark.django_db
class TestAutomationRuleEvaluateAsyncContract:
    """Verifies the real 202 contract — bypasses the inline-evaluate fixture
    used by the legacy test class so we exercise the actual production path.
    """

    @pytest.fixture(autouse=True)
    def _allow_entitlements(self):
        with patch(
            "ee.usage.services.entitlements.Entitlements.can_create",
            return_value=SimpleNamespace(allowed=True),
        ):
            yield

    def test_evaluate_returns_202_with_workflow_id(
        self, auth_client, organization, workspace
    ):
        """Manual /evaluate must schedule async + return 202 when the rule's
        filter resolves to more than ``RULE_RUN_SYNC_THRESHOLD`` items.

        Below the threshold it runs inline (covered by other tests). To force
        the async path here without seeding thousands of traces we patch the
        threshold down to 0 so any non-empty match triggers it.
        """
        project = _create_project(organization, workspace, name="Async Project")
        # Two traces — anything > 0 trips the patched threshold.
        _create_trace(project, name="async-trace-1")
        _create_trace(project, name="async-trace-2")

        queue_id = _create_queue(auth_client, name="Async Queue")
        AnnotationQueue.objects.filter(pk=queue_id).update(project=project)

        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "Async rule",
                "source_type": "trace",
                "conditions": {},
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]

        with patch(
            "model_hub.utils.annotation_queue_helpers.RULE_RUN_SYNC_THRESHOLD",
            0,
        ), patch(
            "tfc.temporal.drop_in.runner.start_activity_sync",
            return_value="wf-test-12345",
        ) as mock_start:
            resp = auth_client.post(
                f"{_rule_detail_url(queue_id, rule_id)}evaluate/",
                format="json",
            )

        assert resp.status_code == status.HTTP_202_ACCEPTED
        assert resp.data["status"] == "scheduled"
        assert resp.data["workflow_id"] == "wf-test-12345"
        assert "email" in resp.data["message"].lower()

        mock_start.assert_called_once()
        call_kwargs = mock_start.call_args.kwargs
        assert call_kwargs["activity_name"] == "evaluate_rule_manual_async"
        assert call_kwargs["queue"] == "tasks_l"
        assert call_kwargs["kwargs"]["rule_id"] == rule_id
        assert call_kwargs["task_id"].startswith(
            f"automation-rule-eval-{rule_id}-"
        )

    def test_evaluate_small_run_returns_200_inline(
        self, auth_client, organization, workspace
    ):
        """Below ``RULE_RUN_SYNC_THRESHOLD`` the endpoint runs inline and
        returns 200 with the eval result — no Temporal scheduling, no email.
        """
        project = _create_project(organization, workspace, name="Sync Project")
        _create_trace(project, name="sync-trace-1")
        _create_trace(project, name="sync-trace-2")

        queue_id = _create_queue(auth_client, name="Sync Queue")
        AnnotationQueue.objects.filter(pk=queue_id).update(project=project)

        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "Sync rule",
                "source_type": "trace",
                "conditions": {},
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]

        with patch(
            "tfc.temporal.drop_in.runner.start_activity_sync"
        ) as mock_start:
            resp = auth_client.post(
                f"{_rule_detail_url(queue_id, rule_id)}evaluate/",
                format="json",
            )

        assert resp.status_code == status.HTTP_200_OK
        result = resp.data.get("result", resp.data)
        assert result["matched"] == 2
        assert result["added"] == 2
        assert result["duplicates"] == 0
        # Sync path must not touch Temporal at all.
        mock_start.assert_not_called()

    def test_evaluate_second_click_within_30s_returns_409(
        self, auth_client, organization, workspace
    ):
        """Rapid double-click on Run Now should 409, not fire a second run.

        Backend uses the rule's ``last_triggered_at`` as a 30s lockout. The
        QueueItem unique constraint already prevents data corruption, but
        without this guard a double-click on the async path would spawn two
        workflows + two completion emails, which is both confusing and
        wasteful.
        """
        project = _create_project(organization, workspace, name="Spam Project")
        _create_trace(project, name="spam-trace-1")

        queue_id = _create_queue(auth_client, name="Spam Queue")
        AnnotationQueue.objects.filter(pk=queue_id).update(project=project)

        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "Spam rule",
                "source_type": "trace",
                "conditions": {},
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]

        with patch("tfc.temporal.drop_in.runner.start_activity_sync"):
            first = auth_client.post(
                f"{_rule_detail_url(queue_id, rule_id)}evaluate/", format="json"
            )
            second = auth_client.post(
                f"{_rule_detail_url(queue_id, rule_id)}evaluate/", format="json"
            )

        assert first.status_code == status.HTTP_200_OK
        assert second.status_code == status.HTTP_409_CONFLICT
        # 409 body should carry a human-readable message so the FE can
        # surface it as a warning toast rather than a generic error.
        body = second.data
        msg = body.get("result") or body.get("detail") or ""
        assert "in progress" in str(msg).lower() or "already" in str(msg).lower()

    def test_evaluate_again_after_30s_lockout_succeeds(
        self, auth_client, organization, workspace
    ):
        """After the 30s lockout expires, the rule can be re-run normally."""
        project = _create_project(organization, workspace, name="ReRun Project")
        _create_trace(project, name="rerun-trace-1")

        queue_id = _create_queue(auth_client, name="ReRun Queue")
        AnnotationQueue.objects.filter(pk=queue_id).update(project=project)

        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "ReRun rule",
                "source_type": "trace",
                "conditions": {},
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]

        with patch("tfc.temporal.drop_in.runner.start_activity_sync"):
            first = auth_client.post(
                f"{_rule_detail_url(queue_id, rule_id)}evaluate/", format="json"
            )
        assert first.status_code == status.HTTP_200_OK

        # Simulate clock advancing past the 30s window.
        from datetime import timedelta as _td

        from django.utils import timezone as _tz

        AutomationRule.objects.filter(pk=rule_id).update(
            last_triggered_at=_tz.now() - _td(minutes=1)
        )

        with patch("tfc.temporal.drop_in.runner.start_activity_sync"):
            second = auth_client.post(
                f"{_rule_detail_url(queue_id, rule_id)}evaluate/", format="json"
            )
        assert second.status_code == status.HTTP_200_OK

    def test_async_path_reserves_rule_before_scheduling(
        self, auth_client, organization, workspace
    ):
        """For async runs, ``last_triggered_at`` must be bumped *before* the
        workflow is scheduled, not later inside the activity. Otherwise two
        clicks arriving within the worker's pickup window both pass the 30s
        multi-click guard and spawn duplicate workflows/emails. Regression
        guard for Codex P2 finding.
        """
        project = _create_project(organization, workspace, name="Reserve Project")
        _create_trace(project, name="reserve-trace")

        queue_id = _create_queue(auth_client, name="Reserve Queue")
        AnnotationQueue.objects.filter(pk=queue_id).update(project=project)

        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "Reserve rule",
                "source_type": "trace",
                "conditions": {},
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]

        rule_before = AutomationRule.objects.get(pk=rule_id)
        assert rule_before.last_triggered_at is None

        with patch(
            "model_hub.utils.annotation_queue_helpers.RULE_RUN_SYNC_THRESHOLD",
            0,
        ), patch(
            "tfc.temporal.drop_in.runner.start_activity_sync",
            return_value="wf-async",
        ):
            resp = auth_client.post(
                f"{_rule_detail_url(queue_id, rule_id)}evaluate/",
                format="json",
            )
        assert resp.status_code == status.HTTP_202_ACCEPTED

        # last_triggered_at was reserved synchronously by the view, not by
        # the (mocked-away) worker. The 30s guard now has something to fire on.
        rule_after = AutomationRule.objects.get(pk=rule_id)
        assert rule_after.last_triggered_at is not None

    def test_async_schedule_failure_releases_reservation(
        self, auth_client, organization, workspace
    ):
        """If start_activity_sync fails, the reservation must be rolled back
        so the user can retry immediately instead of waiting out the 30s
        lockout."""
        project = _create_project(organization, workspace, name="Rollback Project")
        _create_trace(project, name="rollback-trace")

        queue_id = _create_queue(auth_client, name="Rollback Queue")
        AnnotationQueue.objects.filter(pk=queue_id).update(project=project)

        resp = auth_client.post(
            _rules_url(queue_id),
            {
                "name": "Rollback rule",
                "source_type": "trace",
                "conditions": {},
                "enabled": True,
            },
            format="json",
        )
        rule_id = resp.data["id"]

        with patch(
            "model_hub.utils.annotation_queue_helpers.RULE_RUN_SYNC_THRESHOLD",
            0,
        ), patch(
            "tfc.temporal.drop_in.runner.start_activity_sync",
            side_effect=RuntimeError("temporal unreachable"),
        ):
            resp = auth_client.post(
                f"{_rule_detail_url(queue_id, rule_id)}evaluate/",
                format="json",
            )

        assert resp.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        rule_after = AutomationRule.objects.get(pk=rule_id)
        # Schedule failed → reservation rolled back so user can retry now.
        assert rule_after.last_triggered_at is None
