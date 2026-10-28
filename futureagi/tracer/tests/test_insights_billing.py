"""Insights billing: model calls are priced (the gateway's figure, else the
model's token price), a written report is charged once as AI credits, and
the credit cap refuses a report, a question, a draft or a preview up front."""

import json

import pytest
from rest_framework import status

pytest.importorskip("ee.agenthub.insights")

from ee.agenthub.insights import billing, llm, pipeline  # noqa: E402
from ee.usage.schemas.events import CheckResult  # noqa: E402
from tracer.models.insights import InsightRun  # noqa: E402
from tracer.tests.test_insights_api import (  # noqa: E402, F401
    RUNS,
    insight_enabled,
    new_run_body,
)
from tracer.views import insights as views  # noqa: E402


@pytest.fixture(autouse=True)
def on_cloud(monkeypatch):
    """Insights is billed on Cloud only."""
    from ee.usage.deployment import DeploymentMode

    monkeypatch.setattr(DeploymentMode, "is_cloud", staticmethod(lambda: True))


@pytest.fixture(autouse=True)
def credits_allow(monkeypatch):
    """Billing lets every step run unless a test refuses it. The check fails
    closed, so without this a test would read the billing Redis's counters
    (or, with no Redis, be refused)."""
    monkeypatch.setattr(
        "ee.usage.services.metering.check_usage",
        lambda org_id, event_type, amount=1: CheckResult(allowed=True),
    )


@pytest.fixture
def config_missing(monkeypatch):
    """billing.yaml doesn't load: on Cloud BillingConfig raises when it is
    missing or fails validation."""
    from ee.usage.services.config import BillingConfig

    def missing():
        raise RuntimeError("no billing config")

    monkeypatch.setattr(BillingConfig, "get", staticmethod(missing))


@pytest.fixture
def started(request):
    """Runs the view handed to Temporal (the API tests' autouse stub)."""
    return request.getfixturevalue("insight_enabled")


@pytest.fixture
def emitted(monkeypatch):
    events = []
    monkeypatch.setattr("ee.usage.services.emitter.emit", events.append)
    return events


@pytest.fixture
def refuse(monkeypatch):
    """Billing refuses whatever is checked, with ``left`` credits left."""

    def set_refusal(error_code="FREE_TIER_LIMIT", left=0.0):
        checked = []

        def check_usage(org_id, event_type, amount=1):
            checked.append((event_type, amount))
            return CheckResult(
                allowed=False,
                error_code=error_code,
                reason="refused",
                current_usage=100 - left,
                limit=100,
            )

        monkeypatch.setattr("ee.usage.services.metering.check_usage", check_usage)
        return checked

    return set_refusal


