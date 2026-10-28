"""The built-in Insights engine's stages, run in the workflow's order on a
real database: prepare -> extract -> retry the failed -> organise -> finish.

Only the edges are faked: the unit text ClickHouse would return, the
embeddings and the model's replies. A first run discovers categories and
freezes them on the report; later runs classify into them, with leftovers
becoming "Emerging" categories when there are enough of them, else "Didn't
fit a category"."""

import re
import threading
from datetime import UTC, datetime

import numpy as np
import pytest

pytest.importorskip("ee.agenthub.insights")

from ee.agenthub.insights import (  # noqa: E402
    agent,
    clustering,
    facets,
    pipeline,
    summary,
)
from ee.agenthub.insights.llm import InsightModelError  # noqa: E402
from ee.agenthub.insights.units import UnitText  # noqa: E402
from tracer.models.insights import InsightConfig, InsightRun, InsightUnit  # noqa: E402

WINDOW = ["2026-10-01T00:00:00+00:00", "2026-10-08T00:00:00+00:00"]

# What the agent got wrong in each conversation, by theme.
LENS_ANSWERS = {
    "order": "Agent re-asks the order number when tracking a parcel",
    "human": "Agent ignores a request for a human when the customer insists",
    "refund": "Agent invents a refund policy when asked about returns",
    "stock": "Agent makes up stock levels when asked about sizes",
    "billing": "Agent double charges the card when updating billing details",
}
NAMES = {
    "order": "Re-asks Order Number",
    "human": "Ignores Requests For A Human",
    "refund": "Invents Refund Policy",
    "stock": "Makes Up Stock Levels",
    "billing": "Double Charges On Billing",
}
THEMES = list(LENS_ANSWERS)


def theme_of(text: str) -> str | None:
    lowered = text.lower()
    for theme, keyword in (
        ("order", "order number"),
        ("human", "human"),
        ("refund", "refund"),
        ("stock", "stock"),
        ("billing", "billing"),
    ):
        if keyword in lowered:
            return theme
    return None


class FakeModel:
    """Replies for every model call the built-in engine makes, routed by the
    stage's system prompt; records what each stage was asked."""

    def __init__(self):
        self.flaky = set()
        self.calls = {
            "extract": 0,
            "name": 0,
            "organise": 0,
            "classify": 0,
            "summary": 0,
        }
        self._lock = threading.Lock()

    def __call__(self, system, prompt, *, tier, **_):
        if "<content>" in prompt:
            return self.extract(prompt)
        if system == clustering._NAME_SYSTEM:
            return self.name(prompt)
        if system == clustering._ORGANISE_SYSTEM:
            return self.organise(prompt)
        if system == clustering._CLASSIFY_SYSTEM:
            return self.classify(prompt)
        if system == summary._SYSTEM:
            return self.summary()
        raise AssertionError(f"unexpected model call: {system[:60]}")

    def _count(self, stage):
        with self._lock:
            self.calls[stage] += 1

    def extract(self, prompt):
        self._count("extract")
        content = prompt.split("<content>", 1)[1].split("</content>", 1)[0]
        unit_id = re.search(r"unit=(\S+)", content).group(1)
        with self._lock:
            if unit_id in self.flaky:
                # A transient provider error, once.
                self.flaky.discard(unit_id)
                raise InsightModelError(
                    "The analysis model is unavailable.", unavailable=True
                )
        theme = re.search(r"theme=(\w+)", content).group(1)
        quiet = theme == "quiet"
        return {
            "summary": f"Conversation {unit_id} about {theme}.",
            "lens_answer": "none" if quiet else LENS_ANSWERS[theme],
            "topic": "" if quiet else NAMES[theme],
            "outcome": "resolved" if quiet else "unresolved",
            "sentiment": 4 if quiet else 2,
            "failure_present": not quiet,
            "failure_basis": "none" if quiet else "contradicted",
            "failure_mode": "none" if quiet else theme,
            # Quoted from the unit's text: the user's words and the tool
            # result that shows the failure.
            "evidence": []
            if quiet
            else [
                {"quote": f"help unit={unit_id}", "supports": "summary"},
                {"quote": f'"theme": "{theme}"', "supports": "failure"},
            ],
        }

    def name(self, prompt):
        self._count("name")
        inside = prompt.split("Statements IN the cluster:", 1)[1].split(
            "Nearby statements", 1
        )[0]
        themes = [
            theme_of(line) for line in inside.splitlines() if line.startswith("- ")
        ]
        theme = max(set(themes), key=themes.count)
        return {
            "name": NAMES[theme],
            "description": f"Conversations where the {LENS_ANSWERS[theme]}.",
        }

    def organise(self, prompt):
        self._count("organise")
        groups = {"Order Lookups": [], "Policy And Escalation": []}
        for sub_id, name in re.findall(r'- id "([^"]+)": (.+?) \(\d+ items\)', prompt):
            theme = next(t for t, n in NAMES.items() if n == name)
            key = (
                "Order Lookups"
                if theme in ("order", "stock")
                else "Policy And Escalation"
            )
            groups[key].append(sub_id)
        return {
            "categories": [
                {"name": name, "description": "d", "children": children}
                for name, children in groups.items()
            ]
        }

    def classify(self, prompt):
        self._count("classify")
        listing = prompt.split("Categories:", 1)[1].split("Statements:", 1)[0]
        id_of = {
            name: sub_id
            for sub_id, name in re.findall(r'- "([^"]+)": (.+?): ', listing)
        }
        statements = prompt.split("Statements:", 1)[1].split("Return", 1)[0]
        assignments = {}
        for number, text in re.findall(
            r"^(\d+)\. (.+)$", statements, flags=re.MULTILINE
        ):
            # A theme the saved categories don't cover is "none".
            assignments[number] = id_of.get(NAMES.get(theme_of(text), ""), "none")
        return {"assignments": assignments}

    def summary(self):
        self._count("summary")
        return {
            "title": "Where does the agent fall short?",
            "findings": [
                {
                    "headline": "Order lookups loop instead of escalating (#1)",
                    "detail": "The agent asks again for what it was told (#2).",
                    "action": "Keep the order number in context",
                    "severity": "high",
                },
                {"headline": "Seen in 98765 conversations", "detail": "", "action": ""},
            ],
            "highlights": [{"ref": 1, "title": "Asked twice", "reason": "Typical"}],
        }


