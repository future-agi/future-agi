"""
Regression tests for calculate_cost_from_tokens with custom-model pricing.

The custom-model settings UI collects "Input/Output Token Cost Per Million
Tokens" and stores it unchanged (model_hub/views/custom_model.py). The cost
calculation must therefore divide by 1_000_000, not 1_000 — see
https://github.com/future-agi/future-agi/issues/2634.
"""

import inspect
import json
from unittest.mock import Mock

import pytest
from django.core.cache import cache

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.models.custom_models import CustomAIModel
from tracer.models.observation_span import ObservationSpan
from tracer.utils.otel import calculate_cost, calculate_cost_from_tokens
from tracer.views.observation_span import _matches_resolved_project


@pytest.mark.django_db
class TestCustomModelCostCalculation:
    def setup_method(self):
        cache.clear()
        self.organization = Organization.objects.create(name="Cost Test Org")
        self.user = User.objects.create_user(
            email="cost-test@example.com", password="password123"
        )
        self.workspace = Workspace.objects.create(
            name="Cost Test Workspace",
            organization=self.organization,
            created_by=self.user,
        )
        self.custom_model = CustomAIModel.objects.create(
            user_model_id="my-custom-llm",
            provider="openai",
            input_token_cost=2.50,
            output_token_cost=10.00,
            organization=self.organization,
            workspace=self.workspace,
            user=self.user,
            key_config={"key": "test-api-key"},
        )

    def teardown_method(self):
        cache.clear()

    def test_custom_model_cost_uses_per_million_token_pricing(self):
        """1M prompt tokens at $2.50/1M input must cost $2.50, not $2500."""
        cost = calculate_cost_from_tokens(
            prompt_tokens=1_000_000,
            completion_tokens=0,
            model="my-custom-llm",
            organization_id=str(self.organization.id),
            project_type="observe",
        )
        assert cost == pytest.approx(2.50)

    def test_custom_model_cost_combines_prompt_and_completion(self):
        cost = calculate_cost_from_tokens(
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
            model="my-custom-llm",
            organization_id=str(self.organization.id),
            project_type="observe",
        )
        assert cost == pytest.approx(2.50 + 10.00)

    def test_custom_model_cost_scales_linearly_below_a_million_tokens(self):
        """1,000 tokens at $2.50 per million cost $0.0025, not $2.50."""
        cost = calculate_cost_from_tokens(
            prompt_tokens=1_000,
            completion_tokens=0,
            model="my-custom-llm",
            organization_id=str(self.organization.id),
            project_type="observe",
        )
        assert cost == pytest.approx(2.50 * 1_000 / 1_000_000)

    @pytest.mark.parametrize("helper", [calculate_cost, calculate_cost_from_tokens])
    @pytest.mark.parametrize("is_observe, expected", [(False, 2500.0), (True, 2.5)])
    def test_project_type_controls_divisor(self, helper, is_observe, expected):
        kwargs = {"attributes": {}} if helper is calculate_cost else {}
        if is_observe:
            kwargs["project_type"] = "observe"
        assert helper(
            prompt_tokens=1_000_000,
            completion_tokens=0,
            model=self.custom_model.user_model_id,
            organization_id=str(self.organization.pk),
            **kwargs,
        ) == pytest.approx(expected)


