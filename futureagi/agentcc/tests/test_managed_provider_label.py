"""Customer screens name a
managed call's provider "Future AGI managed"; the upstream the gateway bought
it from shows only in the request's technical attempt details.

The label follows a request's billing mode, never the provider's name: an
org's own OpenRouter key is an own-key provider and keeps "openrouter".
These tests pin the server-made views of request logs: analytics grouped by
provider or billing mode, the model comparison, the Logs export, and the
Logs provider filter and search.
"""

import csv
import io
import json
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework import status

from agentcc.models import AgentccRequestLog

MANAGED = "Future AGI"
# One name per billing mode on every screen: the billing
# mode groups are "Managed" and "Own key", the console's pair. A managed
# call's provider is still "Future AGI managed".
MANAGED_MODE = "Future AGI credits"
OWN_KEY = "Own key"
MODEL = "openai/gpt-6-sol"


@pytest.fixture
def billing_logs(organization, workspace):
    """Three managed calls and two own-key calls of one model, both on the
    provider id openrouter; two own-key Anthropic calls (one an error); one
    legacy call that recorded no billing mode."""
    now = timezone.now()
    rows = [
        # request_id, model, provider, billing_mode, cost, is_error, latency
        ("m-1", MODEL, "openrouter", "managed", "0.001000", False, 400),
        ("m-2", MODEL, "openrouter", "managed", "0.002000", False, 500),
        ("m-3", MODEL, "openrouter", "managed", "0.000000", True, 50),
        ("b-1", MODEL, "openrouter", "byok", "0.004000", False, 300),
        ("b-2", MODEL, "openrouter", "byok", "0.004000", False, 350),
        ("a-1", "claude-3", "anthropic", "byok", "0.003000", False, 600),
        ("a-2", "claude-3", "anthropic", "byok", "0.000000", True, 70),
        ("l-1", "gpt-4", "openai", None, "0.005000", False, 800),
    ]
    logs = []
    for request_id, model, provider, mode, cost, is_error, latency in rows:
        logs.append(
            AgentccRequestLog.objects.create(
                organization=organization,
                workspace=workspace,
                request_id=request_id,
                model=model,
                provider=provider,
                billing_mode=mode,
                status_code=502 if is_error else 200,
                latency_ms=latency,
                cost=Decimal(cost),
                input_tokens=10,
                output_tokens=10,
                total_tokens=20,
                is_error=is_error,
                error_message=f"upstream error on {request_id}" if is_error else "",
                started_at=now,
            )
        )
    return logs


def _result(response):
    assert response.status_code == status.HTTP_200_OK, response.content
    return response.json()["result"]


def _group_totals(result):
    return {
        name: sum(bucket["request_count"] for bucket in series)
        for name, series in result["groups"].items()
    }


