"""Request-log API v2: the detail endpoint's attempts, billing, agent and
routing blocks, the v2 list filters, and the golden log entries ingestion reads."""

import copy

import pytest
from jsonschema import Draft202012Validator
from rest_framework import status

from accounts.models import Organization
from agentcc.models import AgentccAPIKey
from agentcc.models.request_attempt import AgentccRequestAttempt
from agentcc.services.log_ingestion import ingest_request_logs
from agentcc.tests.test_log_ingestion import (
    GOLDEN_KEY_ID,
    GOLDEN_REQUEST_ID,
    cache_hit_entry,
    golden_entry,
    load_example,
    v1_entry,
)

LOGS_URL = "/agentcc/request-logs/"
STAFF_ONLY_ATTEMPT_FIELDS = {
    "landed_cost_micros",
    "upstream_fee_bps",
    "processing_fee_bps",
}


@pytest.fixture
def gateway_key(organization):
    return AgentccAPIKey.objects.create(
        gateway_key_id=GOLDEN_KEY_ID, name="golden-key", organization=organization
    )


def _detail(client, request_id):
    listed = client.get(LOGS_URL, {"request_id": request_id})
    assert listed.status_code == status.HTTP_200_OK
    (row,) = listed.json()["results"]
    response = client.get(f"{LOGS_URL}{row['id']}/")
    assert response.status_code == status.HTTP_200_OK
    return response.json()["result"]


def _listed_ids(client, **params):
    response = client.get(LOGS_URL, params)
    assert response.status_code == status.HTTP_200_OK, response.content
    return sorted(row["request_id"] for row in response.json()["results"])


def _byok_entry():
    """A second v2 request: BYOK, another run and step, one successful attempt."""
    entry = golden_entry(
        request_id="req_byok_1",
        generation_id="req_byok_1",
        billing_mode="byok",
        credential_type="byok",
        cost_landed=None,
    )
    entry["agent"] = {
        **entry["agent"],
        "agent_id": "writer",
        "run_id": "run_X",
        "step": "plan",
        "env": "staging",
    }
    entry["routing"] = {**entry["routing"], "policy_variant": "candidate"}
    attempt = {
        **entry["attempts"][1],
        "attempt_id": "req_byok_1.1",
        "index": 1,
        "kind": "primary",
        "reason": "",
        "credential_type": "byok",
    }
    entry["attempts"] = [attempt]
    entry["attempt_count"] = 1
    return entry


@pytest.mark.integration
@pytest.mark.api
class TestRequestLogDetail:
    def test_detail_attempts_ordered(self, auth_client, gateway_key):
        entry = golden_entry()
        entry["attempts"].reverse()  # arrival order is not display order
        ingest_request_logs([entry])

        detail = _detail(auth_client, GOLDEN_REQUEST_ID)

        assert [a["index"] for a in detail["attempts"]] == [1, 2]
        assert [a["kind"] for a in detail["attempts"]] == ["primary", "auto_fallback"]
        first = detail["attempts"][0]
        assert first["attempt_id"] == f"{GOLDEN_REQUEST_ID}.1"
        assert (first["error_class"], first["error_origin"], first["status_code"]) == (
            "rate_limited",
            "upstream",
            429,
        )
        assert detail["attempts"][1]["billed_cost_micros"] == 11815
        assert detail["attempt_count"] == 2
        assert detail["billing"] == {
            "mode": "managed",
            "credential_type": "managed",
            "cost_upstream": "0.01125200",
            "cost_markup": "0.00056300",
            "billed": "0.011815",
            "cost_source": "provider_reported",
            "usage_estimated": False,
        }
        assert detail["agent"] == {
            "framework": "omega",
            "agent_id": "researcher",
            "agent_version": "3",
            "run_id": "run_R2",
            "parent_run_id": "run_R1",
            "root_run_id": "run_R1",
            "task_id": "task_17",
            "workflow": "deep-research",
            "step": "act",
            "step_source": "header",
            "turn": 4,
            "env": "production",
            "budget_scope": "run_R1",
            "identity_source": "runtime_token",
        }
        assert detail["routing"] == {
            "policy_version": "v12",
            "policy_variant": "stable",
            "catalog_snapshot_id": "3f9a1c0b2d4e",
            "auto_routed": True,
        }
        assert detail["guardrail"] == {
            "tool_calls": {"checked": 2, "blocked": [], "stripped": []}
        }

    def test_cost_landed_is_staff_only(self, auth_client, user, gateway_key):
        ingest_request_logs([golden_entry()])

        detail = _detail(auth_client, GOLDEN_REQUEST_ID)
        assert "cost_landed" not in detail
        assert "cost_landed" not in detail["billing"]
        for attempt in detail["attempts"]:
            assert STAFF_ONLY_ATTEMPT_FIELDS.isdisjoint(attempt)
        listed = auth_client.get(LOGS_URL).json()["results"]
        assert all("cost_landed" not in row for row in listed)

        user.is_staff = True
        user.save(update_fields=["is_staff"])
        staff_detail = _detail(auth_client, GOLDEN_REQUEST_ID)
        assert staff_detail["billing"]["cost_landed"] == "0.01224900"
        assert staff_detail["attempts"][1]["landed_cost_micros"] == 12249
        assert staff_detail["attempts"][1]["upstream_fee_bps"] == 550

    def test_v1_row_detail_has_empty_v2_blocks(self, auth_client, gateway_key):
        ingest_request_logs([v1_entry("req_v1_detail")])

        detail = _detail(auth_client, "req_v1_detail")

        assert detail["attempts"] == []
        assert detail["attempt_count"] == 1
        assert detail["billing"]["mode"] is None
        assert detail["agent"]["run_id"] is None
        assert detail["routing"]["auto_routed"] is None
        assert detail["guardrail"] is None

    def test_detail_attempts_stay_in_their_org(self, auth_client, gateway_key):
        ingest_request_logs([golden_entry()])
        other = Organization.objects.create(name="Other Org")
        AgentccRequestAttempt.objects.create(
            organization=other,
            request_id=GOLDEN_REQUEST_ID,
            attempt_id="other-org-attempt",
            index=3,
            kind="retry",
            outcome="error",
        )

        detail = _detail(auth_client, GOLDEN_REQUEST_ID)

        assert [a["index"] for a in detail["attempts"]] == [1, 2]