def fake_embeddings(texts):
    """Each theme its own direction, with a little noise."""
    rng = np.random.default_rng(0)
    vectors = []
    for text in texts:
        vector = np.zeros(len(THEMES))
        vector[THEMES.index(theme_of(text))] = 1.0
        vectors.append(vector + rng.normal(0, 0.01, len(THEMES)))
    return vectors


@pytest.fixture
def model(monkeypatch):
    from agentic_eval.core.embeddings import serving_client

    fake = FakeModel()
    monkeypatch.setattr(agent, "enabled", lambda: False)
    # The models are reachable: no licence to activate.
    monkeypatch.setattr(pipeline, "check_setup", lambda: None)
    for module in (facets, clustering, summary):
        monkeypatch.setattr(module, "chat_json", fake)
    monkeypatch.setattr(serving_client, "serving_available", lambda: True)
    monkeypatch.setattr("tracer.queries.eval_clustering.embed_texts", fake_embeddings)
    return fake


@pytest.fixture
def loaded(monkeypatch):
    """What ClickHouse holds for each unit id: "<theme>-<n>" reads as a
    conversation about that theme; "empty-<n>" has nothing to read."""
    calls = []

    def load_units(unit, project_id, unit_ids, since, until):
        calls.append(
            {"unit": unit, "ids": list(unit_ids), "since": since, "until": until}
        )
        texts = {}
        for index, unit_id in enumerate(unit_ids):
            theme = unit_id.rsplit("-", 1)[0]
            if theme == "empty":
                continue
            texts[unit_id] = UnitText(
                unit_id=unit_id,
                content=(
                    f"[Turn 1] User: help unit={unit_id} theme={theme}\n"
                    f'Tool lookup {{}} -> {{"theme": "{theme}"}}\nAssistant: ...'
                ),
                excerpt="help",
                label=f"Session {unit_id}",
                user_id=f"user-{index % 4}",
                session_id=unit_id,
                time=datetime(2026, 10, 2 + index % 5, 10, tzinfo=UTC),
                metrics={
                    "traces": 3,
                    "errors": 0,
                    "latency_ms": 1200,
                    "cost": 0.01,
                    "tokens": 300,
                    "turns": 3,
                },
            )
        return texts

    monkeypatch.setattr(pipeline, "load_units", load_units)
    return calls


@pytest.fixture
def report(observe_project, user):
    from tfc.middleware.workspace_context import clear_workspace_context

    config = InsightConfig.objects.create(
        project=observe_project,
        organization=observe_project.organization,
        workspace=observe_project.workspace,
        created_by=user,
        name="Where does the agent fall short?",
        unit="sessions",
        lens="failures",
        question="Where does the agent fall short?",
        sample_size=300,
    )
    # The stages run on the Temporal worker, outside any request's workspace.
    clear_workspace_context()
    return config


def ids(**counts):
    return [f"{theme}-{n}" for theme, count in counts.items() for n in range(count)]


def run_report(config, user, unit_ids, *, flaky=(), model=None):
    """Create a run over ``unit_ids`` and drive it through every stage, as
    InsightReportWorkflow does."""
    run = pipeline.create_run(
        config=config,
        scope={"window": WINDOW, "preset": "Custom"},
        population=[{"id": unit_id} for unit_id in unit_ids],
        sample_size=len(unit_ids),
        user=user,
    )
    if model is not None:
        model.flaky = set(flaky)
    ready = pipeline.prepare(str(run.id))
    pipeline.extract_batch(str(run.id), ready)
    retry = pipeline.retry_failed(str(run.id))
    if retry:
        pipeline.extract_batch(str(run.id), retry)
    pipeline.organise(str(run.id))
    pipeline.finish(str(run.id))
    run.refresh_from_db()
    return run, ready, retry