class TestPricing:
    def test_the_gateway_figure_wins(self):
        assert llm.call_cost("turing_flash", 0.0123, 1000, 100) == 0.0123

    def test_a_zero_from_the_gateway_falls_back_to_the_token_price(self):
        priced = llm.call_cost("turing_flash", 0.0, 1_000_000, 100_000)
        assert priced > 0
        assert priced == llm.call_cost("turing_flash", 0.0, 1_000_000, 100_000)

    @pytest.mark.parametrize(
        ("model", "per_million"),
        [
            # Cloud's Insights agent, through AgentCC: Anthropic list prices.
            ("claude-haiku-5-5", 0.1 + 0.5),
            ("claude-sonnet-5-5", 2 + 10),
            # The built-in engine's defaults.
            ("turing_flash", 1.1 + 5.5),
            ("turing_large", 5.5 + 27.5),
        ],
    )
    def test_the_models_cloud_runs_insights_on_are_never_priced_at_the_ceiling(
        self, model, per_million
    ):
        """A call the gateway didn't price (cut off, or no cost header) falls
        back to the price list. Haiku 5.5 wasn't on it, so it was charged
        the $15 / $75 ceiling, and Sonnet 5.5 matched Sonnet 5's entry."""
        cost, priced = llm.usage_cost(model, None, 1_000_000, 1_000_000)
        assert priced is True
        assert cost == pytest.approx(per_million)

    def test_no_tokens_or_no_model_costs_nothing(self):
        assert llm.call_cost("turing_flash", 0.0, 0, 0) == 0.0
        assert llm.call_cost("", 0.0, 500, 50) == 0.0

    def test_a_reply_that_isnt_json_is_still_counted(self, monkeypatch):
        replies = iter([("not json", 0.01, 10, 5), ('{"ok": true}', 0.02, 20, 6)])
        monkeypatch.setattr(llm, "_complete", lambda *a: next(replies))
        monkeypatch.setattr(llm.time, "sleep", lambda *_: None)
        spend = llm.Spend()
        assert llm.chat_json("s", "u", tier=llm.FAST, spend=spend) == {"ok": True}
        assert spend.calls == 2
        assert spend.cost_usd == pytest.approx(0.03)
        assert (spend.prompt_tokens, spend.completion_tokens) == (30, 11)

    def test_the_agents_tokens_are_priced_per_model(self, monkeypatch):
        """The agent's real usage shape: totals plus ``by_model``, with
        ``cost_usd`` null when the gateway didn't price a model."""
        from ee.agenthub.insights import agent

        usage = {
            "model_calls": 5,
            "input_tokens": 26000,
            "output_tokens": 3300,
            "cost_usd": None,
            "by_model": {
                "turing_flash": {
                    "model_calls": 4,
                    "input_tokens": 20000,
                    "output_tokens": 2000,
                },
                "turing_large": {
                    "model_calls": 1,
                    "input_tokens": 6000,
                    "output_tokens": 1300,
                },
            },
        }
        spend = llm.Spend()
        agent._add_usage(spend, usage)
        assert spend.cost_usd == pytest.approx(
            llm.call_cost("turing_flash", 0.0, 20000, 2000)
            + llm.call_cost("turing_large", 0.0, 6000, 1300)
        )
        assert spend.cost_usd > 0
        assert (spend.prompt_tokens, spend.completion_tokens) == (26000, 3300)

        # A model the gateway priced keeps the gateway's figure.
        usage["by_model"]["turing_large"]["cost_usd"] = 0.5
        priced = llm.Spend()
        agent._add_usage(priced, usage)
        assert priced.cost_usd == pytest.approx(
            llm.call_cost("turing_flash", 0.0, 20000, 2000) + 0.5
        )

    def test_the_agent_reports_tokens_when_it_has_no_cost(self, monkeypatch):
        from ee.agenthub.insights import agent

        body = {
            "usage": {
                "model": "turing_large",
                "prompt_tokens": 4000,
                "completion_tokens": 900,
                "cost_usd": 0,
            }
        }
        _agent_replies(monkeypatch, agent, 200, json.dumps(body).encode())
        spend = llm.Spend()
        agent._post("/v1/ask", {}, spend)
        assert spend.cost_usd == llm.call_cost("turing_large", 0.0, 4000, 900) > 0

    @pytest.mark.parametrize(
        "content",
        [b"<html>bad gateway</html>", b'["not", "an", "object"]', None],
    )
    def test_an_unreadable_or_oversized_agent_reply_is_unavailable(
        self, monkeypatch, content
    ):
        from ee.agenthub.insights import agent

        if content is None:
            # Read in chunks and given up on past the cap, never held whole:
            # a well-formed object, so only the cap refuses it.
            content = b'{"pad": "' + b"x" * agent.MAX_RESPONSE_BYTES + b'"}'
        _agent_replies(monkeypatch, agent, 200, content)
        with pytest.raises(llm.InsightModelError) as error:
            agent._post("/v1/ask", {}, llm.Spend())
        assert error.value.unavailable