@pytest.mark.integration
@pytest.mark.api
class TestRequestLogFilters:
    def test_filters_billing_mode_run_id_step_error_class(
        self, auth_client, gateway_key
    ):
        ingest_request_logs([golden_entry(), _byok_entry(), v1_entry("req_v1_filters")])
        managed, byok = [GOLDEN_REQUEST_ID], ["req_byok_1"]

        assert _listed_ids(auth_client, billing_mode="managed") == managed
        assert _listed_ids(auth_client, billing_mode="byok") == byok
        assert _listed_ids(auth_client, billing_mode="managed,byok") == sorted(
            managed + byok
        )
        assert _listed_ids(auth_client, credential_type="byok") == byok
        assert _listed_ids(auth_client, run_id="run_R2") == managed
        assert _listed_ids(auth_client, run_id="run_X") == byok
        assert _listed_ids(auth_client, agent_id="writer") == byok
        assert _listed_ids(auth_client, step="act") == managed
        assert _listed_ids(auth_client, step="plan") == byok
        assert _listed_ids(auth_client, policy_variant="candidate") == byok
        assert _listed_ids(auth_client, env="production") == managed
        # error_class matches a request with any attempt of that class.
        assert _listed_ids(auth_client, error_class="rate_limited") == managed
        assert _listed_ids(auth_client, error_class="timeout,rate_limited") == managed
        assert _listed_ids(auth_client, error_class="timeout") == []
        # Filters combine with each other and with the v1 ones.
        assert _listed_ids(auth_client, billing_mode="managed", step="plan") == []
        assert _listed_ids(auth_client, run_id="run_R2", model="auto") == managed

    def test_error_class_filter_reads_only_the_orgs_attempts(
        self, auth_client, gateway_key
    ):
        ingest_request_logs([_byok_entry()])
        other = Organization.objects.create(name="Other Org")
        AgentccRequestAttempt.objects.create(
            organization=other,
            request_id="req_byok_1",
            attempt_id="other-org-timeout",
            index=2,
            kind="retry",
            outcome="timeout",
            error_class="timeout",
        )

        assert _listed_ids(auth_client, error_class="timeout") == []

    def test_search_and_list_rows_carry_v2_columns(self, auth_client, gateway_key):
        ingest_request_logs([golden_entry()])

        response = auth_client.get(f"{LOGS_URL}search/", {"q": "req_01", "step": "act"})
        assert response.status_code == status.HTTP_200_OK
        (row,) = response.json()["results"]
        assert row["billing_mode"] == "managed"
        assert row["credential_type"] == "managed"
        assert row["cost_upstream"] == "0.01125200"
        assert row["cost_markup"] == "0.00056300"
        assert row["attempt_count"] == 2
        assert row["agent_id"] == "researcher"
        assert row["agent_run_id"] == "run_R2"
        assert row["agent_step"] == "act"
        assert row["generation_id"] == GOLDEN_REQUEST_ID


@pytest.mark.integration
def test_golden_schema_valid():
    """The golden log entries, and the variants these tests derive from them, are
    on contract: a cache hit as the flusher sends it (no attempts, no
    attempt_count) needs no schema change."""
    for name, examples in (
        (
            "log-entry-v2",
            [
                load_example("log-entry-v2.json"),
                cache_hit_entry(),
                _byok_entry(),
            ],
        ),
        ("outcome-event-v1", [load_example("outcome-event-v1.json")]),
    ):
        schema = load_example(f"{name}.schema.json")
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        for example in examples:
            errors = [
                f"{list(e.absolute_path)}: {e.message}"
                for e in validator.iter_errors(copy.deepcopy(example))
            ]
            assert errors == [], (name, errors)

    entry_schema = load_example("log-entry-v2.schema.json")
    assert "kind" not in entry_schema["properties"]
    assert "event_id" in load_example("outcome-event-v1.schema.json")["required"]