@pytest.mark.integration
@pytest.mark.api
class TestAnalyticsByProviderAndBillingMode:
    def test_usage_by_provider_reports_managed_as_future_agi_managed(
        self, auth_client, billing_logs
    ):
        result = _result(
            auth_client.get("/agentcc/analytics/usage-timeseries/?group_by=provider")
        )
        assert result["group_by"] == "provider"
        assert _group_totals(result) == {
            MANAGED: 3,
            "openrouter": 2,  # the org's own OpenRouter key keeps its name
            "anthropic": 2,
            "openai": 1,
        }

    def test_usage_by_billing_mode(self, auth_client, billing_logs):
        result = _result(
            auth_client.get(
                "/agentcc/analytics/usage-timeseries/?group_by=billing_mode"
            )
        )
        assert result["group_by"] == "billing_mode"
        assert _group_totals(result) == {MANAGED_MODE: 3, OWN_KEY: 4, "unknown": 1}

    def test_cost_by_provider_and_by_billing_mode(self, auth_client, billing_logs):
        by_provider = _result(
            auth_client.get("/agentcc/analytics/cost-breakdown/?group_by=provider")
        )
        assert by_provider["group_by"] == "provider"
        costs = {row["name"]: row for row in by_provider["breakdown"]}
        assert set(costs) == {MANAGED, "openrouter", "anthropic", "openai"}
        assert costs[MANAGED]["request_count"] == 3
        assert Decimal(costs[MANAGED]["total_cost"]) == Decimal("0.003")
        assert Decimal(costs["openrouter"]["total_cost"]) == Decimal("0.008")

        by_mode = _result(
            auth_client.get("/agentcc/analytics/cost-breakdown/?group_by=billing_mode")
        )
        assert by_mode["group_by"] == "billing_mode"
        modes = {row["name"]: row["request_count"] for row in by_mode["breakdown"]}
        assert modes == {MANAGED_MODE: 3, OWN_KEY: 4, "unknown": 1}

    def test_errors_by_provider_and_by_billing_mode(self, auth_client, billing_logs):
        by_provider = _result(
            auth_client.get("/agentcc/analytics/error-breakdown/?group_by=provider")
        )
        rows = {row["name"]: row for row in by_provider["breakdown"]}
        assert set(rows) == {MANAGED, "anthropic"}
        # The sample message is the group's own, not another provider's.
        assert rows[MANAGED]["sample_error_message"] == "upstream error on m-3"
        assert rows["anthropic"]["sample_error_message"] == "upstream error on a-2"

        by_mode = _result(
            auth_client.get("/agentcc/analytics/error-breakdown/?group_by=billing_mode")
        )
        assert by_mode["group_by"] == "billing_mode"
        rows = {row["name"]: row for row in by_mode["breakdown"]}
        assert {name: row["error_count"] for name, row in rows.items()} == {
            MANAGED_MODE: 1,
            OWN_KEY: 1,
        }
        assert rows[MANAGED_MODE]["sample_error_message"] == "upstream error on m-3"
        assert rows[OWN_KEY]["sample_error_message"] == "upstream error on a-2"

    def test_model_comparison_names_the_dominant_provider_by_billing_mode(
        self, auth_client, billing_logs
    ):
        result = _result(auth_client.get("/agentcc/analytics/model-comparison/"))
        rows = {row["model"]: row for row in result["models"]}
        # 3 managed calls outnumber 2 on the org's own OpenRouter key.
        assert rows[MODEL]["provider"] == MANAGED
        assert rows[MODEL]["billing_mode"] == "managed"
        assert rows[MODEL]["request_count"] == 5
        assert rows["claude-3"]["provider"] == "anthropic"
        assert rows["claude-3"]["billing_mode"] == "byok"
        assert rows["gpt-4"]["provider"] == "openai"
        assert rows["gpt-4"]["billing_mode"] is None

    def test_model_comparison_grouped_by_billing_mode(self, auth_client, billing_logs):
        result = _result(
            auth_client.get(
                "/agentcc/analytics/model-comparison/?group_by=billing_mode"
            )
        )
        assert result["group_by"] == "billing_mode"
        rows = {(row["model"], row["billing_mode"]): row for row in result["models"]}
        assert set(rows) == {
            (MODEL, "managed"),
            (MODEL, "byok"),
            ("claude-3", "byok"),
            ("gpt-4", None),
        }
        managed, own = rows[(MODEL, "managed")], rows[(MODEL, "byok")]
        assert (managed["provider"], managed["request_count"]) == (MANAGED, 3)
        assert (own["provider"], own["request_count"]) == ("openrouter", 2)
        assert managed["error_count"] == 1 and own["error_count"] == 0
        assert Decimal(own["total_cost"]) == Decimal("0.008")

    def test_model_comparison_default_group_is_model(self, auth_client, billing_logs):
        result = _result(auth_client.get("/agentcc/analytics/model-comparison/"))
        assert result["group_by"] == "model"
        assert len(result["models"]) == 3