class TestCharging:
    def test_credits_follow_billings_conversion_and_nothing_spent_is_free(self):
        from ee.usage.services.config import BillingConfig

        assert billing.credits(0) == 0.0
        assert billing.credits(0.05) == pytest.approx(
            BillingConfig.get().calculate_ai_credits(0.05), abs=1e-4
        )

    @pytest.mark.django_db
    def test_a_written_report_is_charged_once_under_a_stable_id(
        self, auth_client, observe_project, emitted
    ):
        response = auth_client.post(RUNS, new_run_body(observe_project), format="json")
        run = InsightRun.objects.get(id=response.json()["result"]["run_id"])
        run.cost_usd = 0.42
        assert billing.charge_run(run) == billing.credits(0.42)
        billing.charge_run(run)
        assert len(emitted) == 2
        first, again = emitted
        assert first.event_type == "insights_report"
        assert first.org_id == str(run.organization_id)
        assert first.amount == billing.credits(0.42)
        assert first.properties["source"] == "insights_report"
        assert first.properties["raw_cost_usd"] == "0.42"
        # A retried finish re-emits the same id; the consumer bills it once.
        assert first.event_id == again.event_id

    @pytest.mark.django_db
    def test_a_report_is_charged_with_the_models_it_ran_on(
        self, auth_client, observe_project, emitted
    ):
        """Cloud's agent writes on Claude while this deployment's tier models
        are Turing: the event names the models the report was started on."""
        response = auth_client.post(RUNS, new_run_body(observe_project), format="json")
        run = InsightRun.objects.get(id=response.json()["result"]["run_id"])
        run.cost_usd = 0.42
        run.models_used = {"fast": "claude-haiku-5-5", "thinking": "claude-sonnet-5-5"}
        billing.charge_run(run)
        # A run from before models were kept: this deployment's models.
        run.models_used = {}
        billing.charge_run(run)
        on_agent, built_in = (
            (e.properties["fast_model"], e.properties["thinking_model"])
            for e in emitted
        )
        assert on_agent == ("claude-haiku-5-5", "claude-sonnet-5-5")
        assert built_in == (llm.model_for(llm.FAST), llm.model_for(llm.THINKING))

    @pytest.mark.django_db
    def test_a_report_that_spent_nothing_is_not_charged(
        self, auth_client, observe_project, emitted
    ):
        response = auth_client.post(RUNS, new_run_body(observe_project), format="json")
        run = InsightRun.objects.get(id=response.json()["result"]["run_id"])
        assert billing.charge_run(run) == 0.0
        assert emitted == []

    @pytest.mark.django_db
    def test_finishing_a_report_charges_it_and_failing_one_does_not(
        self, auth_client, observe_project, emitted, monkeypatch
    ):
        response = auth_client.post(RUNS, new_run_body(observe_project), format="json")
        run_id = response.json()["result"]["run_id"]
        # As its read stages leave it: finish completes only a running run.
        InsightRun.objects.filter(id=run_id).update(cost_usd=0.3, status="running")

        def write(run, result, units, contents, spend, **_):
            spend.add(0.1)
            return {
                "title": "t",
                "findings": [],
                "key_points": [],
                "highlights": [],
                "examples": [],
            }

        monkeypatch.setattr(pipeline.report, "write", write)
        pipeline.finish(run_id)
        run = InsightRun.objects.get(id=run_id)
        assert run.status == "completed"
        assert [e.amount for e in emitted] == [billing.credits(0.4)]

        emitted.clear()
        pipeline.fail(run_id, "boom")
        assert emitted == []