def units_of(run):
    return {u.unit_id: u for u in InsightUnit.objects.filter(run=run)}


@pytest.mark.django_db
def test_a_first_run_reads_groups_and_freezes_its_categories(
    report, user, model, loaded
):
    config = report
    unit_ids = ids(order=7, human=6, refund=6, stock=6, quiet=1, empty=1)
    run, ready, retry = run_report(
        config, user, unit_ids, flaky=["order-0"], model=model
    )

    # prepare: every unit read inside the run's window; one had no text.
    assert {i for call in loaded for i in call["ids"]} == set(unit_ids)
    assert loaded[0]["unit"] == "sessions"
    assert (loaded[0]["since"], loaded[0]["until"]) == (
        datetime(2026, 10, 1, tzinfo=UTC),
        datetime(2026, 10, 8, tzinfo=UTC),
    )
    units = units_of(run)
    assert units["empty-0"].status == "skipped" and not units["empty-0"].category_id
    assert len(ready) == len(unit_ids) - 1
    assert "unit=order-1" in units["order-1"].content
    assert units["order-1"].metrics["latency_ms"] == 1200

    # extract: facets per unit; the transient failure was retried once and read.
    assert retry == [units["order-0"].pk]
    assert units["order-0"].status == "done" and units["order-0"].error == ""
    assert units["order-1"].facets["failure_present"] is True
    assert units["order-1"].lens_answer == LENS_ANSWERS["order"]
    assert units["order-1"].topic == NAMES["order"]
    # A conversation with no failure is read but left out of the categories.
    assert units["quiet-0"].status == "done" and units["quiet-0"].included is False
    assert units["quiet-0"].category_id == ""

    # organise: four themes, grouped under the two top-level categories.
    taxonomy = run.result["taxonomy"]
    subs = {s["name"]: s for s in taxonomy["subs"]}
    tops = {t["name"]: t for t in taxonomy["tops"]}
    assert set(subs) == {NAMES[t] for t in ("order", "human", "refund", "stock")}
    assert set(tops) == {"Order Lookups", "Policy And Escalation"}
    assert subs[NAMES["stock"]]["parent_id"] == tops["Order Lookups"]["id"]
    assert subs[NAMES["refund"]]["parent_id"] == tops["Policy And Escalation"]["id"]
    assert run.result["clustering"]["mode"] == "discover"
    assert run.result["clustering"]["method"] == "minilm"
    for unit_id, unit in units.items():
        theme = unit_id.rsplit("-", 1)[0]
        if theme in ("quiet", "empty"):
            continue
        assert unit.sub_category_id == subs[NAMES[theme]]["id"], unit_id
        assert unit.category_id == subs[NAMES[theme]]["parent_id"], unit_id
    assert run.analysed_count == 25

    # finish: measured, written and labelled with the engine that wrote it.
    assert run.status == "completed" and run.stage == "done"
    counts = {c["name"]: c["stats"]["count"] for c in run.result["categories"]}
    assert counts[NAMES["order"]] == 7 and counts["Order Lookups"] == 13
    assert (run.result["excluded"], run.result["failed"], run.result["skipped"]) == (
        1,
        0,
        1,
    )
    written = run.result["summary"]
    assert [f["headline"] for f in written["findings"]] == [
        "Order lookups loop instead of escalating"
    ]
    assert written["dropped_points"] == 1
    # Each finding carries the quotes of the examples it cites.
    cited = {u.unit_id: u for u in units.values()}
    for quote in written["findings"][0]["evidence"]:
        assert quote["quote"] in cited[quote["unit_id"]].content
    assert written["findings"][0]["evidence"]
    assert written["verification"] == {"numbers": True, "quotes": True, "audit": False}
    assert run.result["engine"] == {"kind": "builtin", "version": "builtin/v1"}
    # Off Cloud nothing is charged: the report's model cost at list prices.
    assert run.result["cost"] == {"usd": round(run.cost_usd, 6)}

    # The first run's categories become the report's.
    config.refresh_from_db()
    assert config.taxonomy["source_run_id"] == str(run.id)
    assert config.taxonomy["subs"] == taxonomy["subs"]
    assert config.taxonomy["tops"] == taxonomy["tops"]


