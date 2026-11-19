"""Fail-open capture policy, tested with real SDK envelopes rather than grouping."""

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import requests
import sentry_sdk
from django.core.exceptions import ObjectDoesNotExist
from sentry_sdk.integrations.dedupe import DedupeIntegration
from sentry_sdk.integrations.logging import LoggingIntegration

from tfc.logging.sentry import _get_before_send
from tfc.tests.sentry_capture import sentry_capture

LOGGER = "ee.agenthub.explanation_agent.exp_agent"
EXPECTED = {
    "event": "explanation_summary_skipped",
    "reason": "insufficient_samples",
    "outcome": "handled",
    "input_count": 2,
    "usable_count": 2,
    "minimum_required": 5,
}


@pytest.mark.parametrize("shape", ["dict", "json", "formatted", "message"])
def test_expected_log_shapes_are_dropped(shape):
    event = {"logger": LOGGER}
    hint = {}
    if shape == "dict":
        hint["log_record"] = SimpleNamespace(msg=EXPECTED)
    elif shape == "json":
        hint["log_record"] = SimpleNamespace(msg=json.dumps(EXPECTED))
    elif shape == "formatted":
        event["logentry"] = {"message": "%s", "formatted": json.dumps(EXPECTED)}
    else:
        event["message"] = EXPECTED
    assert _get_before_send()(event, hint) is None


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        "[]",
        "{'event': 'explanation_summary_skipped'}",
        [],
        None,
        {**EXPECTED, "outcome": "unhandled"},
        {**EXPECTED, "reason": "missing_reasons"},
        {**EXPECTED, "event": []},
    ],
)
def test_unknown_payloads_are_kept(payload):
    event = {"logger": LOGGER, "logentry": {"formatted": payload}, "request": "invalid"}
    assert _get_before_send()(event, {}) is event


def test_expected_event_name_does_not_suppress_another_logger_or_exception():
    event = {"logger": "other.module", "message": EXPECTED}
    assert _get_before_send()(event, {}) is event
    event["logger"] = LOGGER
    exc = ValueError("unexpected")
    assert _get_before_send()(event, {"exc_info": (type(exc), exc, None)}) is event
    event["exception"] = {"values": [{"mechanism": {"handled": False}}]}
    assert _get_before_send()(event, {}) is event


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 422, 429, 500, 503])
def test_downstream_http_errors_are_actionable(status):
    response = requests.Response()
    response.status_code = status
    exc = requests.HTTPError("downstream failure", response=response)
    assert _get_before_send()({}, {"exc_info": (type(exc), exc, None)}) is not None


@pytest.fixture
def transport():
    with sentry_capture(
        LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        DedupeIntegration(),
    ) as transport:
        yield transport


def test_sdk_envelopes_keep_genuine_faults_and_scrub_secrets(transport):
    logger = logging.getLogger(LOGGER)
    logger.error(EXPECTED)
    logger.error(json.dumps(EXPECTED))
    assert transport.events == []
    for exc in (
        ValueError("bad value"),
        ObjectDoesNotExist("missing internal row"),
        ConnectionResetError("database connection lost"),
    ):
        sentry_sdk.capture_exception(exc)
    logger.error(
        "request_finished", extra={"status_code": 500, "api_key": "synthetic-secret"}
    )
    assert len(transport.events) == 4
    assert "synthetic-secret" not in json.dumps(transport.events)
    assert transport.events[-1]["level"] == "error"