class TestCreditCap:
    @pytest.mark.django_db
    def test_a_report_over_the_cap_is_refused_before_it_starts(
        self, auth_client, observe_project, started, refuse
    ):
        checked = refuse(left=0.0)
        response = auth_client.post(
            RUNS, new_run_body(observe_project, n=30, sample_size=10), format="json"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        body = response.json()
        assert body["code"] == "insights_credits"
        detail = body["result"]
        assert detail["error_code"] == "FREE_TIER_LIMIT"
        assert detail["credits_estimate"] == billing.credits(billing.run_cost(10))
        assert detail["credits_left"] == 0.0
        assert "you have none left this month" in detail["message"]
        assert checked == [("insights_report", billing.credits(billing.run_cost(10)))]
        assert not InsightRun.objects.exists()
        assert started == []

    @pytest.mark.django_db
    def test_the_refusal_names_the_largest_sample_that_fits(
        self, auth_client, observe_project, started, refuse
    ):
        left = billing.credits(billing.run_cost(200)) + 1e-3
        refuse(left=left)
        response = auth_client.post(
            RUNS, new_run_body(observe_project, n=900, sample_size=800), format="json"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        detail = response.json()["result"]
        assert detail["sample_size"] >= 200
        assert billing.credits(billing.run_cost(detail["sample_size"])) <= left
        assert (
            f"A sample of up to {detail['sample_size']:,} sessions fits."
            in detail["message"]
        )

    @pytest.mark.django_db
    def test_a_report_that_would_cross_a_pause_budget_is_refused_before_it_starts(
        self, auth_client, organization, observe_project, started, monkeypatch
    ):
        """A pause budget is crossed only once the usage is counted, so a
        report that started under it ran whole past it. It is refused up
        front, with what it needs and what the budget allows."""
        from ee.usage.tests.test_pause_budgets import pause_budget

        needed = billing.credits(billing.run_cost(10))
        # Half of what the report needs is left under the budget.
        pause_budget(monkeypatch, organization, 100, 100 - needed / 2)
        response = auth_client.post(
            RUNS, new_run_body(observe_project, n=30, sample_size=10), format="json"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        body = response.json()
        assert body["code"] == "insights_credits"
        detail = body["result"]
        assert detail["error_code"] == "BUDGET_LIMIT"
        assert detail["credits_estimate"] == needed
        assert detail["credits_left"] == pytest.approx(needed / 2)
        assert detail["message"] == (
            f"This report needs about {views._credit_count(needed)}; your budget "
            f"limit allows {views._credit_amount(needed / 2)} more this month. "
            "Raise or remove it in billing to run this."
        )
        assert not InsightRun.objects.exists()
        assert started == []

    @pytest.mark.django_db
    def test_a_report_under_a_pause_budget_starts(
        self, auth_client, organization, observe_project, started, monkeypatch
    ):
        from ee.usage.tests.test_pause_budgets import pause_budget

        needed = billing.credits(billing.run_cost(10))
        pause_budget(monkeypatch, organization, 100, 100 - needed)
        response = auth_client.post(
            RUNS, new_run_body(observe_project, n=30, sample_size=10), format="json"
        )
        assert response.status_code == status.HTTP_200_OK, response.content
        assert len(started) == 1

    @pytest.mark.django_db
    def test_unpaid_invoices_refuse_with_402(
        self, auth_client, observe_project, started, refuse
    ):
        refuse(error_code="PAYMENT_REQUIRED")
        response = auth_client.post(RUNS, new_run_body(observe_project), format="json")
        assert response.status_code == status.HTTP_402_PAYMENT_REQUIRED
        assert "unpaid invoices" in response.json()["result"]["message"]

    @pytest.mark.django_db
    def test_a_billing_outage_refuses_the_report_with_503(
        self, auth_client, observe_project, started, monkeypatch
    ):
        """Reports run on Future AGI's model keys: a Redis or billing outage
        refuses (fail closed) instead of starting an unchecked report."""

        def unavailable(*a, **k):
            raise RuntimeError("no billing redis here")

        monkeypatch.setattr("ee.usage.services.metering.check_usage", unavailable)
        response = auth_client.post(RUNS, new_run_body(observe_project), format="json")
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert "Credits can't be checked" in str(response.json())
        assert started == []
        assert not InsightRun.objects.filter(project=observe_project).exists()

    @pytest.mark.django_db
    def test_a_cloud_without_its_billing_config_refuses_the_report_with_503(
        self, auth_client, observe_project, started, config_missing, refuse
    ):
        """A billing config that doesn't load is an outage too, not a
        deployment that doesn't bill."""
        checked = refuse(left=0.0)
        response = auth_client.post(RUNS, new_run_body(observe_project), format="json")
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert response.json()["code"] == "credit_check_unavailable"
        assert checked == []
        assert started == []
        assert not InsightRun.objects.filter(project=observe_project).exists()


@pytest.mark.django_db
def test_a_question_is_checked_then_charged_and_added_to_the_run(
    auth_client, observe_project, emitted, refuse, monkeypatch
):
    response = auth_client.post(RUNS, new_run_body(observe_project), format="json")
    run_id = response.json()["result"]["run_id"]
    InsightRun.objects.filter(id=run_id).update(
        status="completed", cost_usd=0.5, result={"categories": [{"id": "a"}]}
    )

    def answer(run, result, units, contents, question, history, spend):
        spend.add(0.02)
        return {"answer": [], "follow_ups": []}

    monkeypatch.setattr("ee.agenthub.insights.report.answer", answer)
    ask = f"{RUNS}{run_id}/ask/"
    assert auth_client.post(ask, {"question": "why?"}, format="json").status_code == 200
    assert [(e.properties["source"], e.amount) for e in emitted] == [
        ("insights_ask", billing.credits(0.02))
    ]
    assert InsightRun.objects.get(id=run_id).cost_usd == pytest.approx(0.52)

    refuse(left=0.0)
    refused = auth_client.post(ask, {"question": "and?"}, format="json")
    assert refused.status_code == status.HTTP_403_FORBIDDEN
    assert refused.json()["result"]["message"].startswith("This question needs about")


@pytest.mark.django_db
def test_a_preview_is_charged_with_its_tokens_and_ceiling_flag(
    auth_client, observe_project, insight_enabled, emitted, monkeypatch
):
    """Cloud's preview was charged at the unpriced ceiling, but its usage
    event neither said so nor carried the tokens to check it by."""

    def preview(*, spend, **kwargs):
        spend.add(0.244, 6_793, 1_896, unpriced=True)
        return []

    monkeypatch.setattr("ee.agenthub.insights.draft.preview", preview)
    body = new_run_body(observe_project)
    response = auth_client.post(
        "/tracer/insights/preview/",
        {
            "project_id": body["project_id"],
            "config": body["config"],
            "unit_ids": ["s1"],
        },
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.content
    (event,) = emitted
    assert event.properties["source"] == "insights_preview"
    assert event.properties["unpriced"] is True
    assert (
        event.properties["model_calls"],
        event.properties["prompt_tokens"],
        event.properties["completion_tokens"],
    ) == (1, 6_793, 1_896)
    # No agent here: the built-in engine read it, on this deployment's models.
    assert event.properties["fast_model"] == llm.model_for(llm.FAST)


@pytest.mark.parametrize(
    ("enabled", "engine", "ran_on"),
    [
        # A preview on the agent: the models it reports.
        (True, None, {"fast": "claude-haiku-5-5", "thinking": "claude-sonnet-5-5"}),
        # A question about a report written on the agent: the report's models.
        (
            True,
            {"kind": "omega", "models": {"fast": "f", "thinking": "t"}},
            {"fast": "f", "thinking": "t"},
        ),
        # A report the built-in engine wrote, or no agent: this deployment's.
        (True, {"kind": "builtin"}, None),
        (False, None, None),
    ],
)
def test_a_step_names_the_models_it_ran_on(monkeypatch, enabled, engine, ran_on):
    from ee.agenthub.insights import agent

    monkeypatch.setattr(agent, "enabled", lambda: enabled)
    monkeypatch.setattr(
        agent,
        "expected_models",
        lambda: {"fast": "claude-haiku-5-5", "thinking": "claude-sonnet-5-5"},
    )
    assert views._ran_on(engine) == ran_on


def test_the_estimate_carries_the_credit_fields(monkeypatch, refuse):
    class Org:
        id = "org-1"

    assert views._credit_estimate(Org(), 0, "sessions")["credits_estimate"] == 0.0
    refuse(left=0.0)
    out = views._credit_estimate(Org(), 300, "sessions")
    assert out["credits_estimate"] == billing.credits(billing.run_cost(300)) > 0
    assert out["credit_limited"] is True
    assert out["credits_left"] == 0.0
    assert out["credit_max_sample"] is None
    assert "This report needs about" in out["credit_message"]


class Org:
    id = "org-1"


@pytest.mark.django_db
def test_rows_handed_over_are_priced_like_a_scope(auth_client, observe_project):
    """A selection or a run again on the same rows shows its credits too."""
    response = auth_client.post(
        "/tracer/insights/estimate/",
        {
            "project_id": str(observe_project.id),
            "unit": "sessions",
            "population_size": 37,
        },
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.json()
    result = response.json()["result"]
    assert result["credits_estimate"] == billing.credits(billing.run_cost(37)) > 0
    assert result["credit_limited"] is False


def test_a_billing_outage_limits_the_estimate(monkeypatch):
    def unavailable(*a, **k):
        raise RuntimeError("no billing redis here")

    monkeypatch.setattr("ee.usage.services.metering.check_usage", unavailable)
    out = views._credit_estimate(Org(), 300, "sessions")
    assert out["credit_limited"] is True
    assert "Credits can't be checked" in out["credit_message"]


@pytest.mark.parametrize("config_loads", [True, False], ids=["config", "no-config"])
def test_off_cloud_nothing_is_estimated_or_checked_in_credits(
    monkeypatch, refuse, request, config_loads
):
    from ee.usage.deployment import DeploymentMode

    monkeypatch.setattr(DeploymentMode, "is_cloud", staticmethod(lambda: False))
    if not config_loads:
        request.getfixturevalue("config_missing")
    checked = refuse(left=0.0)
    out = views._credit_estimate(Org(), 300, "sessions")
    assert out == {
        "credits_estimate": None,
        "credit_limited": False,
        "credits_left": None,
        "credit_max_sample": None,
        "credit_message": None,
    }
    assert views._credits_refused(views.GeneralMethods(), Org(), "ask") is None
    assert checked == []


def test_a_cloud_without_its_billing_config_limits_the_estimate(config_missing, refuse):
    """Fail closed, as when billing's Redis is down: the report can't start
    until billing answers."""
    checked = refuse(left=0.0)
    out = views._credit_estimate(Org(), 300, "sessions")
    assert out["credit_limited"] is True
    assert out["credit_message"] == views.CREDIT_CHECK_UNAVAILABLE
    # Not null: a null estimate tells the page the report isn't billed.
    assert out["credits_estimate"] is not None
    assert checked == []


@pytest.mark.django_db
@pytest.mark.parametrize("step", ["ask", "draft", "preview"])
def test_a_reached_pause_budget_refuses_every_step_and_its_estimate(
    monkeypatch, organization, step
):
    from ee.usage.tests.test_pause_budgets import pause_budget

    pause_budget(monkeypatch, organization, 2, 2)
    refused = views._credits_refused(views.GeneralMethods(), organization, step)
    assert refused.status_code == status.HTTP_403_FORBIDDEN
    detail = refused.data["result"]
    assert detail["error_code"] == "BUDGET_LIMIT"
    assert detail["credits_left"] == 0.0
    assert detail["message"].startswith(f"{views._STEPS[step]} needs about")
    assert detail["message"].endswith(
        "your budget limit allows no more this month. "
        "Raise or remove it in billing to run this."
    )
    out = views._credit_estimate(organization, 0, None, step=step)
    assert out["credit_limited"] is True
    assert out["credit_message"] == detail["message"]


@pytest.mark.parametrize("step", ["run", "ask", "draft", "preview"])
def test_a_cloud_without_its_billing_config_refuses_every_step_with_503(
    config_missing, refuse, step
):
    checked = refuse(left=0.0)
    refused = views._credits_refused(
        views.GeneralMethods(),
        Org(),
        step,
        **({"sample_size": 300, "unit": "sessions"} if step == "run" else {}),
    )
    assert refused is not None
    assert refused.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert refused.data["code"] == "credit_check_unavailable"
    assert checked == []


@pytest.mark.django_db
def test_off_cloud_a_report_over_any_cap_starts(
    auth_client, observe_project, started, refuse, monkeypatch
):
    from ee.usage.deployment import DeploymentMode

    monkeypatch.setattr(DeploymentMode, "is_cloud", staticmethod(lambda: False))
    checked = refuse(left=0.0)
    response = auth_client.post(RUNS, new_run_body(observe_project), format="json")
    assert response.status_code == 200, response.content
    assert len(started) == 1
    assert checked == []


def _agent_replies(monkeypatch, agent, status_code, content):
    """httpx.stream answering with ``content`` in 1 MB chunks."""
    import contextlib

    class Reply:
        def __init__(self):
            self.status_code = status_code

        def iter_bytes(self):
            for start in range(0, len(content), 1 << 20):
                yield content[start : start + (1 << 20)]

    monkeypatch.setattr(
        agent, "_setting", lambda name: "http://agent" if "URL" in name else "x"
    )
    monkeypatch.setattr(
        agent.httpx, "stream", lambda *a, **k: contextlib.nullcontext(Reply())
    )