def _own_key_credential(organization, provider_name):
    from agentcc.models.provider_credential import AgentccProviderCredential
    from integrations.services.credentials import CredentialManager

    return AgentccProviderCredential.no_workspace_objects.create(
        organization=organization,
        provider_name=provider_name,
        display_name=f"Own {provider_name}",
        encrypted_credentials=CredentialManager.encrypt({"api_key": "sk-test-own"}),
        api_format="openai",
        models_list=[MODEL],
    )


@pytest.mark.integration
@pytest.mark.api
class TestMonitoringOwnKeyProviders:
    """Monitoring's "Your own provider keys" table: one row per own
    credential, with that key's own traffic. Managed calls on the same
    provider id are Future AGI's (they count in the totals above the table),
    so an org's own OpenRouter key never shows them."""

    @staticmethod
    def _providers(auth_client):
        from unittest.mock import MagicMock, patch

        client = MagicMock()
        client.provider_health.return_value = {"providers": {}}
        with patch("agentcc.views.gateway.get_gateway_client", return_value=client):
            response = auth_client.get("/agentcc/gateways/default/providers/")
        rows = _result(response)["providers"]
        return {row["id"]: row for row in rows}

    def test_own_openrouter_key_counts_only_its_own_calls(
        self, auth_client, organization, workspace, billing_logs
    ):
        _own_key_credential(organization, "openrouter")
        # A call recorded before billing modes were stored: no mode, so it is
        # not known to be managed and stays on the key's row.
        AgentccRequestLog.objects.create(
            organization=organization,
            workspace=workspace,
            request_id="l-2",
            model=MODEL,
            provider="openrouter",
            billing_mode=None,
            status_code=200,
            latency_ms=250,
            cost=Decimal("0.001"),
            started_at=timezone.now(),
        )

        row = self._providers(auth_client)["openrouter"]
        # b-1, b-2 (own key) and l-2 (legacy); never m-1..m-3 (managed).
        assert row["request_count"] == 3
        assert row["avg_latency"] == 300.0  # (300 + 350 + 250) / 3
        assert row["error_rate"] == 0.0  # the managed m-3 error is not the key's

    def test_other_own_keys_are_unchanged(
        self, auth_client, organization, billing_logs
    ):
        _own_key_credential(organization, "anthropic")
        _own_key_credential(organization, "openai")

        rows = self._providers(auth_client)
        assert rows["anthropic"]["request_count"] == 2
        assert rows["anthropic"]["error_rate"] == 50.0
        assert rows["openai"]["request_count"] == 1  # legacy, no billing mode


def _csv_rows(response):
    body = b"".join(response.streaming_content).decode()
    return list(csv.reader(io.StringIO(body)))


@pytest.mark.integration
@pytest.mark.api
class TestLogsExportProvider:
    def test_csv_names_managed_rows_future_agi_managed(self, auth_client, billing_logs):
        rows = _csv_rows(
            auth_client.get("/agentcc/request-logs/export/?export_format=csv")
        )
        header = rows[0]
        provider = header.index("provider")
        by_request = {row[header.index("request_id")]: row for row in rows[1:]}
        assert {by_request[r][provider] for r in ("m-1", "m-2", "m-3")} == {MANAGED}
        assert by_request["b-1"][provider] == "openrouter"
        assert by_request["a-1"][provider] == "anthropic"
        assert by_request["l-1"][provider] == "openai"
        # The export has no upstream column: the upstream shows only in the
        # request's attempt details.
        assert not any("openrouter" in by_request[r] for r in ("m-1", "m-2", "m-3"))

    def test_json_names_managed_rows_future_agi_managed(
        self, auth_client, billing_logs
    ):
        response = auth_client.get("/agentcc/request-logs/export/?export_format=json")
        body = b"".join(response.streaming_content).decode()
        rows = {
            row["request_id"]: row
            for row in (json.loads(line) for line in body.splitlines() if line)
        }
        assert rows["m-1"]["provider"] == MANAGED
        assert rows["b-1"]["provider"] == "openrouter"
        assert rows["l-1"]["provider"] == "openai"