@pytest.mark.django_db
def test_later_runs_classify_into_the_frozen_categories(report, user, model, loaded):
    config = report
    first, _, _ = run_report(
        config, user, ids(order=6, human=6, refund=6, stock=6), model=model
    )
    config.refresh_from_db()
    frozen = config.taxonomy
    sub_ids = {s["name"]: s["id"] for s in frozen["subs"]}
    calls_before = dict(model.calls)

    # Enough conversations outside the saved categories become "Emerging".
    second, _, _ = run_report(
        config, user, ids(order=3, human=3, refund=3, stock=3, billing=6), model=model
    )
    assert second.taxonomy_mode == "classify"
    assert model.calls["classify"] > calls_before["classify"]
    # Classifying never re-organises the saved categories.
    assert model.calls["organise"] == calls_before["organise"]
    info = second.result["clustering"]
    assert (info["mode"], info["leftovers"]) == ("classify", 6)
    tops = {t["name"]: t for t in second.result["taxonomy"]["tops"]}
    emerging = tops["Emerging"]
    assert emerging["status"] == "emerging"
    assert {t["id"] for t in frozen["tops"]} < {t["id"] for t in tops.values()}
    new_subs = [
        s for s in second.result["taxonomy"]["subs"] if s["id"] not in sub_ids.values()
    ]
    assert [(s["name"], s["status"], s["parent_id"]) for s in new_subs] == [
        (NAMES["billing"], "emerging", emerging["id"])
    ]
    units = units_of(second)
    for unit_id, unit in units.items():
        theme = unit_id.rsplit("-", 1)[0]
        if theme == "billing":
            assert (unit.sub_category_id, unit.category_id) == (
                new_subs[0]["id"],
                emerging["id"],
            )
        else:
            assert unit.sub_category_id == sub_ids[NAMES[theme]], unit_id
    assert second.status == "completed"
    assert {c["name"] for c in second.result["categories"]} >= {
        "Emerging",
        NAMES["billing"],
    }

    # A few leftovers don't make a category: they "didn't fit".
    third, _, _ = run_report(
        config, user, ids(order=3, human=3, refund=3, stock=3, billing=2), model=model
    )
    info = third.result["clustering"]
    assert (info["mode"], info["leftovers"]) == ("classify", 2)
    tops = {t["id"]: t for t in third.result["taxonomy"]["tops"]}
    subs = {s["id"]: s for s in third.result["taxonomy"]["subs"]}
    assert tops["unmatched-top"]["name"] == "Other"
    assert subs["unmatched"]["parent_id"] == "unmatched-top"
    assert "Emerging" not in {t["name"] for t in tops.values()}
    units = units_of(third)
    assert {
        (u.sub_category_id, u.category_id)
        for i, u in units.items()
        if i.startswith("billing")
    } == {("unmatched", "unmatched-top")}
    assert units["order-0"].sub_category_id == sub_ids[NAMES["order"]]
    assert third.status == "completed"

    # Later runs never re-freeze: the report keeps its first run's categories.
    config.refresh_from_db()
    assert config.taxonomy == frozen
    assert frozen["source_run_id"] == str(first.id)
    assert InsightRun.objects.filter(config=config, status="completed").count() == 3


@pytest.mark.django_db
def test_a_v2_unit_keeps_its_conversation_to_show(report, user, monkeypatch):
    """The agent reads a v2 unit itself; what prepare keeps is for the
    example panel, so it is the unit's turns, not only its last answer."""
    turns = "\n\n".join(
        f"[Turn {n}] User: Is field {n} included?\nAssistant: Yes, field {n} is."
        for n in range(1, 31)
    )
    asked = []

    def load_units(
        unit, project_id, unit_ids, since, until, *, keep_empty=False, read_steps=True
    ):
        asked.append((keep_empty, read_steps))
        return {
            unit_ids[0]: UnitText(
                unit_id=unit_ids[0],
                content=turns,
                output="Yes, field 30 is.",
                time=datetime(2026, 10, 2, tzinfo=UTC),
            ),
            unit_ids[1]: UnitText(unit_id=unit_ids[1]),
        }

    monkeypatch.setattr(pipeline, "load_units", load_units)
    run = pipeline.create_run(
        config=report,
        scope={"window": WINDOW, "preset": "Custom"},
        population=[{"id": "long-0"}, {"id": "spans-only-0"}],
        sample_size=10,
        user=user,
    )
    run.settings = {
        **run.settings,
        "engine": {"kind": "omega", "contract": agent.CONTRACT_V2},
    }
    run.save(update_fields=["settings"])

    ready = pipeline.prepare(str(run.id))

    units = units_of(run)
    # Kept when empty, and the agent reads the steps itself.
    assert asked == [(True, False)] and len(ready) == 2
    assert units["long-0"].content == turns
    # A unit whose roots hold no text is still read, with nothing to show.
    assert units["spans-only-0"].status == "pending"
    assert units["spans-only-0"].content == ""


