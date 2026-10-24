"""Schema v2 for the gateway.

Request-log v2 columns and indexes, the attempt, outcome and managed
upstream-key tables, and the URL, gateway-admin client and schedule
placeholders other modules fill in.
"""

import importlib
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.migrations.loader import MigrationLoader
from django.urls import resolve, reverse
from django.urls.resolvers import URLResolver
from django.utils import timezone

from agentcc.models import (
    AgentccManagedUpstreamKey,
    AgentccRequestAttempt,
    AgentccRequestLog,
    AgentccRequestOutcome,
)
from agentcc.services.credential_manager import encrypt_token

# column -> (Django field type, max_length or (digits, places), null)
V2_LOG_COLUMNS = {
    "billing_mode": ("CharField", 16, True),
    "credential_type": ("CharField", 8, True),
    "cost_upstream": ("DecimalField", (16, 8), True),
    "cost_markup": ("DecimalField", (16, 8), True),
    "cost_source": ("CharField", 32, True),
    "usage_estimated": ("BooleanField", None, True),
    "cached_tokens": ("IntegerField", None, True),
    "cache_write_tokens": ("IntegerField", None, True),
    "reasoning_tokens": ("IntegerField", None, True),
    "attempt_count": ("SmallIntegerField", None, False),
    "generation_id": ("CharField", 64, True),
    "agent_framework": ("CharField", 32, True),
    "agent_step": ("CharField", 32, True),
    "agent_step_source": ("CharField", 32, True),
    "agent_env": ("CharField", 32, True),
    "agent_id": ("CharField", 128, True),
    "agent_version": ("CharField", 128, True),
    "agent_run_id": ("CharField", 128, True),
    "agent_parent_run_id": ("CharField", 128, True),
    "agent_task_id": ("CharField", 128, True),
    "budget_scope": ("CharField", 128, True),
    "agent_turn": ("IntegerField", None, True),
    "policy_version": ("CharField", 64, True),
    "policy_variant": ("CharField", 16, True),
    "catalog_snapshot_id": ("CharField", 32, True),
    "auto_routed": ("BooleanField", None, True),
    "app_title": ("CharField", 128, True),
    "app_referer": ("CharField", 256, True),
    "end_user": ("CharField", 256, True),
    "tags": ("JSONField", None, True),
    "cost_landed": ("DecimalField", (16, 8), True),
    "served_upstream_provider": ("CharField", 64, True),
    "upstream_key_id": ("CharField", 128, True),
    "content_mode": ("CharField", 8, True),
    "agent_identity_source": ("CharField", 32, True),
    "agent_workflow": ("CharField", 128, True),
    "agent_root_run_id": ("CharField", 128, True),
}

# index name -> (columns, orders)
V2_LOG_INDEXES = {
    "agentcc_rl_org_run_idx": (["organization_id", "agent_run_id"], ["ASC", "ASC"]),
    "agentcc_rl_org_agent_idx": (
        ["organization_id", "agent_id", "started_at"],
        ["ASC", "ASC", "DESC"],
    ),
    "agentcc_rl_org_billing_idx": (
        ["organization_id", "billing_mode", "started_at"],
        ["ASC", "ASC", "DESC"],
    ),
    "agentcc_rl_org_request_idx": (
        ["organization_id", "request_id"],
        ["ASC", "ASC"],
    ),
    "agentcc_rl_org_variant_idx": (
        ["organization_id", "policy_variant", "started_at"],
        ["ASC", "ASC", "DESC"],
    ),
}

PLACEHOLDER_URL_MODULES = [
    "agentcc.urls_billing",
    "agentcc.urls_catalog",
    "agentcc.urls_policy",
    "agentcc.urls_analytics_v2",
    "agentcc.urls_playground",
    "agentcc.urls_capabilities",
]