@pytest.mark.asyncio
async def test_logged_and_rethrown_activity_has_one_envelope_per_attempt(
    transport, monkeypatch
):
    from tfc.temporal.common import sentry_interceptor

    monkeypatch.setattr(sentry_interceptor, "_get_sentry_sdk", lambda: sentry_sdk)

    async def failing_activity():
        try:
            raise ValueError("activity failed")
        except ValueError:
            logging.getLogger("test.activity").exception("activity_failure")
            raise

    next_interceptor = SimpleNamespace(
        execute_activity=AsyncMock(side_effect=lambda _: failing_activity())
    )

    # AsyncMock does not await a coroutine returned by a synchronous side effect.
    async def execute(_):
        return await failing_activity()

    next_interceptor.execute_activity.side_effect = execute
    interceptor = sentry_interceptor.SentryActivityInboundInterceptor(next_interceptor)
    activity_input = SimpleNamespace(fn=failing_activity, args=[])
    for attempt in range(2):
        with pytest.raises(ValueError, match="activity failed"):
            await interceptor.execute_activity(activity_input)
        assert len(transport.events) == attempt + 1


def test_invalid_host_header_is_not_an_issue_but_other_security_failures_are(
    transport,
):
    from django.core.exceptions import DisallowedHost, SuspiciousFileOperation
    from django.core.handlers.exception import response_for_exception
    from django.test import RequestFactory, override_settings

    with override_settings(DEBUG=False, ALLOWED_HOSTS=["testserver"]):
        request = RequestFactory().get("/", HTTP_HOST="scanner.example.invalid")
        with pytest.raises(DisallowedHost) as raised:
            request.get_host()
        # The exact path Django takes for a request with an invalid Host header.
        assert response_for_exception(request, raised.value).status_code == 400
        assert transport.events == []

        response = response_for_exception(
            request, SuspiciousFileOperation("synthetic path traversal")
        )
    assert response.status_code == 400
    assert [event["logger"] for event in transport.events] == [
        "django.security.SuspiciousFileOperation"
    ]


def test_litellm_logger_errors_are_breadcrumbs_and_our_captures_stay_issues(
    transport,
):
    # LiteLLM logs the failure on its own logger and raises that same exception
    # object to our code, which logs it again. Dedupe remembers the object from
    # LiteLLM's record before before_send drops it; the SDK must forget it after
    # the drop, or our own record of the failure would be swallowed as a
    # duplicate of an event that was never sent.
    try:
        raise ValueError("Could not deserialize key data.")
    except ValueError as error:
        logging.getLogger("LiteLLM").exception(
            "Failed to load vertex credentials. Check to see if credentials "
            "containing partial/invalid information. Error: %s",
            error,
        )
        assert transport.events == []
        logging.getLogger("model_hub.utils.utils").exception("model_check_failed")
        # Dedupe still merges a second report of the object we already sent.
        sentry_sdk.capture_exception(error)

    [event] = transport.events
    assert event["logger"] == "model_hub.utils.utils"
    assert event["exception"]["values"][-1]["value"] == (
        "Could not deserialize key data."
    )
    assert any(
        crumb.get("category") == "LiteLLM"
        and crumb.get("message") == "Vertex credential loading failed"
        for crumb in event["breadcrumbs"]["values"]
    )

    sentry_sdk.capture_exception(RuntimeError("synthetic genuine fault"))
    assert [event.get("logger") for event in transport.events] == [
        "model_hub.utils.utils",
        None,
    ]


def test_unexpected_litellm_error_is_an_issue(transport):
    logging.getLogger("LiteLLM").error("Unexpected response parser failure")
    [event] = transport.events
    assert event["logger"] == "LiteLLM"
    assert event["logentry"]["formatted"] == "Unexpected response parser failure"


def test_real_litellm_vertex_key_failure_is_not_an_issue(transport):
    pytest.importorskip("litellm")
    from litellm.llms.vertex_ai.vertex_llm_base import VertexBase

    service_account = {
        "type": "service_account",
        "project_id": "synthetic-project",
        "private_key_id": "0",
        "private_key": "-----BEGIN PRIVATE KEY-----\nMIIBsynthetic\n"
        "-----END PRIVATE KEY-----\n",
        "client_email": "qa@synthetic.example.invalid",
        "client_id": "0",
        "token_uri": "http://127.0.0.1:9/token",
    }
    # LiteLLM logs "Failed to load vertex credentials" on its own logger at
    # ERROR, then raises the key-parsing error to the caller.
    with pytest.raises(ValueError):
        VertexBase().get_access_token(
            credentials=json.dumps(service_account), project_id="synthetic-project"
        )
    assert transport.events == []
    # The record is still there as context for the next genuine event.
    sentry_sdk.capture_message("after vertex validation")
    [event] = transport.events
    assert "LiteLLM" in {
        crumb.get("category") for crumb in event["breadcrumbs"]["values"]
    }


