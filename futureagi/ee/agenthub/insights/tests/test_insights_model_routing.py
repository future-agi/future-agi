"""Where Insights' model calls go, without a database.

Turing runs on Future AGI keys: Cloud reaches it through its own gateway, a
licensed self-hosted install through the license's managed service (its local
gateway has no Turing models). A deployment's own models go through its
gateway, and an OSS install never gets as far as a model call.
"""

import re
import uuid
from types import SimpleNamespace

import pytest
from rest_framework import status

from ee.agenthub.insights import llm
from ee.licensing.activation_client import ManagedServiceError

REPLY = '{"ok": true}'
USAGE = {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200}


class Gateway:
    """The gateway's OpenAI client: records each request and answers with
    the gateway's cost header."""

    def __init__(self, cost="0.004"):
        self.requests = []
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=REPLY))],
            usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=200),
        )
        raw = SimpleNamespace(headers={"x-agentcc-cost": cost}, parse=lambda: response)

        def create(**kwargs):
            self.requests.append(kwargs)
            return raw

        self.chat = SimpleNamespace(
            completions=SimpleNamespace(
                with_raw_response=SimpleNamespace(create=create)
            )
        )


@pytest.fixture
def deployment(monkeypatch, settings):
    """Insights on its default models, on Cloud or self-hosted, with the
    gateway and the managed service both recorded."""
    gateway = Gateway()
    managed = []

    def chat_completion(payload, *, timeout=None):
        managed.append((payload, timeout))
        return {"choices": [{"message": {"content": REPLY}}], "usage": dict(USAGE)}

    monkeypatch.setattr(
        "ee.usage.services.gateway_llm_client.get_gateway_client", lambda: gateway
    )
    monkeypatch.setattr("ee.licensing.managed_ai.chat_completion", chat_completion)
    monkeypatch.setattr(llm.time, "sleep", lambda *_: None)
    for name in ("INSIGHTS_FAST_MODEL", "INSIGHTS_THINKING_MODEL"):
        setattr(settings, name, None)
        monkeypatch.delenv(name, raising=False)

    def on(*, cloud):
        monkeypatch.setattr(
            "ee.usage.deployment.DeploymentMode.is_cloud", staticmethod(lambda: cloud)
        )
        return gateway, managed

    return on


def test_cloud_calls_turing_on_its_gateway(deployment):
    gateway, managed = deployment(cloud=True)
    spend = llm.Spend()

    assert llm.chat_json("s", "u", tier=llm.FAST, spend=spend) == {"ok": True}
    assert managed == []
    [request] = gateway.requests
    assert request["model"] == "turing_flash"
    assert request["timeout"] == 180
    # The gateway's own figure, from its cost header.
    assert spend.cost_usd == pytest.approx(0.004)


def test_self_hosted_calls_turing_through_the_managed_service(deployment):
    gateway, managed = deployment(cloud=False)
    spend = llm.Spend()

    answer = llm.chat_json(
        "sys", "user", tier=llm.THINKING, max_tokens=900, temperature=0.1, spend=spend
    )

    assert answer == {"ok": True}
    assert gateway.requests == []
    [(payload, timeout)] = managed
    assert payload == {
        "model": "turing_large",
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "user"},
        ],
        "temperature": 0.1,
        "max_tokens": 900,
    }
    assert timeout == 180
    # No cost header comes back: the tokens at Turing's price.
    assert (spend.calls, spend.prompt_tokens, spend.completion_tokens) == (1, 1000, 200)
    assert spend.cost_usd == pytest.approx(
        llm.call_cost("turing_large", 0.0, 1000, 200)
    )
    assert spend.cost_usd > 0


def test_self_hosted_calls_its_own_models_on_its_gateway(deployment, settings):
    gateway, managed = deployment(cloud=False)
    settings.INSIGHTS_FAST_MODEL = "gpt-4o-mini"

    assert llm.chat_json("s", "u", tier=llm.FAST) == {"ok": True}
    assert managed == []
    assert [request["model"] for request in gateway.requests] == ["gpt-4o-mini"]


def test_a_rejected_service_token_is_retried(deployment, monkeypatch):
    deployment(cloud=False)
    attempts = []

    def once_rejected(payload, *, timeout=None):
        attempts.append(payload["model"])
        if len(attempts) == 1:
            raise ManagedServiceError("TOKEN_EXPIRED", "Service token rejected")
        return {"choices": [{"message": {"content": REPLY}}], "usage": dict(USAGE)}

    monkeypatch.setattr("ee.licensing.managed_ai.chat_completion", once_rejected)
    spend = llm.Spend()

    assert llm.chat_json("s", "u", tier=llm.FAST, spend=spend) == {"ok": True}
    assert attempts == ["turing_flash", "turing_flash"]
    assert spend.calls == 1


def test_managed_service_failures_reach_users_as_model_unavailable(
    deployment, monkeypatch
):
    deployment(cloud=False)

    def refuse(payload, *, timeout=None):
        raise ManagedServiceError("SERVICE_ERROR", "Managed AI service error (502)")

    monkeypatch.setattr("ee.licensing.managed_ai.chat_completion", refuse)

    with pytest.raises(llm.InsightModelError) as caught:
        llm.chat_json("s", "u", tier=llm.FAST)
    assert caught.value.unavailable
    assert "SERVICE_ERROR" not in str(caught.value)


def test_an_error_body_is_a_provider_failure_not_a_cut_off_reply(
    deployment, monkeypatch
):
    deployment(cloud=False)
    budgets = []

    def error_body(payload, *, timeout=None):
        budgets.append(payload["max_tokens"])
        return {"error": {"message": "max_tokens is too large"}}

    monkeypatch.setattr("ee.licensing.managed_ai.chat_completion", error_body)

    with pytest.raises(llm.InsightModelError) as caught:
        llm.chat_json("s", "u", tier=llm.FAST, max_tokens=700)
    assert caught.value.unavailable
    # A cut-off reply is retried with a larger budget; an error isn't.
    assert budgets == [700, 700, 700]


def test_oss_refuses_insights_before_any_model_call(deployment, monkeypatch):
    from rest_framework.test import APIRequestFactory, force_authenticate

    from tfc.capabilities import service
    from tfc.licensing.types import DeploymentFlavor, DeploymentLocation
    from tracer import urls as tracer_urls

    gateway, managed = deployment(cloud=False)
    for name in (
        "_license_resolver",
        "_cloud_plan_resolver",
        "_deployment_flavor",
        "_deployment_location",
        "_configured",
    ):
        monkeypatch.setattr(service, name, getattr(service, name))
    service.configure(
        flavor=DeploymentFlavor.OSS, location=DeploymentLocation.SELF_HOSTED
    )

    user = SimpleNamespace(
        is_authenticated=True, organization=SimpleNamespace(id=uuid.uuid4())
    )
    factory = APIRequestFactory()
    answered = 0
    for pattern in tracer_urls.urlpatterns:
        route = str(pattern.pattern)
        if not route.startswith("insights/"):
            continue
        view = pattern.callback.view_class
        kwargs = {name: uuid.uuid4() for name in re.findall(r"<uuid:(\w+)>", route)}
        for method in ("get", "post", "put", "patch", "delete"):
            if not hasattr(view, method):
                continue
            request = getattr(factory, method)(f"/tracer/{route}")
            force_authenticate(request, user=user)
            response = view.as_view()(request, **kwargs)
            assert response.status_code == status.HTTP_402_PAYMENT_REQUIRED
            assert response.data["error"]["detail"]["feature"] == "insights"
            answered += 1

    assert answered
    assert managed == [] and gateway.requests == []