@pytest.mark.django_db
def test_a_retried_finish_does_not_write_or_charge_the_report_twice(
    report, user, model, loaded, monkeypatch
):
    """The run is saved completed before it is charged: when charging fails
    the activity retries finish, which settles the written report instead of
    writing it again and adding its model spend twice."""
    from ee.agenthub.insights import billing

    charged = []

    def charge_run(run):
        charged.append(run.id)
        if len(charged) == 1:
            raise RuntimeError("billing blip")
        return 12.5

    monkeypatch.setattr(billing, "charge_run", charge_run)
    monkeypatch.setattr(billing, "applies", lambda: True)
    run = pipeline.create_run(
        config=report,
        scope={"window": WINDOW, "preset": "Custom"},
        population=[{"id": i} for i in ids(order=6, human=6, refund=6, stock=6)],
        sample_size=24,
        user=user,
    )
    ready = pipeline.prepare(str(run.id))
    pipeline.extract_batch(str(run.id), ready)
    pipeline.organise(str(run.id))
    with pytest.raises(RuntimeError):
        pipeline.finish(str(run.id))
    run.refresh_from_db()
    cost, summaries, written = run.cost_usd, model.calls["summary"], run.result
    assert run.status == "completed"
    # Some of its spend was priced at a ceiling: the charge says so.
    run.settings = {**run.settings, "spend_flags": {"unpriced": True}}
    run.save(update_fields=["settings"])

    pipeline.finish(str(run.id))
    run.refresh_from_db()
    assert model.calls["summary"] == summaries
    assert run.cost_usd == cost
    # The report as written, with what it was charged once it was.
    assert "cost" not in written
    assert run.result == {**written, "cost": {"credits": 12.5, "unpriced": True}}
    assert charged == [run.id, run.id]
    assert run.stage == "done"
    report.refresh_from_db()
    assert report.taxonomy["source_run_id"] == str(run.id)


@pytest.mark.django_db
def test_a_rediscovered_run_is_not_compared_with_the_last_one(
    report, user, model, loaded
):
    """Discovery mints new category ids, so after "Rediscover categories"
    every category would read as new: the run carries no changes."""
    first, _, _ = run_report(
        report, user, ids(order=6, human=6, refund=6, stock=6), model=model
    )
    # The first run froze its categories on the saved report.
    report.refresh_from_db()
    second, _, _ = run_report(
        report, user, ids(order=6, human=6, refund=6, stock=6), model=model
    )
    assert second.taxonomy_mode == "classify"
    InsightConfig.objects.filter(id=report.id).update(taxonomy={})
    report.refresh_from_db()
    third, _, _ = run_report(
        report, user, ids(order=6, human=6, refund=6, stock=6), model=model
    )
    assert third.taxonomy_mode == "discover"
    assert "changes" not in third.result


def start_run(config, user, unit_ids):
    """Create a run over ``unit_ids`` and read it, up to organise."""
    run = pipeline.create_run(
        config=config,
        scope={"window": WINDOW, "preset": "Custom"},
        population=[{"id": unit_id} for unit_id in unit_ids],
        sample_size=len(unit_ids),
        user=user,
    )
    ready = pipeline.prepare(str(run.id))
    pipeline.extract_batch(str(run.id), ready)
    return run


@pytest.mark.django_db
def test_a_question_edited_mid_run_applies_from_the_next_run(
    report, user, model, loaded
):
    """A run sorts into the categories it started with. A new question resets
    the report's categories while the run is under way (as PATCH does): the
    run still classifies into its own and is compared like for like, never
    rediscovered and called all new; the next run discovers afresh."""
    themes = ids(order=6, human=6, refund=6, stock=6)
    first, _, _ = run_report(report, user, themes, model=model)
    report.refresh_from_db()
    frozen = report.taxonomy
    run = start_run(report, user, themes)
    InsightConfig.objects.filter(id=report.id).update(
        question="Why do customers give up?", taxonomy={}
    )
    pipeline.organise(str(run.id))
    pipeline.finish(str(run.id))
    run.refresh_from_db()

    assert run.taxonomy_mode == "classify"
    assert run.result["clustering"]["mode"] == "classify"
    assert {u.sub_category_id for u in units_of(run).values()} <= {
        s["id"] for s in frozen["subs"]
    }
    changes = run.result["changes"]
    assert changes["previous_run_id"] == str(first.id)
    assert changes["new_category_ids"] == []
    # The run's categories answer the old question: the report keeps none.
    report.refresh_from_db()
    assert report.taxonomy == {}
    following, _, _ = run_report(report, user, themes, model=model)
    assert following.taxonomy_mode == "discover"
    assert "changes" not in following.result


@pytest.mark.django_db
@pytest.mark.parametrize(
    "edit",
    [{"lens": "usage"}, {"question": "Why do customers give up?"}],
    ids=["lens", "question"],
)
def test_a_discovery_run_keeps_its_categories_off_a_report_asking_something_else(
    report, user, model, loaded, edit
):
    """The first run is under way when the lens or question changes. There
    are no saved categories for PATCH to reset, and the run's own answer the
    old question, so the report keeps none and the next run discovers."""
    run = start_run(report, user, ids(order=6, human=6, refund=6, stock=6))
    pipeline.organise(str(run.id))
    InsightConfig.objects.filter(id=report.id).update(**edit)
    pipeline.finish(str(run.id))
    run.refresh_from_db()

    assert (run.status, run.stage) == ("completed", "done")
    report.refresh_from_db()
    assert report.taxonomy == {}
    following = pipeline.create_run(
        config=report,
        scope={"window": WINDOW, "preset": "Custom"},
        population=[{"id": "order-0"}],
        sample_size=1,
        user=user,
    )
    assert following.taxonomy_mode == "discover"