@pytest.mark.asyncio
async def test_expected_activity_denial_is_not_an_issue(transport, monkeypatch):
    from temporalio.exceptions import ApplicationError

    from tfc.temporal.common import sentry_interceptor

    monkeypatch.setattr(sentry_interceptor, "_get_sentry_sdk", lambda: sentry_sdk)

    async def activity_fn():
        raise AssertionError("replaced per case")

    def interceptor_raising(error):
        async def execute(_):
            raise error

        return sentry_interceptor.SentryActivityInboundInterceptor(
            SimpleNamespace(execute_activity=execute)
        )

    activity_input = SimpleNamespace(fn=activity_fn, args=[])
    denials = [
        ApplicationError(
            "Synthetic free tier limit reached",
            {"code": "FREE_TIER_LIMIT", "message": "Synthetic free tier limit reached"},
            type="EvaluationDenied",
            non_retryable=True,
        ),
        # Existing emitters do not all carry structured capability reasons.
        ApplicationError(
            "Synthetic feature denial", type="CapabilityDenied", non_retryable=True
        ),
        ApplicationError(
            "Synthetic license denial",
            "license",
            type="CapabilityDenied",
            non_retryable=True,
        ),
    ]
    for denial in denials:
        with pytest.raises(ApplicationError) as raised:
            await interceptor_raising(denial).execute_activity(activity_input)
        assert raised.value is denial
        assert transport.events == []

    genuine = [
        RuntimeError("synthetic activity fault"),
        ApplicationError("retryable", type="EvaluationDenied", non_retryable=False),
        ApplicationError(
            "retryable capability", type="CapabilityDenied", non_retryable=False
        ),
        ApplicationError("lookup unavailable", type="CapabilityUnavailable"),
        ApplicationError("untyped refusal", non_retryable=True),
        ApplicationError(
            "other refusal", type="ProviderMisconfigured", non_retryable=True
        ),
    ]
    for error in genuine:
        with pytest.raises(type(error)):
            await interceptor_raising(error).execute_activity(activity_input)
    assert [
        event["exception"]["values"][-1]["value"] for event in transport.events
    ] == [
        "synthetic activity fault",
        "retryable",
        "retryable capability",
        "lookup unavailable",
        "untyped refusal",
        "other refusal",
    ]


@pytest.fixture
def denied_capability(monkeypatch):
    from tfc.capabilities import service

    decision = service.CapabilityDecision(
        allowed=False, feature_id="insights", reason_code="PLAN_FEATURE_MISSING"
    )
    monkeypatch.setattr(service, "is_configured", lambda: True)
    monkeypatch.setattr(service, "check", lambda *args, **kwargs: decision)


