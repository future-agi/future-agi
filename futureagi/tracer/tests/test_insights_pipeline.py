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
            "failure_mode": "none" if quiet else theme,
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
                content=f"[Turn 1] User: help unit={unit_id} theme={theme}\nAssistant: ...",
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
    assert written["verification"] == {"numbers": True, "quotes": False, "audit": False}
    assert run.result["engine"] == {"kind": "builtin", "version": "builtin/v1"}

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

    monkeypatch.setattr(billing, "charge_run", charge_run)
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

    pipeline.finish(str(run.id))
    run.refresh_from_db()
    assert model.calls["summary"] == summaries
    assert run.cost_usd == cost
    assert run.result == written
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
