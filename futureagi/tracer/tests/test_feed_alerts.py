"""Unit and API integration coverage for Error Feed Slack alert rules."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status

from integrations.models import (
    ConnectionStatus,
    IntegrationConnection,
    IntegrationPlatform,
)
from tracer.models.feed_alert import (
    ErrorFeedAlertDelivery,
    ErrorFeedAlertIssueState,
    ErrorFeedAlertRule,
    ErrorFeedIssueEvent,
    FeedAlertDeliveryStatus,
    FeedAlertTrigger,
)
from tracer.models.trace_error_analysis import (
    ClusterSource,
    FeedIssueStatus,
    Priority,
    TraceErrorGroup,
)
from tracer.services.feed_alerts import events, rules


@pytest.fixture
def slack_connection(organization, workspace, user):
    return IntegrationConnection.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        created_by=user,
        platform=IntegrationPlatform.SLACK,
        display_name="Engineering Slack",
        host_url="https://slack.com",
        encrypted_credentials=b"test-credentials",
        external_project_name="T-ALERTS",
        status=ConnectionStatus.ACTIVE,
        backfill_completed=True,
    )


@pytest.fixture
def issue(observe_project):
    return TraceErrorGroup.objects.create(
        project=observe_project,
        cluster_id="ALERT-1",
        error_type="tool_failure",
        source=ClusterSource.SCANNER,
        issue_group="Tool failures",
        issue_category="Timeout",
        title="The payment tool timed out",
        error_count=3,
        total_events=3,
        unique_traces=2,
        priority=Priority.HIGH,
        status=FeedIssueStatus.ESCALATING,
        severity_assessment_status="assessed",
        severity_source="model",
    )


def _payload(slack_connection, observe_project, **overrides):
    payload = {
        "name": "Critical payment errors",
        "enabled": True,
        "project_id": str(observe_project.id),
        "trigger_type": FeedAlertTrigger.SEVERITY_REACHED,
        "trigger_value": "high",
        "filters": {"sources": ["scanner"]},
        "slack_connection_id": str(slack_connection.id),
        "slack_channel_id": "C0123456789",
        "cooldown_seconds": 300,
    }
    payload.update(overrides)
    return payload


def _rule(
    organization, workspace, user, slack_connection, observe_project, **overrides
):
    values = {
        "organization": organization,
        "workspace": workspace,
        "created_by": user,
        "project": observe_project,
        "name": "Alert rule",
        "trigger_type": FeedAlertTrigger.NEW_ISSUE,
        "trigger_value": None,
        "slack_connection": slack_connection,
        "slack_channel_id": "C0123456789",
        "slack_channel_name": "production-alerts",
        "cooldown_seconds": 300,
    }
    values.update(overrides)
    return ErrorFeedAlertRule.no_workspace_objects.create(**values)


@pytest.mark.django_db
class TestAlertRuleService:
    def test_create_requires_reconnection_when_public_channel_join_scope_is_missing(
        self,
        monkeypatch,
        organization,
        workspace,
        user,
        observe_project,
        slack_connection,
    ):
        from integrations.services.slack_service import SlackApiError

        monkeypatch.setattr(rules, "check_ee_feature", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            rules.CredentialManager, "decrypt", lambda value: {"bot_token": "xoxb-test"}
        )
        with patch(
            "integrations.services.slack_service.SlackService.validate_channel",
            side_effect=SlackApiError("missing_scope"),
        ) as validate:
            with pytest.raises(rules.FeedAlertError, match="Reconnect Slack") as error:
                rules.create_rule(
                    data=_payload(slack_connection, observe_project),
                    organization=organization,
                    workspace=workspace,
                    user=user,
                )

        assert error.value.status_code == 409
        assert validate.call_args.kwargs == {"join_public": True}
        assert not ErrorFeedAlertRule.no_workspace_objects.filter(
            organization=organization, workspace=workspace
        ).exists()

    def test_create_validates_channel_and_persists_tenant_scoped_rule(
        self,
        monkeypatch,
        organization,
        workspace,
        user,
        observe_project,
        slack_connection,
    ):
        monkeypatch.setattr(rules, "check_ee_feature", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            rules.CredentialManager, "decrypt", lambda value: {"bot_token": "xoxb-test"}
        )
        with patch(
            "integrations.services.slack_service.SlackService.validate_channel",
            return_value={"id": "C0123456789", "name": "production-alerts"},
        ) as validate:
            result = rules.create_rule(
                data=_payload(slack_connection, observe_project),
                organization=organization,
                workspace=workspace,
                user=user,
            )

        stored = ErrorFeedAlertRule.no_workspace_objects.get(pk=result["id"])
        assert stored.organization_id == organization.id
        assert stored.workspace_id == workspace.id
        assert stored.project_id == observe_project.id
        assert stored.slack_channel_name == "production-alerts"
        validate.assert_called_once()

    def test_create_rejects_unknown_filter_before_a_rule_is_written(
        self,
        monkeypatch,
        organization,
        workspace,
        user,
        observe_project,
        slack_connection,
    ):
        monkeypatch.setattr(rules, "check_ee_feature", lambda *args, **kwargs: None)
        payload = _payload(
            slack_connection, observe_project, filters={"unknown": ["x"]}
        )

        with pytest.raises(rules.FeedAlertError, match="Unsupported Error Feed filter"):
            rules.create_rule(
                data=payload, organization=organization, workspace=workspace, user=user
            )

        assert not ErrorFeedAlertRule.no_workspace_objects.exists()

    def test_update_never_exposes_a_rule_from_another_workspace(
        self,
        monkeypatch,
        organization,
        workspace,
        user,
        observe_project,
        slack_connection,
    ):
        from accounts.models.workspace import Workspace

        monkeypatch.setattr(rules, "check_ee_feature", lambda *args, **kwargs: None)
        other_workspace = Workspace.objects.create(
            name="Other alerts",
            organization=organization,
            created_by=user,
            is_active=True,
        )
        other_rule = _rule(
            organization, other_workspace, user, slack_connection, observe_project
        )

        with pytest.raises(rules.FeedAlertError) as exc:
            rules.update_rule(
                rule_id=other_rule.id,
                data={"name": "attempted takeover"},
                organization=organization,
                workspace=workspace,
                user=user,
            )

        assert exc.value.status_code == 404
        other_rule.refresh_from_db()
        assert other_rule.name == "Alert rule"

    def test_test_rule_posts_to_the_selected_channel(
        self,
        monkeypatch,
        organization,
        workspace,
        user,
        observe_project,
        slack_connection,
    ):
        monkeypatch.setattr(rules, "check_ee_feature", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            rules.CredentialManager, "decrypt", lambda value: {"bot_token": "xoxb-test"}
        )
        rule = _rule(organization, workspace, user, slack_connection, observe_project)
        with (
            patch(
                "integrations.services.slack_service.SlackService.validate_channel",
                return_value={"name": "production-alerts"},
            ),
            patch(
                "integrations.services.slack_service.SlackService.post_message",
                return_value={"ts": "123.456"},
            ) as post,
        ):
            result = rules.test_rule(
                rule_id=rule.id,
                organization=organization,
                workspace=workspace,
                user=user,
            )

        assert result == {"sent": True, "slack_ts": "123.456"}
        assert post.call_args.args[1] == "C0123456789"


class TestAlertPredicate:
    def test_matches_threshold_transition_and_filters(self):
        rule = SimpleNamespace(
            trigger_type=FeedAlertTrigger.SEVERITY_REACHED,
            trigger_value="high",
            filters={"sources": ["scanner"], "issue_categories": ["Timeout"]},
        )
        event = SimpleNamespace(
            event_kind="issue_changed",
            before={"severity": "medium", "severity_source": "model"},
            after={
                "visible": True,
                "severity": "high",
                "severity_source": "model",
                "source": "scanner",
                "issue_category": "Timeout",
            },
        )
        assert events._matches(rule, event)
        event.after["source"] = "eval"
        assert not events._matches(rule, event)

    @pytest.mark.parametrize(
        ("trigger_type", "trigger_value", "event_kind", "before", "after", "expected"),
        [
            (
                FeedAlertTrigger.NEW_ISSUE,
                None,
                "new_issue",
                {},
                {"status": "for_review"},
                True,
            ),
            (
                FeedAlertTrigger.NEW_ISSUE,
                None,
                "issue_changed",
                {"status": "for_review"},
                {"status": "for_review"},
                False,
            ),
            (
                FeedAlertTrigger.ESCALATING,
                None,
                "issue_changed",
                {"status": "for_review"},
                {"status": "escalating"},
                True,
            ),
            (
                FeedAlertTrigger.ESCALATING,
                None,
                "issue_changed",
                {"status": "escalating"},
                {"status": "escalating"},
                False,
            ),
            (
                FeedAlertTrigger.OCCURRENCES_CROSSED,
                5,
                "issue_changed",
                {"occurrences": 4},
                {"occurrences": 5},
                True,
            ),
            (
                FeedAlertTrigger.OCCURRENCES_CROSSED,
                5,
                "issue_changed",
                {"occurrences": 5},
                {"occurrences": 6},
                False,
            ),
            (
                FeedAlertTrigger.SEVERITY_REACHED,
                "high",
                "new_issue",
                {},
                {"severity": "high", "severity_source": "default"},
                False,
            ),
            (
                FeedAlertTrigger.SEVERITY_REACHED,
                "high",
                "issue_changed",
                {"severity": "medium", "severity_source": "default"},
                {"severity": "high", "severity_source": "llm"},
                True,
            ),
        ],
    )
    def test_trigger_boundaries(
        self, trigger_type, trigger_value, event_kind, before, after, expected
    ):
        rule = SimpleNamespace(
            trigger_type=trigger_type, trigger_value=trigger_value, filters={}
        )
        event = SimpleNamespace(
            event_kind=event_kind, before=before, after={"visible": True, **after}
        )
        assert events._matches(rule, event) is expected

    def test_invisible_issue_never_matches(self):
        rule = SimpleNamespace(
            trigger_type=FeedAlertTrigger.NEW_ISSUE, trigger_value=None, filters={}
        )
        event = SimpleNamespace(
            event_kind="new_issue", before={}, after={"visible": False}
        )
        assert not events._matches(rule, event)

    def test_slack_message_escapes_issue_text_and_links_to_feed(self, settings):
        settings.APP_BASE_URL = "https://app.example.test"
        delivery = SimpleNamespace(
            event=SimpleNamespace(
                event_kind="new_issue",
                created_at=timezone.now(),
                after={
                    "title": "A <secret> & failure",
                    "cluster_id": "S-123",
                    "severity": "high",
                    "status": "escalating",
                    "occurrences": 4,
                },
                project=SimpleNamespace(name="Project & team"),
            ),
            rule=SimpleNamespace(
                name="Critical errors", get_trigger_type_display=lambda: "New issue"
            ),
        )
        fallback, blocks = events._message(delivery)
        assert (
            fallback
            == ":red_circle: Error Feed: A &lt;secret&gt; &amp; failure (Project &amp; team, high)"
        )
        body = blocks[0]["text"]["text"]
        assert (
            "<https://app.example.test/dashboard/error-feed/S-123|*A &lt;secret&gt; &amp; failure*>"
            in body
        )
        assert "State: *Escalating*" in body
        assert "Severity: *High*" in body
        assert "First seen: *<!date^" in body
        assert "Open issue in Error Feed" in blocks[1]["text"]["text"]
        assert "Project: *Project &amp; team*" in blocks[2]["elements"][0]["text"]
        assert (
            "Alert: <https://app.example.test/dashboard/error-feed/alerts|Critical errors>"
            in blocks[2]["elements"][0]["text"]
        )
        assert "Short ID: `S-123`" in blocks[2]["elements"][0]["text"]
        assert "A <secret>" not in body


class TestAlertRuleValidation:
    def test_trigger_requires_compatible_value(self):
        with pytest.raises(rules.FeedAlertError, match="severity threshold"):
            rules._validate_trigger(FeedAlertTrigger.SEVERITY_REACHED, None)
        with pytest.raises(rules.FeedAlertError, match="does not take a value"):
            rules._validate_trigger(FeedAlertTrigger.NEW_ISSUE, "critical")
        with pytest.raises(rules.FeedAlertError, match="Occurrence threshold"):
            rules._validate_trigger(FeedAlertTrigger.OCCURRENCES_CROSSED, True)

    def test_filters_reject_unknown_predicates(self):
        with pytest.raises(rules.FeedAlertError, match="Unsupported Error Feed filter"):
            rules._validate_filters({"trace_input": ["secret"]})


def test_local_e2e_error_feed_entitlement_never_applies_to_production(settings):
    from tfc import ee_gating

    settings.E2E_ERROR_FEED_ENABLED = True
    settings.ENV_TYPE = "local"
    ee_gating.check_ee_feature(ee_gating.EEFeature.ERROR_FEED, org_id="test-org")

    settings.ENV_TYPE = "prod"
    with pytest.raises(ee_gating.FeatureUnavailable):
        ee_gating.check_ee_feature(ee_gating.EEFeature.ERROR_FEED, org_id="test-org")


@pytest.mark.django_db
class TestAlertEventPipeline:
    def test_record_and_queue_are_idempotent_and_apply_cooldown(
        self, organization, workspace, user, observe_project, slack_connection, issue
    ):
        rule = _rule(organization, workspace, user, slack_connection, observe_project)
        events.record_issue_event(
            cluster=issue, before=None, source_key="issue-created:1"
        )
        events.record_issue_event(
            cluster=issue, before=None, source_key="issue-created:1"
        )
        event = ErrorFeedIssueEvent.no_workspace_objects.get(
            source_key="issue-created:1"
        )
        assert ErrorFeedIssueEvent.no_workspace_objects.count() == 1

        events._queue_event(event)
        events._queue_event(event)
        assert (
            ErrorFeedAlertDelivery.no_workspace_objects.filter(
                rule=rule, event=event
            ).count()
            == 1
        )
        assert (
            ErrorFeedIssueEvent.no_workspace_objects.get(pk=event.pk).processed_at
            is not None
        )

        next_event = ErrorFeedIssueEvent.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            project=observe_project,
            cluster=issue,
            event_kind="new_issue",
            source_key="issue-created:2",
            before={},
            after=events.issue_snapshot(issue),
        )
        events._queue_event(next_event)
        assert (
            ErrorFeedAlertDelivery.no_workspace_objects.filter(rule=rule).count() == 1
        )
        state = ErrorFeedAlertIssueState.no_workspace_objects.get(
            rule=rule, cluster=issue
        )
        assert state.last_event_id == event.id

    def test_delivery_retries_transient_slack_failures_then_sends(
        self,
        monkeypatch,
        organization,
        workspace,
        user,
        observe_project,
        slack_connection,
        issue,
    ):
        rule = _rule(organization, workspace, user, slack_connection, observe_project)
        event = ErrorFeedIssueEvent.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            project=observe_project,
            cluster=issue,
            event_kind="new_issue",
            source_key="delivery:1",
            before={},
            after=events.issue_snapshot(issue),
        )
        delivery = ErrorFeedAlertDelivery.no_workspace_objects.create(
            rule=rule,
            event=event,
            cluster=issue,
            channel_id=rule.slack_channel_id,
            next_attempt_at=timezone.now(),
        )
        monkeypatch.setattr(
            events.CredentialManager,
            "decrypt",
            lambda value: {"bot_token": "xoxb-test"},
        )
        from integrations.services.slack_service import SlackApiError

        with patch(
            "integrations.services.slack_service.SlackService.post_message",
            side_effect=SlackApiError("ratelimited", retry_after=90),
        ):
            events._send_delivery(delivery.id)
        delivery.refresh_from_db()
        assert delivery.status == FeedAlertDeliveryStatus.RETRY
        assert delivery.attempts == 1
        assert delivery.next_attempt_at is not None

        with patch(
            "integrations.services.slack_service.SlackService.post_message",
            return_value={"ts": "99.1"},
        ) as post:
            events._send_delivery(delivery.id)
        delivery.refresh_from_db()
        rule.refresh_from_db()
        assert delivery.status == FeedAlertDeliveryStatus.SENT
        assert delivery.slack_ts == "99.1"
        assert rule.last_triggered_at is not None
        post.assert_called_once()


@pytest.mark.api
@pytest.mark.django_db
class TestCentralAlertAPI:
    def test_metric_kind_uses_same_central_crud_without_error_feed_entitlement(
        self, monkeypatch, auth_client, observe_project
    ):
        from tracer.models.monitor import UserAlertMonitor

        monkeypatch.setattr(
            "tracer.views.alerts.check_ee_can_create", lambda *args, **kwargs: None
        )
        created = auth_client.post(
            "/tracer/alerts/",
            {
                "kind": "metric",
                "name": "Latency threshold",
                "project": str(observe_project.id),
                "metric_type": "span_response_time",
                "threshold_type": "static",
                "threshold_operator": "greater_than",
                "critical_threshold_value": 1,
                "notification_emails": ["alerts@example.com"],
            },
            format="json",
        )
        assert created.status_code == status.HTTP_201_CREATED, created.json()
        alert_id = created.json()["result"]["id"]
        assert created.json()["result"]["kind"] == "metric"

        listed = auth_client.get("/tracer/alerts/?kind=metric")
        assert [row["id"] for row in listed.json()["result"]["alerts"]] == [alert_id]
        patched = auth_client.patch(
            f"/tracer/alerts/metric/{alert_id}/", {"enabled": False}, format="json"
        )
        assert patched.status_code == status.HTTP_200_OK
        assert UserAlertMonitor.no_workspace_objects.get(pk=alert_id).is_mute

        removed = auth_client.delete(f"/tracer/alerts/metric/{alert_id}/")
        assert removed.status_code == status.HTTP_200_OK
        assert not UserAlertMonitor.no_workspace_objects.filter(pk=alert_id).exists()

    def test_error_feed_options_return_only_workspace_projects(
        self, monkeypatch, auth_client, observe_project
    ):
        monkeypatch.setattr(
            "tfc.ee_gating.check_ee_feature", lambda *args, **kwargs: None
        )
        response = auth_client.get("/tracer/alerts/options/?kind=error_feed")
        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert [str(project["id"]) for project in result["projects"]] == [
            str(observe_project.id)
        ]
        assert {trigger["value"] for trigger in result["triggers"]} == {
            "new_issue",
            "severity_reached",
            "escalating",
            "occurrences_crossed",
        }

    def test_error_feed_alert_crud_and_test_use_one_central_route(
        self,
        monkeypatch,
        auth_client,
        organization,
        workspace,
        user,
        observe_project,
        slack_connection,
    ):
        monkeypatch.setattr(rules, "check_ee_feature", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            rules.CredentialManager, "decrypt", lambda value: {"bot_token": "xoxb-test"}
        )
        with patch(
            "integrations.services.slack_service.SlackService.validate_channel",
            return_value={"name": "production-alerts"},
        ):
            created = auth_client.post(
                "/tracer/alerts/",
                {"kind": "error_feed", **_payload(slack_connection, observe_project)},
                format="json",
            )
        assert created.status_code == status.HTTP_201_CREATED
        alert_id = created.json()["result"]["id"]

        listed = auth_client.get("/tracer/alerts/?kind=error_feed")
        assert listed.status_code == status.HTTP_200_OK
        assert [row["id"] for row in listed.json()["result"]["alerts"]] == [alert_id]

        updated = auth_client.patch(
            f"/tracer/alerts/error_feed/{alert_id}/", {"enabled": False}, format="json"
        )
        assert updated.status_code == status.HTTP_200_OK
        assert updated.json()["result"]["enabled"] is False

        with (
            patch(
                "integrations.services.slack_service.SlackService.validate_channel",
                return_value={"name": "production-alerts"},
            ),
            patch(
                "integrations.services.slack_service.SlackService.post_message",
                return_value={"ts": "1.2"},
            ),
        ):
            tested = auth_client.post(
                f"/tracer/alerts/error_feed/{alert_id}/test/", {}, format="json"
            )
        assert tested.status_code == status.HTTP_200_OK
        assert tested.json()["result"]["sent"] is True

        deleted = auth_client.delete(f"/tracer/alerts/error_feed/{alert_id}/")
        assert deleted.status_code == status.HTTP_200_OK
        assert deleted.json()["result"]["deleted"] is True

    def test_error_feed_alert_creation_requires_authentication(
        self, api_client, observe_project, slack_connection
    ):
        response = api_client.post(
            "/tracer/alerts/",
            {"kind": "error_feed", **_payload(slack_connection, observe_project)},
            format="json",
        )
        assert response.status_code in {
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        }
