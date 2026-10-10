"""R-05, R-06, R-15..R-17: managed transport, bounded retries, no fallback."""

from unittest.mock import Mock, patch

import httpx
import pytest

from ee.jev.client import JevGatewayError, JevSystemOneClient
from ee.jev.mapping import JevMappingError
from ee.licensing.managed_ai import chat_completion, is_managed_model, service_for_model

pytestmark = pytest.mark.requires_ee
OK = {
    "model": "jev-1.13.0",
    "answers": {"q1": {"type": "noul", "noul": 0.9}},
    "usage": {"input_tokens": 412, "output_tokens": 6},
}
QUESTIONS = {"q1": {"type": "noul", "instructions": "Judge"}}


@pytest.fixture
def cloud():
    with (
        patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=True),
        patch(
            "ee.jev.client._cloud_gateway_credentials",
            return_value=("http://gateway.invalid/", "fixture-key"),
        ),
    ):
        yield


def reply(status, code=None, headers=None):
    return httpx.Response(
        status,
        json={"error": {"code": code, "message": "PRIVATE-CANARY"}} if code else OK,
        headers=headers,
    )


def test_r05_cloud_exact_route_payload_and_key(cloud):
    with patch("httpx.post", return_value=reply(200)) as post:
        result = JevSystemOneClient().evaluate(
            "jev-latest", {"input": "value"}, QUESTIONS, timeout=35
        )
    assert result == OK
    assert post.call_args.args == ("http://gateway.invalid/v1/systemone",)
    assert post.call_args.kwargs["json"] == {
        "model": "jev-latest",
        "state": {"input": "value"},
        "questions": QUESTIONS,
    }
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer fixture-key"


def test_r05_self_hosted_activation_route():
    with (
        patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=False),
        patch("ee.jev.client.call_managed_service", return_value=OK) as managed,
    ):
        assert (
            JevSystemOneClient().evaluate("jev-latest", "x", QUESTIONS, timeout=35)
            == OK
        )
    assert managed.call_args.kwargs["path"] == "/v1/systemone"


@pytest.mark.parametrize(
    "model", ["jev-preview", "jev-2.0.0", "JEV-LATEST", "turing_large"]
)
def test_r06_unknown_model_zero_transport(cloud, model):
    with (
        patch("httpx.post") as post,
        pytest.raises(JevMappingError, match="JEV_MODEL_UNKNOWN"),
    ):
        JevSystemOneClient().evaluate(model, "x", QUESTIONS, timeout=35)
    post.assert_not_called()


@pytest.mark.parametrize(
    "status,code",
    [
        (400, "upstream_rejected"),
        (401, "invalid_api_key"),
        (402, "entitlement_denied"),
        (403, "permission_denied"),
        (404, "model_not_found"),
        (413, "request_too_large"),
        (422, "invalid_question"),
        (501, "not_supported"),
        (502, "upstream_auth"),
        (500, "upstream_invalid_response"),
    ],
)
def test_r15_r16_no_retry_or_content_leak(cloud, status, code):
    sleep = Mock()
    with patch("httpx.post", return_value=reply(status, code)) as post:
        with pytest.raises(JevGatewayError) as error:
            JevSystemOneClient(sleep=sleep).evaluate(
                "jev-latest", "PRIVATE-CANARY", QUESTIONS, timeout=35
            )
    assert not error.value.retryable
    assert "PRIVATE-CANARY" not in str(error.value)
    assert post.call_count == 1
    sleep.assert_not_called()


@pytest.mark.parametrize(
    "status,code",
    [
        (429, "rate_limit_exceeded"),
        (502, "upstream_error"),
        (503, "all_providers_unavailable"),
        (504, "gateway_timeout"),
    ],
)
def test_r16_transient_retry_success(cloud, status, code):
    sleep = Mock()
    with patch(
        "httpx.post",
        side_effect=[reply(status, code, {"Retry-After": "2"}), reply(200)],
    ) as post:
        client = JevSystemOneClient(sleep=sleep)
        assert client.evaluate("jev-latest", "x", QUESTIONS, timeout=35) == OK
    assert post.call_count == 2
    sleep.assert_called_once_with(2)
    assert client.attempt == 2
    assert client.attempts[0]["outcome"] == "error"
    assert client.attempts[1]["usage"]["output_tokens"] == 6


