"""One gateway log entry the database refuses must not cost the batch.

The gateway holds a batch the backend answers 503 to at the front of its
buffer and sends it again, so a 503 is only right when a resend can succeed.
"""

from unittest.mock import patch

import pytest
from django.db import (
    DataError,
    InterfaceError,
    InternalError,
    NotSupportedError,
    OperationalError,
    ProgrammingError,
)

from accounts.models import Organization
from agentcc.models import AgentccAPIKey, AgentccRequestLog

SECRET = "test-ingest-secret"
LOGS_URL = "/agentcc/webhook/logs/"


def _post(api_client, logs):
    return api_client.post(
        LOGS_URL,
        {"logs": logs},
        format="json",
        HTTP_X_WEBHOOK_SECRET=SECRET,
    )


def _entry(request_id, key_id, **extra):
    return {
        "request_id": request_id,
        "model": "gpt-4o",
        "status_code": 200,
        "auth_key_id": key_id,
        **extra,
    }


def _stored(request_ids):
    return {
        row.request_id: row
        for row in AgentccRequestLog.no_workspace_objects.filter(
            request_id__in=request_ids
        )
    }


@pytest.fixture
def two_orgs(db, organization):
    other = Organization.objects.create(name="Second Organization")
    AgentccAPIKey.objects.create(
        gateway_key_id="key-a", name="key-a", organization=organization
    )
    AgentccAPIKey.objects.create(
        gateway_key_id="key-b", name="key-b", organization=other
    )
    return organization, other


@pytest.mark.integration
@patch("agentcc.views.webhook.AGENTCC_WEBHOOK_SECRET", SECRET)
class TestEntriesTheDatabaseRefuses:
    def test_an_over_long_user_field_is_cut_to_its_column(self, api_client, two_orgs):
        # The gateway copies the caller's OpenAI "user" field into user_id with
        # no length check.
        response = _post(
            api_client,
            [
                _entry("long-user", "key-a", user_id="u" * 300, provider="p" * 300),
                _entry("after-it", "key-b"),
            ],
        )

        assert response.status_code == 200, response.content
        assert response.json()["result"]["ingested"] == 2
        rows = _stored(["long-user", "after-it"])
        assert set(rows) == {"long-user", "after-it"}
        assert rows["long-user"].user_id == "u" * 255
        assert rows["long-user"].provider == "p" * 255

    def test_a_refused_entry_costs_only_itself_across_orgs(self, api_client, two_orgs):
        org_a, org_b = two_orgs
        logs = [
            _entry("a-before", "key-a"),
            # Longer than request_id's column: not cut, since it is the
            # replay key, so the database refuses this one row.
            _entry("r" * 300, "key-a"),
            _entry("a-after", "key-a"),
            _entry("b-fine", "key-b"),
        ]

        response = _post(api_client, logs)

        assert response.status_code == 200, response.content
        assert response.json()["result"]["ingested"] == 3
        rows = _stored(["a-before", "a-after", "b-fine", "r" * 300])
        assert set(rows) == {"a-before", "a-after", "b-fine"}
        assert rows["a-before"].organization_id == org_a.id
        assert rows["b-fine"].organization_id == org_b.id

    def test_a_resent_batch_with_a_refused_entry_stores_nothing_twice(
        self, api_client, two_orgs
    ):
        logs = [_entry("once", "key-a"), _entry("r" * 300, "key-a")]

        assert _post(api_client, logs).status_code == 200
        second = _post(api_client, logs)

        assert second.status_code == 200
        assert second.json()["result"]["ingested"] == 0
        assert (
            AgentccRequestLog.no_workspace_objects.filter(request_id="once").count()
            == 1
        )