@pytest.mark.django_db
def test_a_classify_run_without_categories_to_sort_into_discovers(
    report, user, model, loaded
):
    """A run created before runs kept their categories finds none in its
    settings: it discovers, and is marked so, so finish doesn't compare its
    new ids with the last run's."""
    themes = ids(order=6, human=6, refund=6, stock=6)
    run_report(report, user, themes, model=model)
    report.refresh_from_db()
    run = start_run(report, user, themes)
    settings = {k: v for k, v in run.settings.items() if k != "taxonomy"}
    InsightRun.objects.filter(id=run.id).update(settings=settings)
    pipeline.organise(str(run.id))
    pipeline.finish(str(run.id))
    run.refresh_from_db()

    assert run.taxonomy_mode == "discover"
    assert run.result["clustering"]["mode"] == "discover"
    assert "changes" not in run.result


@pytest.mark.django_db
def test_finish_never_completes_or_charges_a_run_failed_meanwhile(
    report, user, model, loaded, monkeypatch
):
    """A run marked failed while its report is written (lost, or a stage
    that gave up) stays failed: finish neither completes nor charges it, nor
    saves its categories, but what the writing spent is added to it."""
    from ee.agenthub.insights import billing

    charged = []
    monkeypatch.setattr(billing, "charge_run", lambda run: charged.append(run.id))
    run = start_run(report, user, ids(order=6, human=6, refund=6, stock=6))
    pipeline.organise(str(run.id))
    before = InsightRun.objects.get(id=run.id).cost_usd

    def write(run, result, units, contents, spend):
        InsightRun.objects.filter(id=run.id).update(status="failed", error="lost")
        spend.add(0.25)
        return {
            "title": "t",
            "findings": [],
            "key_points": [],
            "highlights": [],
            "examples": [],
        }

    monkeypatch.setattr(pipeline.report, "write", write)
    pipeline.finish(str(run.id))
    run.refresh_from_db()

    assert (run.status, run.error) == ("failed", "lost")
    assert "summary" not in run.result
    assert run.cost_usd == pytest.approx(before + 0.25)
    assert charged == []
    report.refresh_from_db()
    assert not (report.taxonomy or {}).get("subs")


@pytest.mark.django_db
def test_a_failed_run_is_never_started_or_read_again(report, user, model, loaded):
    """A run failed before its workflow got to it is not revived by
    prepare; one failed mid-way makes no more model calls; and the workflow
    failing it again keeps its first reason."""
    run = pipeline.create_run(
        config=report,
        scope={"window": WINDOW, "preset": "Custom"},
        population=[{"id": unit_id} for unit_id in ids(order=6)],
        sample_size=6,
        user=user,
    )
    pipeline.fail(str(run.id), "stopped")
    with pytest.raises(pipeline.InsightRunNotRunning):
        pipeline.prepare(str(run.id))
    run.refresh_from_db()
    assert (run.status, run.started_at) == ("failed", None)
    assert loaded == []
    pipeline.fail(str(run.id), str(pipeline.InsightRunNotRunning()))
    run.refresh_from_db()
    assert run.error == "stopped"

    second = pipeline.create_run(
        config=report,
        scope={"window": WINDOW, "preset": "Custom"},
        population=[{"id": unit_id} for unit_id in ids(human=6)],
        sample_size=6,
        user=user,
    )
    ready = pipeline.prepare(str(second.id))
    InsightRun.objects.filter(id=second.id).update(status="failed")
    calls = dict(model.calls)
    with pytest.raises(pipeline.InsightRunNotRunning):
        pipeline.extract_batch(str(second.id), ready)
    with pytest.raises(pipeline.InsightRunNotRunning):
        pipeline.organise(str(second.id))
    with pytest.raises(pipeline.InsightRunNotRunning):
        pipeline.finish(str(second.id))
    assert model.calls == calls


@pytest.mark.django_db
@pytest.mark.parametrize(
    "errors, expected",
    [
        # Every unit refused by the model gateway: retrying will not help.
        (["refused", "refused"], "refused"),
        # Any other failure among them may pass on a later try.
        (["refused", "timed out"], "could not read these items. Try again shortly."),
    ],
)
def test_a_report_the_model_refused_says_retrying_will_not_help(
    report, user, errors, expected
):
    from ee.agenthub.insights.llm import MODEL_REFUSED_MESSAGE, InsightModelError

    run = pipeline.create_run(
        config=report,
        scope={"window": WINDOW, "preset": "Custom"},
        population=[{"id": unit_id} for unit_id in ids(order=len(errors))],
        sample_size=len(errors),
        user=user,
    )
    InsightRun.objects.filter(id=run.id).update(status="running")
    for n, error in enumerate(errors):
        InsightUnit.objects.create(
            run=run,
            unit_id=f"u-{n}",
            status="failed",
            error=MODEL_REFUSED_MESSAGE if error == "refused" else error,
        )
    with pytest.raises(InsightModelError) as caught:
        pipeline.organise(str(run.id))
    if expected == "refused":
        assert str(caught.value) == MODEL_REFUSED_MESSAGE
        # Not offered again as is: the report page drops its Retry.
        assert (caught.value.code, caught.value.unavailable) == ("model_setup", False)
    else:
        assert str(caught.value).endswith(expected)
        assert caught.value.code == ""