@pytest.mark.parametrize(
    "given, expected",
    [
        ({}, True),
        ({"project_id": "44CF7AEAE64D43EC86C04F8A1961AFDC"}, True),
        ({"project_id": "44cf7aea-e64d-43ec-86c0-4f8a1961afdc"}, True),
        ({"project_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}, False),
        ({"project_id": None}, False),
        ({"project_id": "not a UUID"}, False),
    ],
)
def test_resolved_project_comparison(given, expected):
    from uuid import UUID

    assert (
        _matches_resolved_project(given, UUID("44cf7aeae64d43ec86c04f8a1961afdc"))
        is expected
    )


@pytest.fixture
def custom_rate(observe_project, user):
    return CustomAIModel.objects.create(
        organization=observe_project.organization,
        workspace=observe_project.workspace,
        user=user,
        user_model_id="parcel-cost-fixture",
        provider="openai",
        input_token_cost=2.50,
        output_token_cost=10,
    )


@pytest.mark.django_db
def test_observe_bulk_create_estimates_only_missing_cost(
    auth_client, observe_project, session_trace, project, trace, custom_rate
):
    span = {
        "project": str(observe_project.pk),
        "trace": str(session_trace.pk),
        "observation_type": "llm",
        "model": custom_rate.user_model_id,
        "prompt_tokens": 1_000_000,
    }
    response = auth_client.post(
        "/tracer/observation-span/bulk_create/",
        {
            "spans": [
                {**span, "name": "estimate"},
                {**span, "name": "reported zero", "cost": 0},
                {**span, "name": "reported nonzero", "cost": 0.75},
                {
                    **span,
                    "project": str(project.pk),
                    "trace": str(trace.pk),
                    "name": "experiment control",
                },
            ]
        },
        format="json",
    )
    assert response.status_code == 200
    assert dict(
        ObservationSpan.objects.filter(trace=session_trace).values_list("name", "cost")
    ) == {"estimate": 2.50, "reported zero": 0, "reported nonzero": 0.75}
    assert (
        ObservationSpan.objects.get(trace=trace, name="experiment control").cost == 2500
    )


@pytest.mark.django_db(transaction=True)
def test_observe_bulk_project_identity_over_http(
    live_server, user, workspace, observe_project, project, session_trace, custom_rate
):
    from contextlib import closing
    from unittest.mock import patch

    import requests
    from django.utils import timezone

    from accounts.authentication import generate_encrypted_message
    from accounts.models.auth_token import AuthToken, AuthTokenType
    from conftest import _open_ch_test_http_client
    from ee.usage.models.usage import UsageEventLog, UsageSummary
    from ee.usage.services.emitter import STREAM_KEY, get_redis
    from integrations.models.sync_log import SyncLog
    from tfc.utils.api_errors import build_error_envelope
    from tracer.models.trace_grouping import TraceGroupingOutbox

    token = AuthToken.objects.create(
        user=user, auth_type=AuthTokenType.ACCESS.value, last_used_at=timezone.now()
    )
    bearer = generate_encrypted_message({"user_id": str(user.pk), "id": str(token.pk)})
    headers = {
        "Authorization": f"Bearer {bearer}",
        "X-Workspace-Id": str(workspace.pk),
        "X-Organization-Id": str(workspace.organization_id),
    }
    url = f"{live_server.url}/tracer/observation-span/bulk_create/"
    span = {
        "name": "estimate",
        "project": str(observe_project.pk),
        "trace": str(session_trace.pk),
        "observation_type": "llm",
        "prompt_tokens": 1_000_000,
        "model": custom_rate.user_model_id,
    }

    def post(rows):
        return requests.post(url, json={"spans": rows}, headers=headers, timeout=15)

    # Valid requests use the real insert path; check the three fields together.
    rows = [
        span,
        {
            **span,
            "project_id": observe_project.pk.hex.upper(),
            "name": "free",
            "cost": 0,
        },
        {
            **span,
            "project_id": str(observe_project.pk),
            "name": "reported",
            "cost": 0.75,
        },
    ]
    assert post(rows).status_code == 200
    stored = ObservationSpan._base_manager.filter(trace=session_trace).values_list(
        "name", "project_id", "trace_id", "cost"
    )
    assert set(stored) == {
        (name, observe_project.pk, session_trace.pk, cost)
        for name, cost in [("estimate", 2.5), ("free", 0), ("reported", 0.75)]
    }

    def snapshot(ch):
        tables = (
            ObservationSpan,
            UsageEventLog,
            UsageSummary,
            SyncLog,
            TraceGroupingOutbox,
        )
        redis = get_redis()
        return (
            [
                list(table._base_manager.order_by("pk").values_list("pk", flat=True))
                for table in tables
            ],
            sorted(redis.scan_iter(match="usage:*")),
            redis.xrange(STREAM_KEY),
            ch.query(
                "SELECT project_id, trace_id, id FROM spans ORDER BY project_id, trace_id, id"
            ).result_rows,
        )

    with closing(_open_ch_test_http_client(database="test_tfc")) as ch:
        before = snapshot(ch)
        # Reject during validation: even attempting persistence is a failure.
        projects = type(project).objects
        with (
            patch.object(
                ObservationSpan.objects, "bulk_create", side_effect=AssertionError
            ) as insert,
            patch.object(projects, "get", wraps=projects.get) as lookup,
        ):
            conflict = {**span, "project_id": str(project.pk)}
            for batch in ([conflict], [span, conflict]):
                response = post(batch)
                assert response.status_code == 400
                assert response.json() == build_error_envelope(
                    "project_id must match project"
                )
                insert.assert_not_called()
                assert [call.kwargs["id"] for call in lookup.call_args_list] == [
                    str(observe_project.pk)
                ] * len(batch)
                lookup.reset_mock()
                assert snapshot(ch) == before


@pytest.mark.django_db
@pytest.mark.parametrize("cost", ["NaN", "Infinity", "-Infinity", "1e9999"])
def test_observe_bulk_rejects_nonfinite_cost(
    auth_client, observe_project, session_trace, cost
):
    span = {
        "project": str(observe_project.pk),
        "trace": str(session_trace.pk),
        "observation_type": "llm",
        "name": "finite cost check",
    }
    response = auth_client.post(
        "/tracer/observation-span/bulk_create/",
        {"spans": [{**span, "cost": 0}, {**span, "cost": cost}]},
        format="json",
    )
    assert response.status_code == 400
    assert not ObservationSpan.objects.filter(
        trace=session_trace, name=span["name"]
    ).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("cost", [True, False])
def test_observe_bulk_rejects_boolean_cost_atomically(
    auth_client, observe_project, session_trace, cost
):
    span = {
        "project": str(observe_project.pk),
        "trace": str(session_trace.pk),
        "observation_type": "llm",
        "name": f"boolean cost check {cost}",
    }
    response = auth_client.post(
        "/tracer/observation-span/bulk_create/",
        {"spans": [{**span, "cost": 0.75}, {**span, "cost": cost}]},
        format="json",
    )
    assert response.status_code == 400
    assert not ObservationSpan.objects.filter(
        trace=session_trace, name=span["name"]
    ).exists()


@pytest.mark.django_db
def test_otlp_bulk_activity_persists_cost_and_reports_usage(
    observe_project, session_trace, custom_rate, user, monkeypatch
):
    from tracer.utils import trace_ingestion

    def attr(key, kind, value):
        return {"key": key, "value": {kind: value}}

    spans = []
    for i, cost in enumerate((None, 0, 0.75), start=1):
        attrs = [
            attr("gen_ai.operation.name", "string_value", "chat"),
            attr("gen_ai.request.model", "string_value", custom_rate.user_model_id),
            attr("gen_ai.usage.input_tokens", "int_value", 1_000_000),
        ]
        if cost is not None:
            attrs.append(attr("gen_ai.cost.total", "double_value", cost))
        spans.append(
            {
                "trace_id": session_trace.pk.hex,
                "span_id": f"{i:016x}",
                "name": f"span-{i}",
                "start_time_unix_nano": 1_750_000_000_000_000_000,
                "end_time_unix_nano": 1_750_000_000_001_000_000,
                "attributes": attrs,
            }
        )
    resource = {
        "attributes": [
            attr("project_name", "string_value", observe_project.name),
            attr("project_type", "string_value", "observe"),
        ]
    }
    payload = json.dumps(
        {"resource_spans": [{"resource": resource, "scope_spans": [{"spans": spans}]}]}
    ).encode()
    monkeypatch.setattr(
        trace_ingestion.payload_storage, "retrieve", lambda key: payload
    )
    emit = Mock()
    monkeypatch.setattr(trace_ingestion, "emit_span_ingestion_usage", emit)
    org = str(observe_project.organization_id)
    # Run the actual activity body inside the pytest-managed database transaction.
    inspect.unwrap(trace_ingestion.bulk_create_observation_span_task)(
        "rate-test", org, str(user.pk), str(observe_project.workspace_id)
    )
    assert dict(
        ObservationSpan.objects.filter(trace=session_trace).values_list("name", "cost")
    ) == {"span-1": 2.5, "span-2": 0, "span-3": 0.75}
    emit.assert_called_once_with(
        organization_id=org,
        num_traces=1,
        num_spans=3,
        payload_bytes=len(payload),
        source="trace_span",
    )
