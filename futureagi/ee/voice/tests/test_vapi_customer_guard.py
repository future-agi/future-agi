"""A Vapi API call whose ``customer`` is not an object has no customer number.

Vapi web calls carry ``customer: null``, and a malformed payload can carry a
string, list or number there. The call log readers already treat any such
value as "no customer" through ``tracer.utils.attribute_accessor.vapi_customer``;
the Vapi API readers in this service must do the same instead of failing the
whole call (normalisation) or the whole match (customer call lookup).
"""

from unittest.mock import patch

import pytest

from ee.voice.semantics import FAGICallData
from ee.voice.services.vapi_service import VapiService
from simulate.semantics import CallExecutionStatus, CallType
from tracer.models.observability_provider import ProviderChoices

NON_OBJECT_CUSTOMERS = [None, "+15550001111", ["+15550001111"], 15550001111]


@pytest.fixture
def vapi_service():
    """VapiService with a dummy key (no real API calls)."""
    with patch.dict(
        "os.environ",
        {"VAPI_API_KEY": "test-key", "VAPI_API_BASE_URL": "https://api.vapi.ai"},
    ):
        return VapiService(api_key="test-key")


def _api_call(call_id, customer, started_at="2026-09-28T10:00:01Z"):
    return {
        "id": call_id,
        "type": "inboundPhoneCall",
        "status": "ended",
        "assistantId": "assistant-1",
        "startedAt": started_at,
        "endedAt": "2026-09-28T10:01:00Z",
        "customer": customer,
        "messages": [],
    }


@pytest.mark.parametrize("customer", NON_OBJECT_CUSTOMERS)
def test_normalized_call_with_non_object_customer_has_no_customer_number(
    vapi_service, customer
):
    call_data = _api_call("call-1", customer)

    with patch.object(
        vapi_service, "get_call_transcript", return_value={"transcripts": []}
    ):
        normalized = vapi_service._normalize_to_fagi_call_data(
            call_data, call_data_stored=True
        )

    assert normalized.call_id == "call-1"
    assert normalized.customer_phone_number == ""


def test_normalized_call_keeps_an_object_customer_number(vapi_service):
    call_data = _api_call("call-1", {"number": "+15550001111"})

    with patch.object(
        vapi_service, "get_call_transcript", return_value={"transcripts": []}
    ):
        normalized = vapi_service._normalize_to_fagi_call_data(
            call_data, call_data_stored=True
        )

    assert normalized.customer_phone_number == "+15550001111"


def _our_call(customer_phone_number):
    return FAGICallData(
        call_id="our-call",
        call_type=CallType.INBOUND,
        status=CallExecutionStatus.COMPLETED,
        assistant_id="our-assistant",
        system_phone_number="",
        customer_phone_number=customer_phone_number,
        system_phone_number_id="",
        recording_url=None,
        log_url=None,
        created_at="2026-09-28T10:00:00Z",
        started_at="2026-09-28T10:00:00Z",
        ended_at="2026-09-28T10:01:00Z",
        updated_at="2026-09-28T10:01:00Z",
        cost=None,
        raw_log={ProviderChoices.VAPI.value: {}},
    )


@pytest.mark.parametrize("customer", NON_OBJECT_CUSTOMERS)
def test_customer_call_lookup_survives_a_candidate_with_non_object_customer(
    vapi_service, customer
):
    """One malformed candidate must not void the whole match.

    With two candidates in the window the lookup scores them. The malformed
    one has no customer number, so it falls through the phone check like any
    call without a number and is scored on time; the exact-time candidate
    still wins.
    """
    candidates = [
        _api_call("malformed", customer, started_at="2026-09-28T10:00:05Z"),
        _api_call(
            "exact",
            {"number": "+15550001111"},
            started_at="2026-09-28T10:00:00Z",
        ),
    ]

    with patch.object(VapiService, "list_calls", return_value=candidates):
        call_id = vapi_service._find_customer_vapi_call_id(
            customer_api_key="customer-key",
            customer_assistant_id="customer-assistant",
            our_call_data=_our_call(""),
            min_match_score_threshold=0,
        )

    assert call_id == "exact"