def _setup_error():
    from ee.agenthub.insights.llm import LICENSE, InsightModelSetupError

    return InsightModelSetupError("Insights couldn't use the models.", LICENSE)


@pytest.mark.django_db
@pytest.mark.parametrize("workers", [1, pipeline.EXTRACT_WORKERS])
def test_a_setup_error_stops_the_run_after_the_first_unit(
    report, user, model, loaded, monkeypatch, workers
):
    """A licence the managed service won't activate (or any failure no call
    can get past) is not tried on every unit: the batch stops at the first
    one, keeps the rest pending, and the run fails with its message and
    code. It took 50 minutes of three tries per unit before."""
    calls = []

    def refuse(*args, **kwargs):
        calls.append(1)
        raise _setup_error()

    monkeypatch.setattr(pipeline, "EXTRACT_WORKERS", workers)
    monkeypatch.setattr(facets, "chat_json", refuse)
    run = pipeline.create_run(
        config=report,
        scope={"window": WINDOW, "preset": "Custom"},
        population=[{"id": unit_id} for unit_id in ids(order=15, human=15)],
        sample_size=30,
        user=user,
    )
    ready = pipeline.prepare(str(run.id))
    with pytest.raises(InsightModelError) as caught:
        pipeline.extract_batch(str(run.id), ready)
    # Only the calls already under way when the first one failed.
    assert 1 <= len(calls) <= workers
    statuses = set(InsightUnit.objects.filter(run=run).values_list("status", flat=True))
    assert statuses == {"pending"}
    pipeline.fail(str(run.id), str(caught.value), caught.value.code)
    run.refresh_from_db()
    assert (run.status, run.error) == ("failed", "Insights couldn't use the models.")
    assert run.progress["error_code"] == "license"


@pytest.mark.django_db
def test_a_licence_that_cannot_activate_fails_the_run_before_it_reads(
    report, user, model, loaded, monkeypatch
):
    from ee.agenthub.insights import llm
    from ee.licensing import activation_client

    monkeypatch.setattr(pipeline, "check_setup", llm.check_setup)
    monkeypatch.setattr(llm, "_managed", lambda model: True)
    monkeypatch.setattr(activation_client, "get_service_token", lambda: None)
    run = pipeline.create_run(
        config=report,
        scope={"window": WINDOW, "preset": "Custom"},
        population=[{"id": unit_id} for unit_id in ids(order=6)],
        sample_size=6,
        user=user,
    )
    with pytest.raises(llm.InsightModelSetupError) as caught:
        pipeline.prepare(str(run.id))
    assert caught.value.code == llm.LICENSE
    assert "Settings → License" in str(caught.value)
    assert loaded == []
    assert sum(model.calls.values()) == 0


@pytest.mark.django_db
def test_a_stopped_run_fails_once_and_reads_no_further_units(
    report, user, model, loaded, monkeypatch
):
    """Stop fails the run with who stopped it, at once; a batch under way
    starts no further unit; no later stage runs; stopping again, or the
    workflow failing it afterwards, changes nothing."""
    from django.core.cache import cache

    monkeypatch.setattr(pipeline, "EXTRACT_WORKERS", 1)
    run = pipeline.create_run(
        config=report,
        scope={"window": WINDOW, "preset": "Custom"},
        population=[{"id": unit_id} for unit_id in ids(order=6)],
        sample_size=6,
        user=user,
    )
    ready = pipeline.prepare(str(run.id))
    read = model.extract

    def extract_then_stop(prompt):
        # Stop arrives while the first unit is read: the flag a batch under
        # way reads (the pool's thread can't see this test's transaction, so
        # the run itself is stopped below).
        cache.set(pipeline.STOP_KEY.format(run.id), True)
        return read(prompt)

    monkeypatch.setattr(model, "extract", extract_then_stop)
    pipeline.extract_batch(str(run.id), ready)
    assert model.calls["extract"] == 1
    statuses = sorted(
        InsightUnit.objects.filter(run=run).values_list("status", flat=True)
    )
    assert statuses == ["done"] + ["pending"] * 5
    assert pipeline.stop(str(run.id), "Stopped by Ada.")
    run.refresh_from_db()
    assert (run.status, run.error) == ("failed", "Stopped by Ada.")
    assert run.progress["error_code"] == pipeline.STOPPED
    assert run.finished_at is not None

    assert not pipeline.stop(str(run.id), "Stopped by Bo.")
    pipeline.fail(str(run.id), "The report could not be completed.")
    with pytest.raises(pipeline.InsightRunNotRunning):
        pipeline.organise(str(run.id))
    run.refresh_from_db()
    assert (run.status, run.error) == ("failed", "Stopped by Ada.")