# Every agentcc URL name and its path. Detail routes use pk="x1".
EXISTING_ROUTES = [
    ("agentcc-analytics-cost-breakdown", {}, "/agentcc/analytics/cost-breakdown/"),
    ("agentcc-analytics-error-breakdown", {}, "/agentcc/analytics/error-breakdown/"),
    (
        "agentcc-analytics-guardrail-overview",
        {},
        "/agentcc/analytics/guardrail-overview/",
    ),
    ("agentcc-analytics-guardrail-rules", {}, "/agentcc/analytics/guardrail-rules/"),
    ("agentcc-analytics-guardrail-trends", {}, "/agentcc/analytics/guardrail-trends/"),
    ("agentcc-analytics-latency-stats", {}, "/agentcc/analytics/latency-stats/"),
    ("agentcc-analytics-model-comparison", {}, "/agentcc/analytics/model-comparison/"),
    ("agentcc-analytics-overview", {}, "/agentcc/analytics/overview/"),
    ("agentcc-analytics-usage-timeseries", {}, "/agentcc/analytics/usage-timeseries/"),
    ("agentcc-api-key-bulk", {}, "/agentcc/api-keys/bulk/"),
    ("agentcc-api-key-detail", {"pk": "x1"}, "/agentcc/api-keys/x1/"),
    ("agentcc-api-key-list", {}, "/agentcc/api-keys/"),
    ("agentcc-api-key-revoke", {"pk": "x1"}, "/agentcc/api-keys/x1/revoke/"),
    ("agentcc-api-key-sync", {}, "/agentcc/api-keys/sync/"),
    ("agentcc-blocklist-add-words", {"pk": "x1"}, "/agentcc/blocklists/x1/add-words/"),
    ("agentcc-blocklist-detail", {"pk": "x1"}, "/agentcc/blocklists/x1/"),
    ("agentcc-blocklist-list", {}, "/agentcc/blocklists/"),
    (
        "agentcc-blocklist-remove-words",
        {"pk": "x1"},
        "/agentcc/blocklists/x1/remove-words/",
    ),
    ("agentcc-custom-property-detail", {"pk": "x1"}, "/agentcc/custom-properties/x1/"),
    ("agentcc-custom-property-list", {}, "/agentcc/custom-properties/"),
    ("agentcc-custom-property-validate", {}, "/agentcc/custom-properties/validate/"),
    ("agentcc-email-alert-detail", {"pk": "x1"}, "/agentcc/email-alerts/x1/"),
    ("agentcc-email-alert-list", {}, "/agentcc/email-alerts/"),
    ("agentcc-email-alert-test", {"pk": "x1"}, "/agentcc/email-alerts/x1/test/"),
    (
        "agentcc-gateway-cancel-batch",
        {"pk": "x1"},
        "/agentcc/gateways/x1/cancel-batch/",
    ),
    ("agentcc-gateway-config", {"pk": "x1"}, "/agentcc/gateways/x1/config/"),
    ("agentcc-gateway-detail", {"pk": "x1"}, "/agentcc/gateways/x1/"),
    ("agentcc-gateway-get-batch", {"pk": "x1"}, "/agentcc/gateways/x1/get-batch/"),
    (
        "agentcc-gateway-health-check",
        {"pk": "x1"},
        "/agentcc/gateways/x1/health_check/",
    ),
    ("agentcc-gateway-list", {}, "/agentcc/gateways/"),
    ("agentcc-gateway-mcp-prompts", {"pk": "x1"}, "/agentcc/gateways/x1/mcp-prompts/"),
    (
        "agentcc-gateway-mcp-resources",
        {"pk": "x1"},
        "/agentcc/gateways/x1/mcp-resources/",
    ),
    ("agentcc-gateway-mcp-status", {"pk": "x1"}, "/agentcc/gateways/x1/mcp-status/"),
    ("agentcc-gateway-mcp-tools", {"pk": "x1"}, "/agentcc/gateways/x1/mcp-tools/"),
    ("agentcc-gateway-protect-templates", {}, "/agentcc/gateways/protect-templates/"),
    ("agentcc-gateway-providers", {"pk": "x1"}, "/agentcc/gateways/x1/providers/"),
    ("agentcc-gateway-reload", {"pk": "x1"}, "/agentcc/gateways/x1/reload/"),
    (
        "agentcc-gateway-remove-budget",
        {"pk": "x1"},
        "/agentcc/gateways/x1/remove-budget/",
    ),
    (
        "agentcc-gateway-remove-mcp-server",
        {"pk": "x1"},
        "/agentcc/gateways/x1/remove-mcp-server/",
    ),
    (
        "agentcc-gateway-remove-provider",
        {"pk": "x1"},
        "/agentcc/gateways/x1/remove-provider/",
    ),
    ("agentcc-gateway-set-budget", {"pk": "x1"}, "/agentcc/gateways/x1/set-budget/"),
    (
        "agentcc-gateway-submit-batch",
        {"pk": "x1"},
        "/agentcc/gateways/x1/submit-batch/",
    ),
    (
        "agentcc-gateway-test-mcp-tool",
        {"pk": "x1"},
        "/agentcc/gateways/x1/test-mcp-tool/",
    ),
    (
        "agentcc-gateway-test-playground",
        {"pk": "x1"},
        "/agentcc/gateways/x1/test-playground/",
    ),
    (
        "agentcc-gateway-toggle-guardrail",
        {"pk": "x1"},
        "/agentcc/gateways/x1/toggle-guardrail/",
    ),
    (
        "agentcc-gateway-update-config",
        {"pk": "x1"},
        "/agentcc/gateways/x1/update-config/",
    ),
    (
        "agentcc-gateway-update-guardrail",
        {"pk": "x1"},
        "/agentcc/gateways/x1/update-guardrail/",
    ),
    (
        "agentcc-gateway-update-mcp-guardrails",
        {"pk": "x1"},
        "/agentcc/gateways/x1/update-mcp-guardrails/",
    ),
    (
        "agentcc-gateway-update-mcp-server",
        {"pk": "x1"},
        "/agentcc/gateways/x1/update-mcp-server/",
    ),
    (
        "agentcc-gateway-update-provider",
        {"pk": "x1"},
        "/agentcc/gateways/x1/update-provider/",
    ),
    (
        "agentcc-guardrail-config-pii-entities",
        {},
        "/agentcc/guardrail-configs/pii-entities/",
    ),
    ("agentcc-guardrail-config-topics", {}, "/agentcc/guardrail-configs/topics/"),
    (
        "agentcc-guardrail-config-validate-cel",
        {},
        "/agentcc/guardrail-configs/validate-cel/",
    ),
    (
        "agentcc-guardrail-feedback-detail",
        {"pk": "x1"},
        "/agentcc/guardrail-feedback/x1/",
    ),
    ("agentcc-guardrail-feedback-list", {}, "/agentcc/guardrail-feedback/"),
    ("agentcc-guardrail-feedback-summary", {}, "/agentcc/guardrail-feedback/summary/"),
    (
        "agentcc-guardrail-policy-apply",
        {"pk": "x1"},
        "/agentcc/guardrail-policies/x1/apply/",
    ),
    (
        "agentcc-guardrail-policy-detail",
        {"pk": "x1"},
        "/agentcc/guardrail-policies/x1/",
    ),
    ("agentcc-guardrail-policy-list", {}, "/agentcc/guardrail-policies/"),
    ("agentcc-guardrail-policy-sync", {}, "/agentcc/guardrail-policies/sync/"),
    ("agentcc-managed-models-catalog", {}, "/agentcc/managed-models/catalog/"),
    ("agentcc-managed-models-list", {}, "/agentcc/managed-models/"),
    ("agentcc-org-config-activate", {"pk": "x1"}, "/agentcc/org-configs/x1/activate/"),
    ("agentcc-org-config-active", {}, "/agentcc/org-configs/active/"),
    ("agentcc-org-config-bulk", {}, "/agentcc/org-configs/bulk/"),
    ("agentcc-org-config-detail", {"pk": "x1"}, "/agentcc/org-configs/x1/"),
    ("agentcc-org-config-diff", {"pk": "x1"}, "/agentcc/org-configs/x1/diff/"),
    ("agentcc-org-config-list", {}, "/agentcc/org-configs/"),
    (
        "agentcc-provider-credential-detail",
        {"pk": "x1"},
        "/agentcc/provider-credentials/x1/",
    ),
    (
        "agentcc-provider-credential-fetch-models",
        {},
        "/agentcc/provider-credentials/fetch_models/",
    ),
    ("agentcc-provider-credential-list", {}, "/agentcc/provider-credentials/"),
    (
        "agentcc-provider-credential-rotate",
        {"pk": "x1"},
        "/agentcc/provider-credentials/x1/rotate/",
    ),
    ("agentcc-request-log-detail", {"pk": "x1"}, "/agentcc/request-logs/x1/"),
    ("agentcc-request-log-export", {}, "/agentcc/request-logs/export/"),
    ("agentcc-request-log-list", {}, "/agentcc/request-logs/"),
    (
        "agentcc-request-log-metadata-values",
        {},
        "/agentcc/request-logs/metadata-values/",
    ),
    ("agentcc-request-log-search", {}, "/agentcc/request-logs/search/"),
    (
        "agentcc-request-log-session-detail",
        {"session_id": "x1"},
        "/agentcc/request-logs/sessions/x1/",
    ),
    ("agentcc-request-log-sessions", {}, "/agentcc/request-logs/sessions/"),
    (
        "agentcc-routing-policy-activate",
        {"pk": "x1"},
        "/agentcc/routing-policies/x1/activate/",
    ),
    ("agentcc-routing-policy-detail", {"pk": "x1"}, "/agentcc/routing-policies/x1/"),
    ("agentcc-routing-policy-list", {}, "/agentcc/routing-policies/"),
    ("agentcc-routing-policy-sync", {}, "/agentcc/routing-policies/sync/"),
    ("agentcc-session-close", {"pk": "x1"}, "/agentcc/sessions/x1/close/"),
    ("agentcc-session-detail", {"pk": "x1"}, "/agentcc/sessions/x1/"),
    ("agentcc-session-list", {}, "/agentcc/sessions/"),
    ("agentcc-session-requests", {"pk": "x1"}, "/agentcc/sessions/x1/requests/"),
    (
        "agentcc-shadow-experiment-complete",
        {"pk": "x1"},
        "/agentcc/shadow-experiments/x1/complete/",
    ),
    (
        "agentcc-shadow-experiment-detail",
        {"pk": "x1"},
        "/agentcc/shadow-experiments/x1/",
    ),
    ("agentcc-shadow-experiment-list", {}, "/agentcc/shadow-experiments/"),
    (
        "agentcc-shadow-experiment-pause",
        {"pk": "x1"},
        "/agentcc/shadow-experiments/x1/pause/",
    ),
    (
        "agentcc-shadow-experiment-resume",
        {"pk": "x1"},
        "/agentcc/shadow-experiments/x1/resume/",
    ),
    (
        "agentcc-shadow-experiment-stats",
        {"pk": "x1"},
        "/agentcc/shadow-experiments/x1/stats/",
    ),
    ("agentcc-shadow-result-detail", {"pk": "x1"}, "/agentcc/shadow-results/x1/"),
    ("agentcc-shadow-result-list", {}, "/agentcc/shadow-results/"),
    ("agentcc-spend-summary", {}, "/agentcc/spend-summary/"),
    ("agentcc-webhook-detail", {"pk": "x1"}, "/agentcc/webhooks/x1/"),
    ("agentcc-webhook-event-detail", {"pk": "x1"}, "/agentcc/webhook-events/x1/"),
    ("agentcc-webhook-event-list", {}, "/agentcc/webhook-events/"),
    ("agentcc-webhook-event-retry", {"pk": "x1"}, "/agentcc/webhook-events/x1/retry/"),
    ("agentcc-webhook-list", {}, "/agentcc/webhooks/"),
    ("agentcc-webhook-logs", {}, "/agentcc/webhook/logs/"),
    ("agentcc-webhook-shadow-results", {}, "/agentcc/webhook/shadow-results/"),
    ("agentcc-webhook-test", {"pk": "x1"}, "/agentcc/webhooks/x1/test/"),
]


