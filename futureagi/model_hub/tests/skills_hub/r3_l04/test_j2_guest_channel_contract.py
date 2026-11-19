"""J2 guest channel: malformed bodies are refused with the service's own codes, before any write.

Refusals use the common error envelope (status/type/code/detail)."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from drf_yasg.utils import get_serializer_ref_name
from rest_framework.test import APIRequestFactory, force_authenticate

from model_hub.serializers import skills_native_runs as guest_serializers
from model_hub.services.skills_hub.r3 import native_runs
from model_hub.views.skills_native_runs import (
    NativeGuestFailureView,
    NativeGuestHealthView,
    NativeGuestObservationView,
)
from simulate.models.hosted_harness import HostedHarnessAttempt

GUEST = SimpleNamespace(is_authenticated=True, is_harness_attempt=True)
HEALTH = {
    "protocol": "p",
    "adapter_module": "m",
    "configuration_digest": "c",
    "execution_fingerprint": "f",
    "code_digest": "d",
    "image_digest": "i",
    "packages": [],
    "checkpoint_digest": "k",
    "read_only_source": True,
    "read_only_context": True,
    "separate_writable_state": True,
    "controlled_paid_egress": True,
}
OBSERVATION = {
    "schema": 1,
    "protocol": "p",
    "execution_fingerprint": "f",
    "case_id": "case",
    "variant": "candidate",
    "repeat_index": 0,
    "application": {},
    "business": {},
    "observation_digest": "x",
}


def post(view, body, monkeypatch, service, token=None):
    calls = []
    monkeypatch.setattr(
        f"model_hub.services.skills_hub.r3.native_runs.{service}",
        lambda **kwargs: calls.append(kwargs) or {"accepted": True},
    )
    attempt_id = uuid4()
    request = APIRequestFactory().post(
        f"/model-hub/skills/native/attempts/{attempt_id}/", body, format="json"
    )
    # HarnessAttemptAuthentication authenticates as the fenced attempt itself.
    if token is None:
        token = HostedHarnessAttempt(id=attempt_id)
    force_authenticate(request, user=GUEST, token=token)
    return view.as_view()(request, attempt_id=attempt_id), calls


@pytest.mark.parametrize(
    "body",
    [{**HEALTH, "extra": True}, {k: v for k, v in HEALTH.items() if k != "protocol"}],
)
def test_malformed_health_is_runtime_incompatible_without_a_write(body, monkeypatch):
    response, calls = post(
        NativeGuestHealthView, body, monkeypatch, "record_guest_health"
    )
    assert response.status_code == 422
    assert response.data["code"] == "runtime_incompatible"
    assert calls == []


@pytest.mark.parametrize(
    "body",
    [
        {**OBSERVATION, "transcript": "raw"},
        {k: v for k, v in OBSERVATION.items() if k != "observation_digest"},
        {**OBSERVATION, "application": "not an object"},
    ],
)
def test_malformed_observation_is_an_invalid_receipt_without_a_write(body, monkeypatch):
    response, calls = post(
        NativeGuestObservationView, body, monkeypatch, "ingest_observation"
    )
    assert response.status_code == 400
    assert response.data["code"] == "invalid_receipt"
    assert calls == []


def test_well_formed_bodies_reach_the_service_unchanged(monkeypatch):
    response, calls = post(
        NativeGuestHealthView, HEALTH, monkeypatch, "record_guest_health"
    )
    assert response.status_code == 200 and calls[0]["health"] == HEALTH
    response, calls = post(
        NativeGuestObservationView, OBSERVATION, monkeypatch, "ingest_observation"
    )
    assert response.status_code == 200 and calls[0]["body"] == OBSERVATION


@pytest.mark.parametrize("body", [{"reason": "provider_timeout"}, {"reason": 7}, {}])
def test_any_failure_reason_is_recorded(body, monkeypatch):
    response, calls = post(
        NativeGuestFailureView, body, monkeypatch, "report_guest_failure"
    )
    assert response.status_code == 200
    assert calls == [{"attempt": calls[0]["attempt"], "reason": body.get("reason")}]


@pytest.mark.parametrize(
    ("view", "body", "service"),
    [
        (NativeGuestHealthView, HEALTH, "record_guest_health"),
        (NativeGuestObservationView, OBSERVATION, "ingest_observation"),
        (
            NativeGuestFailureView,
            {"reason": "provider_timeout"},
            "report_guest_failure",
        ),
    ],
)
def test_guest_routes_require_the_fenced_attempt_identity(
    view, body, service, monkeypatch
):
    token = SimpleNamespace(pk=uuid4())
    response, calls = post(view, body, monkeypatch, service, token=token)
    assert response.status_code == 401
    assert response.data["code"] == "authentication_required"
    assert response.data["status"] is False and response.data["type"]
    assert calls == []


@pytest.mark.parametrize(
    ("reason", "code"),
    [
        ("provider_timeout", "provider_timeout"),
        (7, "7"),
        (None, "guest_failed"),
        ("Not A Code", "guest_failed"),
        ({"nested": True}, "guest_failed"),
    ],
)
def test_guest_failure_reason_is_stored_as_a_bounded_string_code(
    reason, code, monkeypatch
):
    finished = []
    monkeypatch.setattr(
        native_runs,
        "_finish",
        lambda job_id, attempt_id, *, failure: finished.append(failure),
    )
    attempt = SimpleNamespace(job_id=uuid4(), pk=uuid4())
    assert native_runs.report_guest_failure(attempt=attempt, reason=reason) == {
        "accepted": True
    }
    assert finished == [code]


@pytest.mark.parametrize(
    "name", ["GuestHealthInput", "GuestObservationInput", "GuestFailureInput"]
)
def test_guest_request_bodies_publish_in_the_skills_schema_namespace(name):
    # The generated contract may only add Skills-namespaced definitions; a bare
    # serializer name would publish as an unrelated top-level schema.
    serializer = getattr(guest_serializers, name)()
    assert get_serializer_ref_name(serializer) == f"SkillsContext{name}"


GUEST_REFUSALS = {400, 401, 403, 404, 409, 413, 422}


@pytest.mark.parametrize(
    ("view", "output", "reply"),
    [
        (NativeGuestHealthView, "GuestAccepted", {"accepted": True}),
        (
            NativeGuestObservationView,
            "GuestObservationAccepted",
            {"accepted": True, "received": 2, "planned": 4},
        ),
        (NativeGuestFailureView, "GuestAccepted", {"accepted": True}),
    ],
)
def test_guest_routes_document_their_reply_and_every_refusal(view, output, reply):
    # The published contract describes what the guest really receives: a 200
    # envelope with the service's reply and the common error envelope for each
    # refusal, never a 201 that echoes the request body.
    responses = view.post._swagger_auto_schema["responses"]
    assert set(responses) == {200, *GUEST_REFUSALS}
    envelope = responses[200]
    assert get_serializer_ref_name(envelope()) == f"Skills{output}Response"
    result = envelope().fields["result"]
    assert get_serializer_ref_name(result) == f"SkillsContext{output}"
    checked = envelope(data={"status": True, "result": reply})
    assert checked.is_valid(), checked.errors
    assert checked.validated_data["result"] == reply