@pytest.mark.integration
@patch("agentcc.views.webhook.AGENTCC_WEBHOOK_SECRET", SECRET)
class TestIngestErrorStatus:
    @pytest.mark.parametrize(
        "error",
        [OperationalError("server closed the connection"), InterfaceError("closed")],
    )
    def test_a_database_outage_asks_the_gateway_to_retry(self, api_client, error):
        with patch("agentcc.views.webhook.ingest_request_logs", side_effect=error):
            response = _post(api_client, [{"request_id": "r1", "model": "gpt-4o"}])

        assert response.status_code == 503
        assert "server closed" not in response.content.decode()

    @pytest.mark.parametrize(
        "error",
        [
            DataError("value too long for type character varying(255)"),
            ValueError("bad value"),
            TypeError("bad type"),
            RuntimeError("bug"),
        ],
    )
    def test_an_error_a_resend_would_repeat_drops_the_batch(self, api_client, error):
        with patch("agentcc.views.webhook.ingest_request_logs", side_effect=error):
            response = _post(api_client, [{"request_id": "r1", "model": "gpt-4o"}])

        assert response.status_code == 400
        assert "character varying" not in response.content.decode()


@pytest.mark.integration
@patch("agentcc.views.webhook.AGENTCC_WEBHOOK_SECRET", SECRET)
@pytest.mark.parametrize(
    "error", [InterfaceError("connection already closed"), OperationalError("gone")]
)
def test_a_connection_lost_mid_insert_is_retried_not_skipped(
    api_client, two_orgs, error
):
    with patch(
        "agentcc.services.log_ingestion.AgentccRequestLog.no_workspace_objects.bulk_create",
        side_effect=error,
    ):
        response = _post(api_client, [_entry("kept-for-resend", "key-a")])

    assert response.status_code == 503


@pytest.mark.integration
@patch("agentcc.views.webhook.AGENTCC_WEBHOOK_SECRET", SECRET)
def test_nul_bytes_from_the_caller_are_stripped_not_refused(api_client, two_orgs):
    """PostgreSQL text and jsonb cannot hold NUL. The gateway copies the
    caller's ``user`` field and bodies unchecked, so a NUL there must not cost
    the request its log row (and with it its metering and webhooks)."""
    response = _post(
        api_client,
        [
            _entry(
                "nul-everywhere",
                "key-b",
                user_id="a\x00b",
                error_message="bad\x00byte",
                provider="open\x00ai",
                request_body={"messages": [{"role": "user", "content": "hi\x00"}]},
                response_body={"x\x00": ["y\x00"]},
                metadata={"k": "v\x00"},
            ),
            _entry("after-it", "key-b"),
        ],
    )

    assert response.status_code == 200, response.content
    assert response.json()["result"]["ingested"] == 2
    row = _stored(["nul-everywhere"])["nul-everywhere"]
    assert row.user_id == "ab"
    assert row.error_message == "badbyte"
    assert row.provider == "openai"
    assert row.request_body == {"messages": [{"role": "user", "content": "hi"}]}
    assert row.response_body == {"x": ["y"]}
    assert row.metadata == {"k": "v"}


@pytest.mark.integration
@patch("agentcc.views.webhook.AGENTCC_WEBHOOK_SECRET", SECRET)
@pytest.mark.parametrize(
    "error",
    [
        ProgrammingError("permission denied for table agentcc_request_log"),
        InternalError("cannot execute INSERT in a read-only transaction"),
        NotSupportedError("not supported"),
    ],
)
def test_a_whole_batch_database_error_is_one_503_not_row_by_row(
    api_client, two_orgs, error
):
    """An error every row would hit is not a row's fault: no split into
    single-row INSERTs, no 200 with nothing stored; the gateway resends."""
    with patch(
        "agentcc.services.log_ingestion.AgentccRequestLog.no_workspace_objects.bulk_create",
        side_effect=error,
    ) as insert:
        response = _post(api_client, [_entry(f"r{i}", "key-a") for i in range(8)])

    assert response.status_code == 503, response.content
    assert insert.call_count == 1
    assert "permission denied" not in response.content.decode()