def _indexes(table):
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, table)
    return {name: c for name, c in constraints.items() if c["index"]}


def _index_on(table, columns):
    return [c for c in _indexes(table).values() if c["columns"] == columns]


def _log(organization, **fields):
    return AgentccRequestLog.objects.create(
        organization=organization,
        request_id=fields.pop("request_id", "req-1"),
        model="gpt-4o-mini",
        provider="openai",
        **fields,
    )


@pytest.mark.integration
class TestRequestLogV2:
    def test_v2_columns_match_contract(self):
        for name, (field_type, size, nullable) in V2_LOG_COLUMNS.items():
            field = AgentccRequestLog._meta.get_field(name)
            assert field.get_internal_type() == field_type, name
            assert field.null is nullable, name
            if field_type == "CharField":
                assert field.max_length == size, name
            if field_type == "DecimalField":
                assert (field.max_digits, field.decimal_places) == size, name

    def test_v1_shaped_row_saves_with_v2_columns_unset(self, organization):
        log = _log(
            organization,
            latency_ms=120,
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
            cost=Decimal("0.000123"),
            status_code=200,
            metadata={"billing_mode": "byok"},
        )

        log.refresh_from_db()
        assert log.attempt_count == 1
        for name in V2_LOG_COLUMNS:
            if name != "attempt_count":
                assert getattr(log, name) is None, name

    def test_v2_values_round_trip(self, organization):
        log = _log(
            organization,
            billing_mode="managed",
            credential_type="managed",
            cost_upstream=Decimal("0.00012345"),
            cost_markup=Decimal("0.00000617"),
            cost_landed=Decimal("0.00013100"),
            cost_source="provider_reported",
            usage_estimated=False,
            cached_tokens=3,
            cache_write_tokens=0,
            reasoning_tokens=7,
            attempt_count=3,
            generation_id="req-1",
            agent_framework="omega",
            agent_run_id="run-1",
            agent_root_run_id="run-root",
            agent_workflow="triage",
            agent_turn=4,
            policy_version="v12",
            policy_variant="candidate",
            auto_routed=True,
            tags=["prod", "eu"],
            content_mode="none",
        )

        log.refresh_from_db()
        assert log.cost_upstream == Decimal("0.00012345")
        assert log.cost_markup == Decimal("0.00000617")
        assert log.attempt_count == 3
        assert log.agent_turn == 4
        assert log.auto_routed is True
        assert log.usage_estimated is False
        assert log.tags == ["prod", "eu"]
        assert log.agent_root_run_id == "run-root"

    def test_insert_without_v2_columns_succeeds(self, organization):
        """A process still running the v1 model omits every v2 column."""
        v1_fields = [
            f
            for f in AgentccRequestLog._meta.concrete_fields
            if f.name not in V2_LOG_COLUMNS
        ]
        row = AgentccRequestLog(organization=organization, request_id="old-pod")
        values = [
            f.get_db_prep_save(f.pre_save(row, add=True), connection) for f in v1_fields
        ]
        columns = ", ".join(connection.ops.quote_name(f.column) for f in v1_fields)
        placeholders = ", ".join(["%s"] * len(v1_fields))
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO agentcc_request_log ({columns}) VALUES ({placeholders})",
                values,
            )

        stored = AgentccRequestLog.objects.get(request_id="old-pod")
        assert stored.attempt_count == 1
        assert stored.billing_mode is None

    def test_v2_indexes_exist(self, db):
        indexes = _indexes("agentcc_request_log")
        for name, (columns, orders) in V2_LOG_INDEXES.items():
            assert name in indexes, name
            assert indexes[name]["columns"] == columns, name
            assert indexes[name]["orders"] == orders, name
            assert indexes[name]["unique"] is False, name

    def test_no_unique_constraint_on_request_id(self, organization):
        _log(organization, request_id="dup")
        _log(organization, request_id="dup")

        assert AgentccRequestLog.objects.filter(request_id="dup").count() == 2


