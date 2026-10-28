"""Prices for every billed Insights action, without reading the population."""

from types import SimpleNamespace

import pytest

from ee.agenthub.insights import billing, llm
from tracer.models.insights import InsightConfig, InsightRun, InsightUnit
from tracer.tests.conftest import observe_project as observe_project
from tracer.tests.test_insights_api import insight_enabled as insight_enabled


@pytest.mark.django_db
@pytest.mark.parametrize("action", ["ask", "draft", "preview"])
@pytest.mark.parametrize("cloud", [False, True])
def test_action_estimate_matches_the_billed_step(
    auth_client, observe_project, monkeypatch, action, cloud
):
    monkeypatch.setattr(billing, "applies", lambda: cloud)
    checked = []
    monkeypatch.setattr(billing, "check", lambda org, cost: checked.append(cost))
    monkeypatch.setattr(billing, "credits", lambda cost: cost * 100)
    expected = getattr(billing, f"{action}_cost")()
    response = auth_client.post(
        "/tracer/insights/estimate/",
        {"project_id": str(observe_project.id), "action": action},
        format="json",
    )
    assert response.status_code == 200, response.json()
    data = response.json()["result"]
    assert data["credits_estimate"] == (expected * 100 if cloud else None)
    assert data["credit_limited"] is False
    assert data.get("credit_check_unavailable", False) is False
    assert checked == ([expected] if cloud else [])
    assert data["sample_ids"] == []


# The models Cloud's Insights agent runs on.
AGENT_MODELS = {"fast": "claude-haiku-5-5", "thinking": "claude-sonnet-5-5"}


@pytest.fixture
def cloud_agent(monkeypatch):
    """A v2 agent whose health names AGENT_MODELS; counts health calls."""
    from django.core.cache import cache

    from ee.agenthub.insights import agent

    asked = []
    cache.delete("insights:agent-models")
    monkeypatch.setattr(agent, "enabled", lambda: True)
    monkeypatch.setattr(agent, "expected_contract", lambda: agent.CONTRACT_V2)
    monkeypatch.setattr(
        agent, "_health", lambda: asked.append(1) or {"models": AGENT_MODELS}
    )
    monkeypatch.setattr(billing, "applies", lambda: True)
    monkeypatch.setattr(billing, "check", lambda org, cost: None)
    monkeypatch.setattr(billing, "credits", lambda cost: cost * 100)
    yield asked
    cache.delete("insights:agent-models")


@pytest.mark.django_db
def test_steps_on_the_agent_are_priced_at_the_agents_models(
    auth_client, observe_project, cloud_agent
):
    """Cloud's backend keeps the Turing tier models while its agent reads
    and writes on Claude Haiku 5.5 and Sonnet 5.5: a 21-session report was
    estimated at 245 credits and cost 45 at list prices."""
    from ee.agenthub.insights import agent

    def estimate(**body):
        response = auth_client.post(
            "/tracer/insights/estimate/",
            {"project_id": str(observe_project.id), "unit": "sessions", **body},
            format="json",
        )
        assert response.status_code == 200, response.json()
        return response.json()["result"]["credits_estimate"]

    run = estimate(population_size=21)
    v2 = {"contract": agent.CONTRACT_V2, "unit": "sessions"}
    assert run == pytest.approx(billing.run_cost(21, models=AGENT_MODELS, **v2) * 100)
    assert run < billing.run_cost(21, **v2) * 100 / 3
    assert estimate(action="preview") == pytest.approx(
        billing.preview_cost(AGENT_MODELS) * 100
    )
    assert estimate(action="ask") == pytest.approx(
        billing.ask_cost(agent=True, models=AGENT_MODELS) * 100
    )
    # Drafts are the built-in engine's, on this deployment's model.
    assert estimate(action="draft") == pytest.approx(billing.draft_cost() * 100)
    # The agent was asked once; its answer is kept.
    assert len(cloud_agent) == 1


def _usd(model, tokens_in, tokens_out):
    return llm.call_cost(model, 0.0, tokens_in, tokens_out)