@pytest.mark.asyncio
async def test_capability_gate_activity_denial_is_not_an_issue(
    transport, denied_capability, monkeypatch
):
    from temporalio.exceptions import ApplicationError

    from tfc.ee_gating import check_ee_feature
    from tfc.temporal.common import sentry_interceptor

    monkeypatch.setattr(sentry_interceptor, "_get_sentry_sdk", lambda: sentry_sdk)
    capture = MagicMock(wraps=sentry_sdk.capture_exception)
    monkeypatch.setattr(sentry_sdk, "capture_exception", capture)

    async def denied_activity(_):
        # Use the gate and its real Temporal denial emitter, not a fabricated type.
        check_ee_feature("insights", activity=True)

    interceptor = sentry_interceptor.SentryActivityInboundInterceptor(
        SimpleNamespace(execute_activity=denied_activity)
    )
    with pytest.raises(ApplicationError) as raised:
        await interceptor.execute_activity(SimpleNamespace(fn=denied_activity, args=[]))
    assert raised.value.type == "CapabilityDenied"
    assert raised.value.non_retryable is True
    assert raised.value.details == (
        {"feature_id": "insights", "reason_code": "PLAN_FEATURE_MISSING"},
    )
    capture.assert_not_called()
    assert transport.events == []

    # The failure is still context for a later, unrelated fault.
    sentry_sdk.capture_exception(RuntimeError("synthetic subsequent fault"))
    [event] = transport.events
    assert any(
        crumb.get("category") == "temporal.activity" and crumb.get("level") == "error"
        for crumb in event["breadcrumbs"]["values"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source",
    [
        "cloud_resolver_missing",
        "license_resolver_missing",
        "org_missing",
        "org_blank",
        "lookup_error",
        "plan_feature_missing",
    ],
)
async def test_capability_gate_activity_capture_distinguishes_lookup_outages(
    source, transport, monkeypatch
):
    from temporalio.exceptions import ApplicationError

    from tfc.capabilities import service
    from tfc.ee_gating import check_ee_feature
    from tfc.licensing.types import DeploymentFlavor, DeploymentLocation
    from tfc.temporal.common import sentry_interceptor

    resolver = SimpleNamespace(
        has_feature=MagicMock(return_value=False),
        get_upgrade_cta=MagicMock(return_value=None),
    )
    if source == "lookup_error":
        resolver.has_feature.side_effect = RuntimeError("synthetic lookup failure")
    self_hosted = source == "license_resolver_missing"
    monkeypatch.setattr(service, "_configured", True)
    monkeypatch.setattr(
        service,
        "_deployment_flavor",
        DeploymentFlavor.SELF_HOSTED_EE if self_hosted else DeploymentFlavor.CLOUD,
    )
    monkeypatch.setattr(
        service,
        "_deployment_location",
        DeploymentLocation.SELF_HOSTED if self_hosted else DeploymentLocation.CLOUD,
    )
    monkeypatch.setattr(service, "_license_resolver", None)
    monkeypatch.setattr(
        service,
        "_cloud_plan_resolver",
        None if source == "cloud_resolver_missing" else resolver,
    )
    org_id = "11111111-1111-1111-1111-111111111111"
    if source == "org_missing":
        org_id = None
    elif source == "org_blank":
        org_id = "   "

    monkeypatch.setattr(sentry_interceptor, "_get_sentry_sdk", lambda: sentry_sdk)
    capture = MagicMock(wraps=sentry_sdk.capture_exception)
    monkeypatch.setattr(sentry_sdk, "capture_exception", capture)

    async def gated_activity(_):
        # Keep the real service.check -> denial emitter path, as in the Imagine
        # activity. Only the resolver is synthetic; no database or provider call.
        check_ee_feature("falcon_ai", org_id=org_id, activity=True)

    interceptor = sentry_interceptor.SentryActivityInboundInterceptor(
        SimpleNamespace(execute_activity=gated_activity)
    )
    with pytest.raises(ApplicationError) as raised:
        await interceptor.execute_activity(SimpleNamespace(fn=gated_activity, args=[]))

    # Observability must not change the existing failure type or retry policy.
    assert raised.value.non_retryable is True
    if source == "lookup_error":
        assert raised.value.type == "FeatureUnavailable"
        assert raised.value.message == "Feature entitlement service is unavailable."
    else:
        assert raised.value.type == "CapabilityDenied"
        reason = (
            "PLAN_FEATURE_MISSING"
            if source == "plan_feature_missing"
            else "RESOLVER_UNAVAILABLE"
        )
        assert raised.value.details == (
            {"feature_id": "falcon_ai", "reason_code": reason},
        )
        assert raised.value.message == (
            "Falcon AI isn't included in your plan."
            if source == "plan_feature_missing"
            else "Falcon AI is not available."
        )

    if source == "plan_feature_missing":
        capture.assert_not_called()
        assert transport.events == []
    else:
        capture.assert_called_once_with(raised.value)
        # A raised lookup failure is logged separately; the emitted failure must
        # also survive capture, not just that original log record.
        assert transport.events[-1]["exception"]["values"][-1]["type"] == (
            "ApplicationError"
        )
        assert len(transport.events) == (2 if source == "lookup_error" else 1)
        if source == "lookup_error":
            assert transport.events[0]["exception"]["values"][-1]["type"] == (
                "RuntimeError"
            )


@pytest.mark.parametrize("source", ["ee_stub", "capability_gate"])
@pytest.mark.parametrize("capture_method", ["exception", "log"])
def test_feature_denial_sources_do_not_create_issues(
    source, capture_method, transport, denied_capability
):
    from tfc.ee_gating import FeatureUnavailable, check_ee_feature
    from tfc.ee_stub import _ee_stub

    try:
        if source == "ee_stub":
            _ee_stub("synthetic_feature")()
        else:
            check_ee_feature("insights")
    except FeatureUnavailable as error:
        assert error.status_code == 402
        if capture_method == "exception":
            sentry_sdk.capture_exception(error)
        else:
            logging.getLogger("test.feature_gate").exception("synthetic denial")
    else:
        pytest.fail("The real gate must refuse the feature")
    assert transport.events == []

    sentry_sdk.capture_exception(RuntimeError("synthetic subsequent fault"))
    [event] = transport.events
    assert event["exception"]["values"][-1]["type"] == "RuntimeError"
    if capture_method == "log":
        assert any(
            crumb.get("category") == "test.feature_gate"
            for crumb in event["breadcrumbs"]["values"]
        )


@pytest.mark.parametrize("source", ["resolver", "legacy_service"])
def test_entitlement_lookup_outages_remain_issues(source, transport, monkeypatch):
    from tfc.capabilities import service
    from tfc.ee_gating import FeatureUnavailable, check_ee_feature

    monkeypatch.setattr(service, "is_configured", lambda: True)

    def unavailable(*args, **kwargs):
        if source == "resolver":
            return service.CapabilityDecision(
                allowed=False, feature_id="insights", reason_code="RESOLVER_UNAVAILABLE"
            )
        raise RuntimeError("synthetic lookup failure")

    monkeypatch.setattr(service, "check", unavailable)
    with pytest.raises(FeatureUnavailable) as raised:
        check_ee_feature("insights")
    sentry_sdk.capture_exception(raised.value)
    # The legacy path also logs the original lookup failure. In both cases the
    # denial-shaped exception itself must remain actionable.
    assert transport.events[-1]["exception"]["values"][-1]["type"] == (
        "CapabilityDenied" if source == "resolver" else "FeatureUnavailable"
    )


@pytest.mark.parametrize("status", [400, 402, 404])
@pytest.mark.parametrize("source", ["drf", "requests"])
def test_sdk_keeps_generic_http_errors(status, source, transport):
    from rest_framework.exceptions import APIException

    if source == "drf":
        error = APIException("synthetic refusal", code="ENTITLEMENT_DENIED")
        error.status_code = status
    else:
        response = requests.Response()
        response.status_code = status
        error = requests.HTTPError("synthetic refusal", response=response)
    sentry_sdk.capture_exception(error)
    assert len(transport.events) == 1


def test_fault_caused_by_feature_denial_is_still_an_issue(transport):
    from tfc.ee_gating import FeatureUnavailable

    try:
        try:
            raise FeatureUnavailable("synthetic_feature")
        except FeatureUnavailable as denial:
            raise RuntimeError("synthetic denial handler failed") from denial
    except RuntimeError as error:
        sentry_sdk.capture_exception(error)
    [event] = transport.events
    assert event["exception"]["values"][-1]["type"] == "RuntimeError"