def test_r16_retry_exhaustion_and_bounded_jitter(cloud):
    sleep = Mock()
    with patch(
        "httpx.post",
        return_value=reply(429, "rate_limit_exceeded", {"Retry-After": "11"}),
    ) as post:
        with pytest.raises(JevGatewayError, match="JEV_RATE_LIMITED"):
            JevSystemOneClient(sleep=sleep, jitter=lambda upper: upper).evaluate(
                "jev-latest", "x", QUESTIONS, timeout=35
            )
    assert post.call_count == 3
    assert [call.args[0] for call in sleep.call_args_list] == [1, 2]


@pytest.mark.parametrize(
    "error",
    [httpx.ConnectError("PRIVATE-CANARY"), httpx.TimeoutException("PRIVATE-CANARY")],
)
def test_r16_transport_retries(cloud, error):
    with patch("httpx.post", side_effect=[error, reply(200)]) as post:
        assert (
            JevSystemOneClient(sleep=Mock()).evaluate(
                "jev-latest", "x", QUESTIONS, timeout=35
            )
            == OK
        )
    assert post.call_count == 2


def test_r16_total_deadline_and_cancel(cloud):
    now = [0.0]

    def delayed(*args, **kwargs):
        assert kwargs["timeout"] <= 35
        now[0] += kwargs["timeout"]
        raise httpx.TimeoutException("slow")

    with patch("httpx.post", side_effect=delayed) as post:
        with pytest.raises(JevGatewayError, match="JEV_TIMEOUT"):
            JevSystemOneClient(
                clock=lambda: now[0],
                sleep=lambda delay: now.__setitem__(0, now[0] + delay),
                jitter=lambda _: 0,
            ).evaluate("jev-latest", "x", QUESTIONS, timeout=35)
    assert now[0] == 90
    assert post.call_count == 3
    with (
        patch("httpx.post", side_effect=KeyboardInterrupt) as post,
        pytest.raises(KeyboardInterrupt),
    ):
        JevSystemOneClient(sleep=Mock()).evaluate(
            "jev-latest", "x", QUESTIONS, timeout=35
        )
    assert post.call_count == 1


def test_r12_malformed_json_is_sanitized_error(cloud):
    with patch(
        "httpx.post", return_value=httpx.Response(200, content=b"PRIVATE-CANARY")
    ) as post:
        with pytest.raises(JevGatewayError, match="JEV_PROVIDER_ERROR"):
            JevSystemOneClient(sleep=Mock()).evaluate(
                "jev-latest", "x", QUESTIONS, timeout=35
            )
    assert post.call_count == 1


@pytest.mark.parametrize("model", ["jev-latest", "jev-1.13.0"])
def test_r05_r17_managed_recognition_but_chat_rejected(model):
    assert is_managed_model(model)
    assert service_for_model(model) == "jev"
    with patch("ee.licensing.managed_ai._cloud_chat_completion") as chat:
        with pytest.raises(ValueError, match="System One"):
            chat_completion({"model": model, "messages": []})
    chat.assert_not_called()
    assert not is_managed_model("jev-preview")
    assert service_for_model("jev-preview") is None


def test_r16_concurrency_bound(cloud):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from time import sleep

    lock = threading.Lock()
    active = 0
    maximum = 0

    def dispatch(*args, **kwargs):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(active, maximum)
        sleep(0.02)
        with lock:
            active -= 1
        return reply(200)

    with (
        patch("httpx.post", side_effect=dispatch),
        ThreadPoolExecutor(max_workers=20) as pool,
    ):
        results = list(
            pool.map(
                lambda _: JevSystemOneClient().evaluate(
                    "jev-latest", "x", QUESTIONS, timeout=35
                ),
                range(20),
            )
        )
    assert len(results) == 20
    assert 1 <= maximum <= 8