def _attempt(organization, attempt_id, **fields):
    return AgentccRequestAttempt.objects.create(
        organization=organization,
        attempt_id=attempt_id,
        request_id=fields.pop("request_id", "req-1"),
        index=fields.pop("index", 1),
        kind=fields.pop("kind", AgentccRequestAttempt.Kind.PRIMARY),
        outcome=fields.pop("outcome", AgentccRequestAttempt.Outcome.SUCCESS),
        **fields,
    )


@pytest.mark.integration
class TestRequestAttempt:
    def test_attempt_id_is_unique(self, organization):
        _attempt(organization, "req-1.1")

        with pytest.raises(IntegrityError), transaction.atomic():
            _attempt(organization, "req-1.1", index=2)

    def test_attempt_id_takes_a_255_char_request_id(self, organization):
        request_id = "r" * 255
        attempt = _attempt(organization, f"{request_id}.16", request_id=request_id)

        attempt.refresh_from_db()
        assert attempt.attempt_id == f"{request_id}.16"

    def test_fields_round_trip(self, organization):
        log = _log(organization)
        attempt = _attempt(
            organization,
            "req-1.2",
            index=2,
            kind=AgentccRequestAttempt.Kind.FAILOVER,
            reason="status_5xx",
            request_log=log,
            provider="openrouter",
            model="openai/gpt-4o-mini",
            endpoint_id="openai|gpt-4o-mini",
            upstream_provider="openai",
            served_upstream_provider="azure",
            upstream_key_id="kh_123",
            credential_type="managed",
            status_code=502,
            outcome=AgentccRequestAttempt.Outcome.ERROR,
            error_class="upstream_5xx",
            error_origin="upstream",
            error_message="bad gateway",
            started_at=timezone.now(),
            latency_ms=900,
            ttfb_ms=300,
            prompt_tokens=10,
            completion_tokens=0,
            cached_tokens=2,
            cache_write_tokens=1,
            reasoning_tokens=0,
            upstream_cost_micros=3_000_000_000,
            billed_cost_micros=3_150_000_000,
            landed_cost_micros=3_261_000_000,
            upstream_fee_bps=550,
            processing_fee_bps=320,
            cost_source="provider_reported",
            usage_estimated=True,
            billable=True,
        )

        attempt.refresh_from_db()
        assert attempt.request_log_id == log.id
        assert attempt.billed_cost_micros == 3_150_000_000
        assert attempt.landed_cost_micros == 3_261_000_000
        assert attempt.served_upstream_provider == "azure"
        assert attempt.usage_estimated is True
        assert attempt.created_at is not None

    def test_log_hard_delete_keeps_attempt(self, organization):
        log = _log(organization)
        attempt = _attempt(organization, "req-1.1", request_log=log)

        AgentccRequestLog.all_objects.filter(pk=log.pk).delete()

        attempt.refresh_from_db()
        assert attempt.request_log_id is None

    def test_indexes(self, db):
        assert _index_on("agentcc_request_attempt", ["organization_id", "request_id"])
        assert _index_on("agentcc_request_attempt", ["organization_id", "created_at"])