def _legacy_openrouter_row(organization, workspace):
    """An own-key OpenRouter call recorded before billing modes: none stored."""
    return AgentccRequestLog.objects.create(
        organization=organization,
        workspace=workspace,
        request_id="l-2",
        model="mistral-small",
        provider="openrouter",
        billing_mode=None,
        status_code=200,
        latency_ms=250,
        cost=Decimal("0.001"),
        started_at=timezone.now(),
    )


def _request_ids(response):
    assert response.status_code == status.HTTP_200_OK, response.content
    return {row["request_id"] for row in response.json()["results"]}


@pytest.mark.integration
@pytest.mark.api
class TestLogsProviderFilter:
    @pytest.mark.parametrize("label", [MANAGED, "Future AGI managed"])
    def test_provider_filter_accepts_future_agi_managed(
        self, auth_client, billing_logs, label
    ):
        ids = _request_ids(
            auth_client.get("/agentcc/request-logs/", {"provider": label})
        )
        assert ids == {"m-1", "m-2", "m-3"}

    def test_label_is_matched_without_case_or_extra_spaces(
        self, auth_client, billing_logs
    ):
        ids = _request_ids(
            auth_client.get(
                "/agentcc/request-logs/", {"provider": "  future AGI  Managed "}
            )
        )
        assert ids == {"m-1", "m-2", "m-3"}

    def test_label_combines_with_provider_names(self, auth_client, billing_logs):
        ids = _request_ids(
            auth_client.get(
                "/agentcc/request-logs/", {"provider": f"{MANAGED},anthropic"}
            )
        )
        assert ids == {"m-1", "m-2", "m-3", "a-1", "a-2"}

    def test_provider_id_leaves_out_managed_rows(
        self, auth_client, organization, workspace, billing_logs
    ):
        # The filter matches the Provider column it filters: a managed row
        # reads "Future AGI managed" there, so the upstream's id does not find
        # it. A row recorded before
        # billing modes (none stored) is not known to be managed and stays.
        _legacy_openrouter_row(organization, workspace)
        ids = _request_ids(
            auth_client.get("/agentcc/request-logs/", {"provider": "openrouter"})
        )
        assert ids == {"b-1", "b-2", "l-2"}

    def test_provider_id_and_label_together_find_both(self, auth_client, billing_logs):
        ids = _request_ids(
            auth_client.get(
                "/agentcc/request-logs/", {"provider": f"openrouter,{MANAGED}"}
            )
        )
        assert ids == {"m-1", "m-2", "m-3", "b-1", "b-2"}

    def test_export_and_volume_leave_out_managed_rows_for_a_provider_id(
        self, auth_client, billing_logs
    ):
        rows = _csv_rows(
            auth_client.get(
                "/agentcc/request-logs/export/",
                {"export_format": "csv", "provider": "openrouter"},
            )
        )
        assert sorted(row[1] for row in rows[1:]) == ["b-1", "b-2"]

        now = timezone.now()
        volume = _result(
            auth_client.get(
                "/agentcc/request-logs/volume/",
                {
                    "provider": "openrouter",
                    "start": (now - timedelta(hours=1)).isoformat(),
                    "end": (now + timedelta(hours=1)).isoformat(),
                    "granularity": "hour",
                },
            )
        )
        assert sum(bucket["request_count"] for bucket in volume["series"]) == 2

    def test_export_and_volume_follow_the_label_filter(self, auth_client, billing_logs):
        rows = _csv_rows(
            auth_client.get(
                "/agentcc/request-logs/export/",
                {"export_format": "csv", "provider": MANAGED},
            )
        )
        assert sorted(row[1] for row in rows[1:]) == ["m-1", "m-2", "m-3"]

        now = timezone.now()
        volume = _result(
            auth_client.get(
                "/agentcc/request-logs/volume/",
                {
                    "provider": MANAGED,
                    "start": (now - timedelta(hours=1)).isoformat(),
                    "end": (now + timedelta(hours=1)).isoformat(),
                    "granularity": "hour",
                },
            )
        )
        assert sum(bucket["request_count"] for bucket in volume["series"]) == 3

    def test_search_finds_managed_rows_by_their_label(self, auth_client, billing_logs):
        ids = _request_ids(
            auth_client.get("/agentcc/request-logs/", {"q": "future agi"})
        )
        assert ids == {"m-1", "m-2", "m-3"}

    @pytest.mark.parametrize("query", ["openrouter", "openrou", "OpenRouter"])
    def test_search_for_the_upstream_leaves_out_managed_rows(
        self, auth_client, organization, workspace, billing_logs, query
    ):
        # Search matches the provider as shown: a managed row's model,
        # request id and error still match, but not the upstream's id.
        _legacy_openrouter_row(organization, workspace)
        ids = _request_ids(auth_client.get("/agentcc/request-logs/", {"q": query}))
        assert ids == {"b-1", "b-2", "l-2"}

    @pytest.mark.parametrize(
        ("query", "expected"), [
            ("fut", {"m-1", "m-2", "m-3"}),
            ("future", {"m-1", "m-2", "m-3"}),
            ("agi", {"m-1", "m-2", "m-3"}),
            ("future agi", {"m-1", "m-2", "m-3"}),
            ("man", set()), ("managed", set()), ("AGI Managed", set()),
        ]
    )
    def test_search_matches_the_label_from_the_start_of_a_word(
        self, auth_client, billing_logs, query, expected
    ):
        ids = _request_ids(auth_client.get("/agentcc/request-logs/", {"q": query}))
        assert ids == expected

    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            # Inside a word of the label, or under three characters: only the
            # rows that match on their own fields.
            ("ed", set()),
            ("ag", set()),
            ("fu", set()),
            ("ure", set()),
            ("aged", set()),
            ("re", {"m-3", "a-2"}),  # "upstream error on ..."
            ("an", {"a-1", "a-2"}),  # anthropic
        ],
    )
    def test_search_does_not_match_inside_the_label(
        self, auth_client, billing_logs, query, expected
    ):
        ids = _request_ids(auth_client.get("/agentcc/request-logs/", {"q": query}))
        assert ids == expected