HAIKU, SONNET = AGENT_MODELS["fast"], AGENT_MODELS["thinking"]


@pytest.mark.parametrize(
    ("step", "actual"),
    [
        # Cloud's test steps, from the agent's request log, at list prices.
        # A first 21-session report: reading, naming the categories, writing.
        (
            "first report",
            _usd(HAIKU, 255_683, 43_631)
            + _usd(SONNET, 8_554, 685)
            + _usd(SONNET, 112_848, 11_384),
        ),
        # Run again (18,301 characters a session, measured), classifying
        # into the saved categories on the built-in engine.
        (
            "report again",
            _usd(HAIKU, 281_139, 43_270)
            + _usd(llm.model_for(llm.FAST), 1_279, 478)
            + _usd(SONNET, 123_080, 10_842),
        ),
        # Preview: 3 sessions in 3 calls.
        ("preview", _usd(HAIKU, 6_793, 1_896)),
        # A question about the report: 7 calls.
        ("ask", _usd(SONNET, 50_299, 4_075)),
        # Four drafts were charged $0.0102 to $0.0109 at $3 / $15 a million.
        ("draft", 0.0109),
    ],
)
def test_estimates_are_within_twice_what_clouds_test_steps_cost(step, actual):
    from ee.agenthub.insights import agent

    v2 = {"contract": agent.CONTRACT_V2, "unit": "sessions", "models": AGENT_MODELS}
    estimate = {
        "first report": lambda: billing.run_cost(21, **v2),
        "report again": lambda: billing.run_cost(21, unit_chars=18_301, **v2),
        "preview": lambda: billing.preview_cost(AGENT_MODELS),
        "ask": lambda: billing.ask_cost(agent=True, models=AGENT_MODELS),
        "draft": lambda: (billing.DRAFT_TOKENS[0] * 3 + billing.DRAFT_TOKENS[1] * 15)
        / 1_000_000,
    }[step]()
    assert actual / 2 <= estimate <= actual * 2


# Naming the categories and writing, on Cloud's test reports of 18 to 21
# sessions (the agent's request log): two categories investigated, then three
# on four reports (the last one classified into saved categories, unnamed).
WRITE_TWO = (4_631 + 86_047, 469 + 9_280)
WRITE_THREE = [
    (6_285 + 135_428, 888 + 11_251),
    (8_554 + 112_848, 685 + 11_384),
    (7_145 + 126_393, 660 + 9_055),
    (123_080, 10_842),
]


@pytest.mark.parametrize(
    ("categories", "measured"),
    [(2, WRITE_TWO), *((3, tokens) for tokens in WRITE_THREE)],
)
def test_a_reports_writing_is_sized_by_the_categories_it_investigates(
    categories, measured
):
    write = [
        base + categories * each
        for base, each in zip(
            billing.AGENT_REPORT_TOKENS, billing.AGENT_CATEGORY_TOKENS, strict=True
        )
    ]
    actual = _usd(SONNET, *measured)
    assert actual / 1.25 <= _usd(SONNET, *write) <= actual * 1.25