def _outcome(organization, **fields):
    return AgentccRequestOutcome.objects.create(
        organization=organization,
        request_id=fields.pop("request_id", "req-1"),
        model="gpt-4o-mini",
        outcome=fields.pop("outcome", AgentccRequestOutcome.Outcome.OK),
        source=fields.pop("source", AgentccRequestOutcome.Source.FEEDBACK),
        occurred_at=timezone.now(),
        **fields,
    )


@pytest.mark.integration
class TestRequestOutcome:
    def test_duplicate_event_id_raises(self, organization):
        _outcome(organization, event_id="evt-1")

        with pytest.raises(IntegrityError), transaction.atomic():
            _outcome(organization, event_id="evt-1", request_id="req-2")

    def test_second_classified_row_for_a_request_raises(self, organization):
        classified = AgentccRequestOutcome.Source.CLASSIFIED
        _outcome(organization, source=classified)

        with pytest.raises(IntegrityError), transaction.atomic():
            _outcome(
                organization,
                source=classified,
                outcome=AgentccRequestOutcome.Outcome.BAD,
            )

    def test_feedback_rows_and_other_requests_are_independent(self, organization):
        classified = AgentccRequestOutcome.Source.CLASSIFIED
        _outcome(organization, source=classified)
        _outcome(organization, source=classified, request_id="req-2")
        _outcome(organization, event_id="evt-1")
        _outcome(
            organization,
            event_id="evt-2",
            source=AgentccRequestOutcome.Source.PREV_OUTCOME,
            outcome=AgentccRequestOutcome.Outcome.RETRIED,
            score=0.25,
        )

        assert (
            AgentccRequestOutcome.objects.filter(
                organization=organization, request_id="req-1"
            ).count()
            == 3
        )

    def test_indexes(self, db):
        table = "agentcc_request_outcome"
        assert _index_on(table, ["organization_id", "request_id"])
        assert _index_on(table, ["organization_id", "agent_run_id"])
        assert _index_on(table, ["organization_id", "occurred_at"])