# The auto decision the gateway records: each ranked candidate, and the
# decision itself, say how a choice of it is paid for ("managed" or "byok";
# none for a candidate on the gateway's own credentials, which bills the org
# neither way). Django stores and serves it as the gateway wrote it.
AUTO_DECISION = {
    "model": MODEL,
    "provider": "openrouter",
    "billing": "managed",
    "ranked": [
        {"model": MODEL, "provider": "openrouter", "billing": "managed"},
        {"model": "openai/gpt-6-sol", "provider": "openrouter", "billing": "byok"},
        {"model": "gpt-4o", "provider": "operator"},
    ],
}


@pytest.mark.integration
def test_auto_decision_candidate_billing_is_stored_and_served(organization):
    from agentcc.models import AgentccAPIKey
    from agentcc.serializers.request_log import (
        AgentccRequestLogDetailSerializer,
        AgentccRequestLogSerializer,
    )
    from agentcc.services.log_ingestion import ingest_request_logs
    from agentcc.tests.test_log_ingestion import (
        GOLDEN_KEY_ID,
        GOLDEN_REQUEST_ID,
        golden_entry,
    )

    AgentccAPIKey.objects.create(
        gateway_key_id=GOLDEN_KEY_ID, name="golden-key", organization=organization
    )
    raw = json.dumps(AUTO_DECISION, separators=(",", ":"))
    entry = golden_entry()
    entry["metadata"] = {**entry["metadata"], "auto_model": MODEL, "auto_decision": raw}

    assert ingest_request_logs([entry]) == 1

    log = AgentccRequestLog.no_workspace_objects.get(request_id=GOLDEN_REQUEST_ID)
    for serializer in (AgentccRequestLogSerializer, AgentccRequestLogDetailSerializer):
        served = json.loads(serializer(log).data["metadata"]["auto_decision"])
        assert served["billing"] == "managed"
        assert [r.get("billing") for r in served["ranked"]] == ["managed", "byok", None]