def test_a_report_of_fewer_than_8_units_is_written_for_its_one_category():
    """Cloud estimated a report of 4 sessions at 74 credits and one of 49 at
    130: 68 of each was writing, sized for the three categories its test
    reports investigated. Fewer than 8 units make one category, so one
    investigator. No report that small was measured: its writing is the
    measured two-category one less what a category added, and its reading
    what a session took on the 18-session report."""
    from ee.agenthub.insights import agent

    v2 = {"contract": agent.CONTRACT_V2, "unit": "sessions", "models": AGENT_MODELS}
    three = [sum(each) / len(WRITE_THREE) for each in zip(*WRITE_THREE, strict=True)]
    one = [2 * two - each for two, each in zip(WRITE_TWO, three, strict=True)]
    actual = _usd(HAIKU, 4 * 223_591 // 18, 4 * 38_605 // 18) + _usd(SONNET, *one)
    assert actual / 1.5 <= billing.run_cost(4, **v2) <= actual * 1.5
    sizes = (1, 4, 7, 8, 21, 300)
    assert [billing.report_categories(n) for n in sizes] == [1, 1, 1, 3, 3, 3]
    # An eighth unit is read, and splits the report into the three measured.
    assert billing.run_cost(8, **v2) - billing.run_cost(7, **v2) == pytest.approx(
        _usd(HAIKU, *billing.read_tokens(agent.CONTRACT_V2, "sessions"))
        + 2 * _usd(SONNET, *billing.AGENT_CATEGORY_TOKENS)
    )


@pytest.mark.django_db
@pytest.mark.parametrize("action", ["ask", "draft", "preview"])
def test_action_estimate_explains_a_credit_refusal(
    auth_client, observe_project, monkeypatch, action
):
    monkeypatch.setattr(billing, "applies", lambda: True)
    monkeypatch.setattr(billing, "credits", lambda cost: 2)
    monkeypatch.setattr(billing, "check", lambda *args: SimpleNamespace(
        error_code="FREE_TIER_LIMIT", limit=100, current_usage=99,
    ))
    response = auth_client.post(
        "/tracer/insights/estimate/",
        {"project_id": str(observe_project.id), "action": action}, format="json",
    )
    assert response.status_code == 200, response.json()
    data = response.json()["result"]
    assert data["credit_limited"] is True
    assert data["credit_check_unavailable"] is False
    assert data["credits_left"] == 1
    assert "needs about 2 credits" in data["credit_message"]
    assert data["credit_max_sample"] is None


@pytest.mark.django_db
@pytest.mark.parametrize("action", ["run", "ask", "draft", "preview"])
def test_action_estimate_fails_closed_when_credits_cannot_be_checked(
    auth_client, observe_project, monkeypatch, action,
):
    monkeypatch.setattr(billing, "applies", lambda: True)

    def unavailable(*args):
        raise billing.CreditCheckUnavailable()

    monkeypatch.setattr(billing, "check", unavailable)
    body = {"project_id": str(observe_project.id)}
    body.update(
        {"unit": "sessions", "population_size": 10}
        if action == "run" else {"action": action}
    )
    response = auth_client.post(
        "/tracer/insights/estimate/", body, format="json",
    )
    assert response.status_code == 200, response.json()
    data = response.json()["result"]
    assert data["credit_limited"] is True
    assert data["credit_check_unavailable"] is True
    assert "Please retry in a moment." in data["credit_message"]


@pytest.mark.django_db
@pytest.mark.parametrize("analysed", [0, 3])
def test_failed_history_includes_actual_skips_and_reason(
    auth_client, observe_project, organization, workspace, user, analysed,
):
    config = InsightConfig.objects.create(
        project=observe_project, organization=organization, workspace=workspace,
        created_by=user, name="Failures", unit="sessions", lens="failures",
        question="Where did the agent fail?",
    )
    run = InsightRun.objects.create(
        config=config, project=observe_project, organization=organization,
        workspace=workspace, unit="sessions", status="failed", sample_size=130,
        error="There are no readable sessions in this sample.",
    )
    InsightUnit.objects.bulk_create([
        InsightUnit(run=run, unit_id=f"s{i}", status="skipped") for i in range(125)
    ] + [
        InsightUnit(run=run, unit_id=f"read-{i}", status="done")
        for i in range(analysed)
    ] + [
        InsightUnit(run=run, unit_id="pending", status="pending"),
        InsightUnit(run=run, unit_id="excluded", status="done", included=False),
    ])
    detail = auth_client.get(f"/tracer/insights/runs/{run.id}/").json()["result"]
    history = auth_client.get(f"/tracer/insights/configs/{config.id}/").json()["result"]
    for item in (detail, history["latest_run"], history["runs"][0]):
        assert item["skipped_count"] == 125
        assert item["analysed_count"] == analysed
        assert item["error"] == run.error