PLAINTEXT_UPSTREAM_KEY = "upstream-test-key-not-a-real-credential"


def _upstream_key(organization, provider_id="openrouter", **fields):
    return AgentccManagedUpstreamKey.objects.create(
        organization=organization,
        provider_id=provider_id,
        encrypted_key=encrypt_token(PLAINTEXT_UPSTREAM_KEY),
        key_hash=fields.pop("key_hash", "kh_123"),
        label="org key",
        **fields,
    )


@pytest.mark.integration
class TestManagedUpstreamKey:
    def test_second_active_key_for_org_and_provider_raises(self, organization):
        _upstream_key(organization)

        with pytest.raises(IntegrityError), transaction.atomic():
            _upstream_key(organization, key_hash="kh_456")

    def test_only_active_keys_are_unique(self, organization):
        status = AgentccManagedUpstreamKey.Status
        _upstream_key(organization, status=status.REVOKED)
        _upstream_key(organization, key_hash="kh_2", status=status.DISABLED)
        _upstream_key(organization, key_hash="kh_3")
        _upstream_key(organization, provider_id="other", key_hash="kh_4")

        active = AgentccManagedUpstreamKey.objects.filter(
            organization=organization, status=status.ACTIVE
        )
        assert active.count() == 2

    def test_repr_and_str_never_show_the_key(self, organization):
        row = _upstream_key(organization, limit_usd=Decimal("25.00"))

        for text in (repr(row), str(row)):
            assert PLAINTEXT_UPSTREAM_KEY not in text
            assert row.encrypted_key not in text
            assert "kh_123" in text
        assert row.encrypted_key.startswith("enc::")
        assert PLAINTEXT_UPSTREAM_KEY not in row.encrypted_key