class InlinePool:
    """A ThreadPoolExecutor that reads each unit on this thread, so a stop
    made while a unit is read sees this test's transaction."""

    def __init__(self, max_workers=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def map(self, fn, items):
        return [fn(item) for item in items]


@pytest.mark.django_db
def test_a_stopped_run_is_charged_the_units_it_read_and_no_more(
    report, user, model, loaded, monkeypatch
):
    """Stopped during its second batch, after k = 3 units (two in the first
    batch, one in the second): the stop charges what the run had spent, the
    batch under way adds its one unit when it ends, and nothing else is
    charged, however often it is stopped. A run that fails on its own still
    isn't charged."""
    from ee.agenthub.insights import billing
    from ee.agenthub.insights.llm import Spend

    UNIT_COST = 0.01
    charged = []

    def charge(org_id, cost_usd, *, source, source_id, event_id=None, **_):
        charged.append((str(org_id), round(cost_usd, 6), source, source_id))
        return cost_usd

    monkeypatch.setattr(billing, "charge", charge)
    monkeypatch.setattr(pipeline, "ThreadPoolExecutor", InlinePool)
    run = pipeline.create_run(
        config=report,
        scope={"window": WINDOW, "preset": "Custom"},
        population=[{"id": unit_id} for unit_id in ids(order=6)],
        sample_size=6,
        user=user,
    )
    ready = pipeline.prepare(str(run.id))
    fake = facets.chat_json
    read = []

    def extract_and_pay(system, prompt, *, spend=None, **kwargs):
        reply = fake(system, prompt, **kwargs)
        spend.add(UNIT_COST)
        read.append(1)
        if len(read) == 3:
            assert pipeline.stop(str(run.id), "Stopped by Ada.")
        return reply

    monkeypatch.setattr(facets, "chat_json", extract_and_pay)
    pipeline.extract_batch(str(run.id), ready[:2])
    assert charged == []
    pipeline.extract_batch(str(run.id), ready[2:])

    assert len(read) == 3
    org, run_id = str(run.organization_id), str(run.id)
    assert charged == [
        (org, 0.02, "insights_report", run_id),
        (org, 0.01, "insights_report", run_id),
    ]
    assert not pipeline.stop(run_id, "Stopped by Bo.")
    with pytest.raises(pipeline.InsightRunNotRunning):
        pipeline.organise(run_id)
    assert len(charged) == 2
    run.refresh_from_db()
    assert run.cost_usd == pytest.approx(3 * UNIT_COST)

    # Failed on its own, with spend added after: not charged.
    other = pipeline.create_run(
        config=report,
        scope={"window": WINDOW, "preset": "Custom"},
        population=[{"id": unit_id} for unit_id in ids(human=2)],
        sample_size=2,
        user=user,
    )
    pipeline.fail(str(other.id), "The report could not be completed.")
    spend = Spend()
    spend.add(0.5)
    pipeline.add_spend(str(other.id), spend)
    assert len(charged) == 2


@pytest.mark.django_db
def test_a_batch_under_way_is_in_flight_until_its_units_are_saved(
    report, user, model, loaded, monkeypatch
):
    """Stopped while a batch reads: the run fails at once with that batch
    still in flight and none of its units saved yet; when the batch ends,
    the unit it had started is saved and the batch is no longer in flight,
    so the stopped run's counts are final from then on. A batch reaching a
    run already stopped reads nothing and is never in flight."""
    monkeypatch.setattr(pipeline, "ThreadPoolExecutor", InlinePool)
    run = pipeline.create_run(
        config=report,
        scope={"window": WINDOW, "preset": "Custom"},
        population=[{"id": unit_id} for unit_id in ids(order=4)],
        sample_size=4,
        user=user,
    )
    ready = pipeline.prepare(str(run.id))
    fake = facets.chat_json
    seen = []

    def stop_then_read(system, prompt, **kwargs):
        if not seen:
            assert pipeline.stop(str(run.id), "Stopped by Ada.")
            stopped = InsightRun.objects.get(id=run.id)
            seen.append(
                (
                    stopped.status,
                    list(stopped.progress.get(pipeline.IN_FLIGHT) or {}),
                    InsightUnit.objects.filter(run=run, status="done").count(),
                )
            )
        return fake(system, prompt, **kwargs)

    monkeypatch.setattr(facets, "chat_json", stop_then_read)
    pipeline.extract_batch(str(run.id), ready[:2])

    assert seen == [("failed", [str(min(ready[:2]))], 0)]
    run.refresh_from_db()
    assert run.progress[pipeline.IN_FLIGHT] == {}
    assert InsightUnit.objects.filter(run=run, status="done").count() == 1

    with pytest.raises(pipeline.InsightRunNotRunning):
        pipeline.extract_batch(str(run.id), ready[2:])
    run.refresh_from_db()
    assert run.progress[pipeline.IN_FLIGHT] == {}
    assert len(seen) == 1