class TestMigrationChain:
    def test_prism_and_usage_chains(self):
        graph = MigrationLoader(None, ignore_no_migrations=True).graph
        expected = {
            ("prism", "0030_request_log_v2"): (
                "prism",
                "0029_org_config_managed_models",
            ),
            ("prism", "0031_request_log_v2_indexes"): ("prism", "0030_request_log_v2"),
            ("prism", "0032_request_attempt"): ("prism", "0031_request_log_v2_indexes"),
            ("prism", "0033_managed_upstream_key"): ("prism", "0032_request_attempt"),
            ("prism", "0034_request_outcome"): ("prism", "0033_managed_upstream_key"),
            ("usage", "0030_managed_credit_ledger"): (
                "usage",
                "0029_merge_20260806_1623",
            ),
        }
        for child, parent in expected.items():
            assert parent in graph.node_map[child].parents, child
        # One prism leaf that descends from 0034_request_outcome; other migration
        # chains are asserted in their own tests (test_schema_mcp.py).
        (prism_leaf,) = graph.leaf_nodes("prism")
        assert ("prism", "0034_request_outcome") in graph.forwards_plan(prism_leaf)
        assert graph.leaf_nodes("usage") == [("usage", "0030_managed_credit_ledger")]

    def test_index_migration_runs_concurrently_outside_a_transaction(self):
        loader = MigrationLoader(None, ignore_no_migrations=True)
        migration = loader.get_migration("prism", "0031_request_log_v2_indexes")

        assert migration.atomic is False
        added = {op.index.name for op in migration.operations}
        assert added == set(V2_LOG_INDEXES)
        assert all(
            type(op).__name__ == "AddIndexConcurrently" for op in migration.operations
        )


class TestUrlPlaceholders:
    @pytest.mark.parametrize("name,kwargs,path", EXISTING_ROUTES)
    def test_existing_url_names_still_reverse(self, name, kwargs, path):
        assert reverse(name, kwargs=kwargs) == path

    def test_managed_models_catalog_is_not_shadowed(self):
        match = resolve("/agentcc/managed-models/catalog/")

        assert match.url_name == "agentcc-managed-models-catalog"

    @pytest.mark.parametrize("module_name", PLACEHOLDER_URL_MODULES)
    def test_placeholder_is_empty_and_included(self, module_name):
        module = importlib.import_module(module_name)
        agentcc_urls = importlib.import_module("agentcc.urls")
        includes = [
            p
            for p in agentcc_urls.urlpatterns
            if isinstance(p, URLResolver) and p.urlconf_name is module
        ]

        assert module.urlpatterns == []
        assert len(includes) == 1
        assert str(includes[0].pattern) == ""
        # Ahead of the router, so a detail route such as analytics/<pk>/
        # cannot swallow a later explicit path such as analytics/report/.
        first_router_pattern = agentcc_urls.router.urls[0]
        assert agentcc_urls.urlpatterns.index(
            includes[0]
        ) < agentcc_urls.urlpatterns.index(first_router_pattern)


class TestGatewayAdminClient:
    def _client_mock(self, status_code=200, body=b'{"ok": true}'):
        response = MagicMock(status_code=status_code, content=body, text=body.decode())
        response.json.return_value = {"ok": True}
        client = MagicMock()
        client.__enter__.return_value.request.return_value = response
        return client

    def test_request_uses_gateway_url_token_and_timeout(self):
        from agentcc.services import gateway_client
        from agentcc.services.gateway_admin import GatewayAdminClient

        client = self._client_mock()
        with (
            patch.object(
                gateway_client, "AGENTCC_GATEWAY_INTERNAL_URL", "http://gw:8080/"
            ),
            patch.object(gateway_client, "AGENTCC_ADMIN_TOKEN", "admin-test-token"),
            patch.object(gateway_client.httpx, "Client", return_value=client) as ctor,
        ):
            result = GatewayAdminClient().request(
                "PUT", "/-/orgs/o1/credits/granted", json={"version": 2}
            )

        assert result == {"ok": True}
        ctor.assert_called_once_with(timeout=gateway_client.DEFAULT_TIMEOUT)
        call = client.__enter__.return_value.request.call_args
        assert call.args == ("PUT", "http://gw:8080/-/orgs/o1/credits/granted")
        assert call.kwargs["json"] == {"version": 2}
        assert call.kwargs["headers"]["Authorization"] == "Bearer admin-test-token"

    def test_error_status_raises_gateway_client_error(self):
        from agentcc.services.gateway_admin import GatewayAdminClient
        from agentcc.services.gateway_client import GatewayClientError

        client = self._client_mock(status_code=409, body=b'{"error": "stale"}')
        with patch("agentcc.services.gateway_client.httpx.Client", return_value=client):
            with pytest.raises(GatewayClientError) as exc_info:
                GatewayAdminClient().request("GET", "/-/orgs/o1/credits")

        assert exc_info.value.status_code == 409


class TestSchedulePlaceholders:
    def test_placeholders_are_empty_lists(self):
        # agentcc_retention is tested in test_retention.py.
        from tfc.temporal.schedules import agentcc_managed

        assert agentcc_managed.AGENTCC_MANAGED_SCHEDULES == []

    def test_all_schedules_includes_both_lists(self, monkeypatch):
        import tfc.temporal.schedules as schedules
        from tfc.temporal.schedules import agentcc_managed, agentcc_retention

        managed, retention = object(), object()
        monkeypatch.setattr(agentcc_managed, "AGENTCC_MANAGED_SCHEDULES", [managed])
        monkeypatch.setattr(
            agentcc_retention, "AGENTCC_RETENTION_SCHEDULES", [retention]
        )
        try:
            reloaded = importlib.reload(schedules)
            assert managed in reloaded.ALL_SCHEDULES
            assert retention in reloaded.ALL_SCHEDULES
        finally:
            monkeypatch.undo()
            importlib.reload(schedules)
