"""Insights report pipeline, run as Temporal activities.

prepare  -> load each sampled unit's text and metrics
extract  -> per-unit facets (fast model), in parallel batches
organise -> discover categories, or classify into the config's saved taxonomy
           as the run started with it (new themes in the leftovers become
           "emerging" categories)
finish   -> statistics, executive summary, freeze the taxonomy on first run

Each stage runs on the engine the run was pinned to when it was created
(``agent.pinned``). On an ``insights-agent/v2`` pin the agent reads each unit's
whole span graph through the evidence broker, so prepare keeps only a short
display excerpt of the unit, and extract builds each call's unit snapshots
into the run's evidence store before the agent is asked (a unit that can't be
built fails with the builder's code, never sent), then stores the agent's
facets, receipts and coverage (``facets["evidence"]``,
``facets["evidence_meta"]``, ``metrics["coverage"]``). The run's snapshots
are deleted when it finishes or fails.

A stage works only on a run still running: one failed meanwhile (lost, or a
stage that gave up) is never started again, read, completed or charged.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import structlog
from django.db import transaction
from django.db.models import DateTimeField, F, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from ee.agenthub.insights import agent as insight_agent
from ee.agenthub.insights import billing, clustering, report
from ee.agenthub.insights.agent import InsightEngineMismatch
from ee.agenthub.insights.facets import clean_attributes, extract, included, lens_for
from ee.agenthub.insights.llm import (
    FAST,
    MODEL_SETUP,
    THINKING,
    InsightModelError,
    InsightModelSetupError,
    Spend,
    check_setup,
    model_for,
)
from ee.agenthub.insights.stats import compare_runs, measure
from ee.agenthub.insights.units import load_units

logger = structlog.get_logger(__name__)

MAX_SAMPLE = 1000
EXTRACT_WORKERS = 8
# An insights-agent/v2 read-units call reads at most eight units; a batch's
# calls go a few at a time.
V2_UNITS_PER_CALL = 8
V2_PARALLEL_CALLS = 3
# How long finishing or failing a run waits for its snapshots to be deleted,
# however slow the store (the fail stage has 30 s in all); the store's sweep
# deletes whatever is left.
EVIDENCE_DELETE_S = 15.0
STAGES = [
    "queued",
    "reading",
    "summarising",
    "grouping",
    "measuring",
    "writing",
    "done",
]


class InsightRunNotRunning(InsightModelError):
    """A stage of a run that is no longer running (failed meanwhile): it
    does nothing, and the run keeps the reason it already has."""

    def __init__(self):
        super().__init__("This report stopped before it finished. Run it again.")


def _models():
    from tracer.models.insights import InsightRun, InsightUnit

    return InsightRun, InsightUnit


def _require_running(run) -> None:
    if run.status != "running":
        logger.warning("insight_stage_run_not_running", run_id=str(run.id))
        raise InsightRunNotRunning()


def _aware(value):
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def sample_evenly(population: list[dict], size: int) -> list[dict]:
    """Evenly spaced over time, so a busy hour can't crowd out the window."""
    ordered = sorted(population, key=lambda p: p.get("time") or "")
    if len(ordered) <= size:
        return ordered
    if size <= 1:
        return ordered[-1:] if size == 1 else []
    return [ordered[round(i * (len(ordered) - 1) / (size - 1))] for i in range(size)]


def sampling_method(
    population: list[dict], sampled: list[dict], *, drawn: bool, total: int
) -> str:
    """How a run's units were chosen, as "How this report was made" says it:
    "all" (every unit was read), "uniform" (the server's hash-ordered draw
    from a scope), "even_over_time" (rows handed over with their times) or
    "even" (rows handed over without times, spread through the selection)."""
    if len(sampled) >= total:
        return "all"
    if drawn:
        return "uniform"
    if all(entry.get("time") for entry in population):
        return "even_over_time"
    return "even"


def _log_spend(run_id, stage: str, spend: Spend) -> None:
    """What a stage's model calls cost, for calibrating billing's estimates."""
    logger.info(
        "insight_stage_spend",
        run_id=str(run_id),
        stage=stage,
        calls=spend.calls,
        cost_usd=round(spend.cost_usd, 6),
        prompt_tokens=spend.prompt_tokens,
        completion_tokens=spend.completion_tokens,
        image_parts=spend.image_parts,
        unpriced=spend.unpriced,
    )


def _flag_spend(run, spend: Spend) -> bool:
    """Mark the run when some of a step's spend was priced at the ceiling
    because nothing knew its model's price; True when ``run.settings``
    changed."""
    flags = dict((run.settings or {}).get("spend_flags") or {})
    if not spend.unpriced or flags.get("unpriced"):
        return False
    run.settings = {**(run.settings or {}), "spend_flags": {**flags, "unpriced": True}}
    return True


def add_spend(run_id, spend: Spend) -> None:
    """Add a step's model spend to its run (with its flags): what the agent
    reported for a call that failed is spend too."""
    InsightRun, _ = _models()
    if not (spend.cost_usd or spend.unpriced):
        return
    with transaction.atomic():
        run = (
            InsightRun.no_workspace_objects.select_for_update()
            .filter(id=run_id)
            .first()
        )
        if run is None:
            # Deleted meanwhile: nothing left to add it to.
            return
        run.cost_usd = (run.cost_usd or 0) + spend.cost_usd
        fields = ["cost_usd", "updated_at"]
        if _flag_spend(run, spend):
            fields.append("settings")
        run.save(update_fields=fields)
    if _stopped(run):
        _charge_after_stop(run, spend)


def set_progress(
    run, stage: str, done: int | None = None, total: int | None = None, **extra
):
    progress = dict(run.progress or {})
    progress.update(
        {
            "stage": stage,
            "stage_index": STAGES.index(stage),
            "updated_at": timezone.now().isoformat(),
        }
    )
    if done is not None:
        progress["done"] = done
    if total is not None:
        progress["total"] = total
    progress.update(extra)
    run.progress = progress
    run.stage = stage
    run.save(update_fields=["progress", "stage", "updated_at"])


def create_run(
    *,
    config,
    scope: dict,
    population: list[dict],
    sample_size: int,
    user,
    population_size: int | None = None,
    discover: bool = False,
    engine: dict | None = None,
):
    """Create a run with its sampled units; the workflow does the rest.

    ``population`` is every row handed over, or a sample the server already
    drew from the scope; then ``population_size`` is how many the scope held.
    The run reads each unit inside ``scope["window"]``. How the units were
    chosen is kept in ``settings["sampling"]`` (see ``sampling_method``).
    ``discover`` finds new categories even though the report has saved ones
    ("Rediscover categories"). ``engine`` is the engine the run is pinned to
    (``agent.resolve_engine``), asked of the agent when not given."""
    InsightRun, InsightUnit = _models()
    seen, unique = set(), []
    for entry in population:
        unit_id = str(entry.get("id") or "").strip()
        if unit_id and unit_id not in seen:
            seen.add(unit_id)
            unique.append(entry)
    size = max(1, min(MAX_SAMPLE, int(sample_size or config.sample_size)))
    sampled = sample_evenly(unique, size)
    total = len(unique) if population_size is None else population_size
    taxonomy = config.taxonomy or {}
    classify = bool(taxonomy.get("subs")) and not discover
    # Asked of the agent before anything is written: one that can't answer
    # refuses the run rather than start it on an engine nobody chose.
    engine = engine or insight_agent.resolve_engine()
    models = engine.get("models") if insight_agent.is_v2(engine) else None
    if not (isinstance(models, dict) and models.get("fast")):
        models = {"fast": model_for(FAST), "thinking": model_for(THINKING)}
    with transaction.atomic():
        run = InsightRun.objects.create(
            config=config,
            project=config.project,
            organization=config.organization,
            workspace=config.workspace,
            created_by=user,
            unit=config.unit,
            scope=scope or {},
            settings={
                "name": config.name,
                "lens": config.lens,
                "question": config.question,
                "agent_description": config.agent_description,
                "instructions": config.instructions,
                "attributes": clean_attributes(config.attributes),
                "sampling": sampling_method(
                    unique, sampled, drawn=population_size is not None, total=total
                ),
                # The saved categories a classify run sorts into, as they were
                # when it started: an edit that resets them (a new question or
                # lens) applies from the next run, not halfway through this one.
                **({"taxonomy": taxonomy} if classify else {}),
                # Every stage runs on this engine, whatever is configured later.
                "engine": engine,
            },
            population_size=total,
            sample_size=len(sampled),
            taxonomy_mode="classify" if classify else "discover",
            models_used={
                "fast": str(models.get("fast") or ""),
                "thinking": str(models.get("thinking") or ""),
            },
            progress={
                "stage": "queued",
                "stage_index": 0,
                "done": 0,
                "total": len(sampled),
            },
        )
        InsightUnit.objects.bulk_create(
            [
                InsightUnit(
                    run=run,
                    unit_id=str(entry["id"]),
                    user_id=str(entry.get("user_id") or "")[:255],
                    time=_parse_time(entry.get("time")),
                )
                for entry in sampled
            ]
        )
    return run


def _parse_time(value):
    if not value:
        return None
    try:
        return _aware(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        return None


def prepare(run_id: str) -> list[int]:
    """Load text and metrics; returns the unit pks ready for extraction."""
    InsightRun, InsightUnit = _models()
    now = timezone.now()
    # Started (or this stage retried) only while it is still to run: a run
    # failed before its workflow got here stays failed.
    started = InsightRun.objects.filter(
        id=run_id, status__in=["pending", "running"]
    ).update(
        status="running",
        started_at=Coalesce("started_at", Value(now, output_field=DateTimeField())),
        updated_at=now,
    )
    run = InsightRun.objects.get(id=run_id)
    if not started:
        _require_running(run)
    engine = insight_agent.pinned(run.settings)
    # A model setup no call can get through (a licence the managed service
    # won't activate) fails the run now, before anything is read. The agent
    # reads and writes on its own models, so this asks only the built-in
    # engine's; a call the agent's run still makes here is refused on its own.
    if not insight_agent.is_omega(engine):
        check_setup()
    set_progress(run, "reading", 0, run.sample_size)
    units = list(InsightUnit.objects.filter(run=run))
    window = (run.scope or {}).get("window") or []
    since = _parse_time(window[0]) if len(window) == 2 else None
    until = _parse_time(window[1]) if len(window) == 2 else None
    # A v2 unit is read whole by the agent: one whose root has no input or
    # output of its own may still hold everything in its spans.
    v2 = insight_agent.is_v2(engine)
    loaded = {}
    ids = [u.unit_id for u in units]
    for start in range(0, len(ids), 200):
        loaded.update(
            load_units(
                run.unit,
                str(run.project_id),
                ids[start : start + 200],
                since,
                until,
                **({"keep_empty": True, "read_steps": False} if v2 else {}),
            )
        )
    ready = []
    for unit in units:
        text = loaded.get(unit.unit_id)
        if text is None or not (v2 or text.content.strip()):
            unit.status = "skipped"
            unit.error = "No readable input or output."
        else:
            # A v2 unit's text is for the example panel only (its turns, as
            # the built-in engine reads them): the agent reads the unit itself.
            unit.content = text.content
            unit.excerpt = text.excerpt
            unit.label = text.label[:255]
            unit.user_id = (text.user_id or unit.user_id)[:255]
            unit.session_id = (text.session_id or "")[:255]
            unit.time = _aware(text.time) or unit.time
            unit.metrics = text.metrics
            ready.append(unit.pk)
    InsightUnit.objects.bulk_update(
        units,
        [
            "status",
            "error",
            "content",
            "excerpt",
            "label",
            "user_id",
            "session_id",
            "time",
            "metrics",
        ],
        batch_size=200,
    )
    set_progress(run, "summarising", 0, len(ready))
    return ready


# The run's batches being read now (``progress["in_flight"]``: the batch's
# first unit pk -> when it started). Stop ends the run at once, but the units
# a batch had already started are still read and saved: the page waits for
# them before it gives a stopped run's counts.
IN_FLIGHT = "in_flight"


def _note_in_flight(run_id, key: str, *, reading: bool) -> None:
    """Add a batch to the run's in-flight batches (only while the run is
    running: one stopped first reads nothing), or take it off. A retried
    batch has the same key, so a lost attempt's entry goes with it."""
    InsightRun, _ = _models()
    with transaction.atomic():
        run = (
            InsightRun.no_workspace_objects.select_for_update()
            .filter(id=run_id)
            .first()
        )
        if run is None or (reading and run.status != "running"):
            return
        progress = dict(run.progress or {})
        batches = dict(progress.get(IN_FLIGHT) or {})
        if reading:
            batches[key] = timezone.now().isoformat()
        elif batches.pop(key, None) is None:
            return
        progress[IN_FLIGHT] = batches
        run.progress = progress
        run.save(update_fields=["progress", "updated_at"])


def extract_batch(run_id: str, unit_pks: list[int]) -> dict:
    """Read a batch of units, listed in the run's in-flight batches until
    its units are saved."""
    if not unit_pks:
        return _extract_batch(run_id, unit_pks)
    key = str(min(unit_pks))
    _note_in_flight(run_id, key, reading=True)
    try:
        return _extract_batch(run_id, unit_pks)
    finally:
        _note_in_flight(run_id, key, reading=False)


def _extract_batch(run_id: str, unit_pks: list[int]) -> dict:
    InsightRun, InsightUnit = _models()
    run = InsightRun.objects.get(id=run_id)
    # Nothing is read, nor paid for, for a run that has stopped.
    _require_running(run)
    s = run.settings
    attributes = s.get("attributes") or []
    spend = Spend()
    units = list(InsightUnit.objects.filter(run=run, pk__in=unit_pks, status="pending"))
    stopped: list[InsightModelSetupError] = []

    def one(unit):
        # After a setup error no other call can succeed, and after Stop none
        # is wanted: the units not started yet stay pending.
        if stopped or stop_requested(run_id):
            return unit
        try:
            facets = extract(
                run.unit,
                unit.content,
                lens_key=s.get("lens", "usage"),
                question=s.get("question", ""),
                agent_description=s.get("agent_description", ""),
                instructions=s.get("instructions", ""),
                attributes=attributes,
                spend=spend,
                key=str(unit.pk),
            )
            _apply_facets(unit, facets, s.get("lens", "usage"), attributes)
        except InsightModelSetupError as exc:
            stopped.append(exc)
        except InsightModelError as exc:
            unit.status = "failed"
            unit.error = str(exc)
        return unit

    engine = insight_agent.pinned(s)
    error = None
    try:
        if insight_agent.is_v2(engine):
            finished, error = _read_with_agent_v2(run, units, attributes, spend, engine)
        elif insight_agent.is_omega(engine):
            finished = _read_with_agent(run, units, attributes, spend, engine)
        else:
            with ThreadPoolExecutor(max_workers=EXTRACT_WORKERS) as pool:
                finished = list(pool.map(one, units))
            error = stopped[0] if stopped else None
    except InsightModelError:
        add_spend(run_id, spend)
        raise
    InsightUnit.objects.bulk_update(
        finished,
        [
            "facets",
            "summary",
            "lens_answer",
            "topic",
            "included",
            "status",
            "error",
            "metrics",
        ],
        batch_size=100,
    )
    with transaction.atomic():
        locked = InsightRun.objects.select_for_update().get(id=run_id)
        done = InsightUnit.objects.filter(
            run_id=run_id, status__in=["done", "failed"]
        ).count()
        progress = dict(locked.progress or {})
        progress["done"] = done
        progress["updated_at"] = timezone.now().isoformat()
        recent = [u.summary for u in finished if u.summary][-6:]
        if recent:
            progress["recent"] = (progress.get("recent", []) + recent)[-8:]
        locked.progress = progress
        locked.cost_usd = (locked.cost_usd or 0) + spend.cost_usd
        fields = ["progress", "cost_usd", "updated_at"]
        if _flag_spend(locked, spend):
            fields.append("settings")
        locked.save(update_fields=fields)
    if _stopped(locked):
        _charge_after_stop(locked, spend)
    _log_spend(run_id, "read", spend)
    if error is not None:
        # What was read is kept; Temporal retries the units still pending,
        # or the run fails on an engine mismatch or a setup error.
        raise error
    return {
        "done": len(finished),
        "failed": sum(1 for u in finished if u.status == "failed"),
    }


def _apply_facets(unit, facets: dict, lens_key: str, attributes) -> None:
    unit.facets = facets
    unit.summary = facets["summary"]
    unit.lens_answer = facets["lens_answer"]
    unit.topic = facets["topic"]
    unit.included = included(facets, lens_key, attributes)
    unit.status = "done"


def _read_with_agent(run, units, attributes, spend, engine) -> list:
    """One agent call reads the batch; a unit it couldn't read fails alone.
    An unreachable agent raises, so Temporal retries the batch."""
    lens_key = run.settings.get("lens", "usage")
    context = insight_agent.run_context(run.settings, run.unit, run.id)
    results = insight_agent.read_units(
        context,
        [(str(u.pk), u.content) for u in units],
        attributes,
        spend,
        engine=engine,
    )
    for unit in units:
        result = results.get(str(unit.pk), {})
        if result.get("error"):
            unit.status = "failed"
            unit.error = result["error"]
            continue
        _apply_facets(unit, result["facets"], lens_key, attributes)
    return units


def _read_with_agent_v2(run, units, attributes, spend, engine):
    """The batch in calls of up to V2_UNITS_PER_CALL units, each through its
    own capability. Before each call its units' snapshots are built into the
    run's evidence store (``broker.prebuild``, a few at a time for the whole
    batch, the first call's first): a unit that can't be built fails with
    the builder's code (``metrics["evidence_error"]``) and is not sent, and a
    unit the agent couldn't read fails alone. Returns the units read and the
    first error of a call that failed (one that fails the run before one
    Temporal may retry): the units of that call stay pending."""
    from ee.agenthub.insights import broker, evidence_store

    lens_key = run.settings.get("lens", "usage")
    context = insight_agent.run_context(run.settings, run.unit, run.id)
    by_key = {str(unit.pk): unit for unit in units}
    keys = list(by_key)
    calls = [
        keys[start : start + V2_UNITS_PER_CALL]
        for start in range(0, len(keys), V2_UNITS_PER_CALL)
    ]

    def read(call, builders):
        built = broker.prebuild(
            run, [by_key[key] for key in call], stage="read", pool=builders
        )
        results = {
            key: {
                "error": f"This item's evidence could not be read ({code}).",
                "code": code,
            }
            for key, code in built.failed.items()
        }
        if built.ready:
            results.update(
                insight_agent.read_units_v2(
                    run, context, built.ready, attributes, spend, engine=engine
                )
            )
        return results

    finished, error = [], None
    # Swept here, on this stage's own thread: the calls' pre-builds share a
    # pool and don't.
    evidence_store.sweep()
    with (
        ThreadPoolExecutor(max_workers=broker.PREBUILD_WORKERS) as builders,
        ThreadPoolExecutor(max_workers=V2_PARALLEL_CALLS) as pool,
    ):
        futures = [pool.submit(read, call, builders) for call in calls]
        for call, future in zip(calls, futures, strict=True):
            try:
                results = future.result()
            except InsightModelError as exc:
                if error is None or (error.unavailable and not exc.unavailable):
                    error = exc
                continue
            for key in call:
                unit, result = by_key[key], results[key]
                if result.get("error"):
                    unit.status = "failed"
                    unit.error = result["error"]
                    if result.get("code"):
                        unit.metrics = {
                            **(unit.metrics or {}),
                            "evidence_error": result["code"],
                        }
                else:
                    _apply_facets(unit, result["facets"], lens_key, attributes)
                    unit.metrics = {
                        **(unit.metrics or {}),
                        "coverage": result["coverage"],
                    }
                finished.append(unit)
    return finished, error


def retry_failed(run_id: str) -> list[int]:
    """Put units that failed on a transient model error back in the queue,
    once, so a brief provider hiccup doesn't shrink the report."""
    _, InsightUnit = _models()
    failed = InsightUnit.objects.filter(run_id=run_id, status="failed")
    pks = list(failed.values_list("pk", flat=True))
    if pks:
        InsightUnit.objects.filter(pk__in=pks).update(status="pending", error="")
    return pks


def _context(run) -> str:
    s = run.settings
    lens = lens_for(s.get("lens", "usage"))
    question = s.get("question") or lens.title
    agent = s.get("agent_description") or "an AI application"
    return f"Agent: {agent}\nReport question: {question}"


def _item_text(unit) -> str:
    topic = unit.topic.strip()
    return f"{topic}. {unit.lens_answer}" if topic else unit.lens_answer


def _namer(run, spend, audit: dict):
    """The agent's namer on an agent run, else None (the built-in one).
    ``audit`` collects its audit of the taxonomy as a whole."""
    engine = insight_agent.pinned(run.settings)
    if not insight_agent.is_omega(engine):
        return None
    context = insight_agent.run_context(run.settings, run.unit, run.id)

    def name(clusters, target):
        reply = insight_agent.name_taxonomy(
            context, clusters, target, spend, engine=engine
        )
        audit.update(insight_agent.naming_audit(reply, engine))
        return reply

    return name


def organise(run_id: str) -> dict:
    InsightRun, InsightUnit = _models()
    run = InsightRun.objects.get(id=run_id)
    _require_running(run)
    set_progress(run, "grouping")
    spend = Spend()
    units = list(InsightUnit.objects.filter(run=run, status="done", included=True))
    failed_units = InsightUnit.objects.filter(run=run, status="failed")
    failed = failed_units.count()
    done = InsightUnit.objects.filter(run=run, status="done").count()
    if done == 0:
        from ee.agenthub.insights.llm import MODEL_REFUSED_MESSAGE

        if not failed:
            message = "Nothing could be read from these items."
        elif not failed_units.exclude(error=MODEL_REFUSED_MESSAGE).exists():
            raise InsightModelSetupError(MODEL_REFUSED_MESSAGE, MODEL_SETUP)
        else:
            message = (
                "The analysis model could not read these items. Try again shortly."
            )
        raise InsightModelError(message, unavailable=bool(failed))
    items = [clustering.Item(key=str(u.pk), text=_item_text(u)) for u in units]
    context = _context(run)
    audit: dict = {}
    try:
        tops, subs, parent_of, sub_of, info = _organise(
            run, items, context, spend, audit
        )
    except InsightModelError:
        add_spend(run_id, spend)
        raise
    if audit:
        info["naming_audit"] = audit
    by_pk = {str(u.pk): u for u in units}
    for key, unit in by_pk.items():
        sub_id = sub_of.get(key, "")
        unit.sub_category_id = sub_id
        unit.category_id = parent_of.get(sub_id, "") or ""
    InsightUnit.objects.bulk_update(
        units, ["category_id", "sub_category_id"], batch_size=200
    )
    result = dict(run.result or {})
    result["taxonomy"] = {"tops": tops, "subs": subs}
    result["clustering"] = info
    run.result = result
    run.analysed_count = len(units)
    run.save(update_fields=["result", "analysed_count", "taxonomy_mode", "updated_at"])
    # Under the run's lock, so a run stopped while it grouped is charged it.
    add_spend(run_id, spend)
    _log_spend(run_id, "organise", spend)
    return {"categories": len(tops), "subcategories": len(subs)}


def _organise(run, items, context, spend, audit):
    """(tops, subs, parent of each sub, sub of each item, info)."""
    info: dict = {}
    taxonomy = (run.settings or {}).get("taxonomy") or {}
    if run.taxonomy_mode == "classify" and taxonomy.get("subs"):
        assigned = clustering.classify(
            items, taxonomy["subs"], context=context, spend=spend
        )
        subs = [dict(s) for s in taxonomy["subs"]]
        tops = [dict(t) for t in taxonomy["tops"]]
        parent_of = {s["id"]: s["parent_id"] for s in subs}
        leftovers = [i for i in items if assigned.get(i.key) == clustering.NONE]
        sub_of = {k: v for k, v in assigned.items() if v != clustering.NONE}
        if len(leftovers) >= max(5, 0.08 * len(items)):
            new_tops, new_subs, info = clustering.discover(
                leftovers,
                context=context,
                spend=spend,
                namer=_namer(run, spend, audit),
            )
            emerging = {
                "id": clustering._new_id(),
                "name": "Emerging",
                "description": "Themes that did not fit the saved categories in this run.",
                "level": 1,
                "parent_id": None,
                "status": "emerging",
            }
            tops.append(emerging)
            for node in new_subs:
                node.parent_id = emerging["id"]
                node.status = "emerging"
                subs.append(node.as_dict())
                parent_of[node.id] = emerging["id"]
                for key in node.members:
                    sub_of[key] = node.id
        else:
            unmatched = {
                "id": "unmatched",
                "name": "Didn't fit a category",
                "description": "Items that matched none of the saved categories.",
                "level": 2,
                "parent_id": None,
                "status": "unmatched",
            }
            if leftovers:
                other_top = {
                    "id": "unmatched-top",
                    "name": "Other",
                    "description": "Items outside the saved categories.",
                    "level": 1,
                    "parent_id": None,
                    "status": "unmatched",
                }
                unmatched["parent_id"] = other_top["id"]
                tops.append(other_top)
                subs.append(unmatched)
                parent_of["unmatched"] = other_top["id"]
                for item in leftovers:
                    sub_of[item.key] = "unmatched"
        info.update({"mode": "classify", "leftovers": len(leftovers)})
    else:
        # A run with no categories to sort into (one from before runs kept
        # theirs) discovers, and says so: finish compares only a run that
        # classified with the last one.
        run.taxonomy_mode = "discover"
        top_nodes, sub_nodes, info = clustering.discover(
            items, context=context, spend=spend, namer=_namer(run, spend, audit)
        )
        tops = [t.as_dict() for t in top_nodes]
        subs = [s.as_dict() for s in sub_nodes]
        parent_of = {s.id: s.parent_id for s in sub_nodes}
        sub_of = {key: s.id for s in sub_nodes for key in s.members}
        info["mode"] = "discover"
    return tops, subs, parent_of, sub_of, info


def _unit_dict(unit) -> dict:
    return {
        "key": str(unit.pk),
        "unit_id": unit.unit_id,
        "user_id": unit.user_id,
        "session_id": unit.session_id,
        "time": unit.time.isoformat() if unit.time else "",
        "summary": unit.summary,
        "facets": unit.facets or {},
        "metrics": unit.metrics or {},
        "category_id": unit.category_id,
        "sub_category_id": unit.sub_category_id,
    }


def _previous_result(run) -> dict | None:
    """The last completed run of the same report before this one."""
    InsightRun, _ = _models()
    row = (
        InsightRun.objects.filter(
            config_id=run.config_id,
            status="completed",
            deleted=False,
            created_at__lt=run.created_at,
        )
        .order_by("-created_at")
        .values("id", "result", "finished_at")
        .first()
    )
    if not row or not (row["result"] or {}).get("categories"):
        return None
    return {
        "id": row["id"],
        "result": row["result"],
        "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
    }


def _asks(lens: str, question: str) -> tuple[str, str]:
    """What a report asks: its lens, and its question or the lens's own."""
    return lens, question or lens_for(lens).title


def _settle_written(run, taxonomy) -> None:
    """What follows a written report: charge it (idempotent per run), mark it
    done and, for a discovery run, save its categories on the report. Not
    when the report's question or lens changed while the run was under way:
    its categories answer the old one, and the edit applies from the next
    run."""
    charged = billing.charge_run(run)
    _record_cost(run, charged)
    set_progress(run, "done")
    config = run.config
    s = run.settings or {}
    if (
        run.taxonomy_mode == "discover"
        and not (config.taxonomy or {}).get("subs")
        and _asks(config.lens, config.question)
        == _asks(s.get("lens", "usage"), s.get("question"))
    ):
        config.taxonomy = {
            "tops": taxonomy["tops"],
            "subs": taxonomy["subs"],
            "source_run_id": str(run.id),
            "frozen_at": timezone.now().isoformat(),
        }
        config.save(update_fields=["taxonomy", "updated_at"])


def _record_cost(run, charged: float) -> None:
    """What the report itself cost, for "How this report was made": the
    credits it was charged where reports are billed (Cloud), else its model
    cost at list prices; ``unpriced`` when some of it is the ceiling price
    of a model nothing priced. Kept now: follow-up questions add to the
    run's ``cost_usd`` later."""
    InsightRun, _ = _models()
    if billing.applies():
        cost = {"credits": charged}
    else:
        cost = {"usd": round(float(run.cost_usd or 0), 6)}
    if ((run.settings or {}).get("spend_flags") or {}).get("unpriced"):
        cost["unpriced"] = True
    run.result = {**(run.result or {}), "cost": cost}
    InsightRun.objects.filter(id=run.id).update(result=run.result)


def finish(run_id: str) -> dict:
    InsightRun, InsightUnit = _models()
    run = InsightRun.objects.get(id=run_id)
    taxonomy = (run.result or {}).get("taxonomy", {"tops": [], "subs": []})
    if run.status == "completed" and "summary" in (run.result or {}):
        # A retry after the report was already written and saved (charging
        # or saving the categories failed): finish that, never write the
        # report again or add its model spend twice.
        _settle_written(run, taxonomy)
        return {"categories": len((run.result or {}).get("categories") or [])}
    _require_running(run)
    set_progress(run, "measuring")
    s = run.settings
    attributes = s.get("attributes") or []
    units = [
        _unit_dict(u)
        for u in InsightUnit.objects.filter(run=run, status="done", included=True)
    ]
    children = {t["id"]: [] for t in taxonomy["tops"]}
    for sub in taxonomy["subs"]:
        children.setdefault(sub.get("parent_id"), []).append(sub["id"])
    tops = [{**t, "children": children.get(t["id"], [])} for t in taxonomy["tops"]]
    measured = measure(units, tops, taxonomy["subs"], attributes)
    # Single-level reports (a top with one identical child) read as one level.
    for category in measured["categories"]:
        category["has_children"] = bool(category.get("children"))
    measured["categories"] = [
        c for c in measured["categories"] if c["stats"]["count"] > 0
    ]
    result = dict(run.result or {})
    result.update(measured)
    result["population_size"] = run.population_size
    result["sample_size"] = run.sample_size
    result["excluded"] = InsightUnit.objects.filter(
        run=run, status="done", included=False
    ).count()
    result["failed"] = InsightUnit.objects.filter(run=run, status="failed").count()
    result["skipped"] = InsightUnit.objects.filter(run=run, status="skipped").count()
    # Categories match across runs by id. A discovery run mints new ids (the
    # first run, or one after "Rediscover categories"), so comparing it with
    # the previous run would call every category new: only a run classified
    # into the saved categories is compared.
    previous = _previous_result(run) if run.taxonomy_mode == "classify" else None
    if previous:
        changes = compare_runs(result, previous["result"])
        if changes:
            changes.update(
                {
                    "previous_run_id": str(previous["id"]),
                    "previous_finished_at": previous["finished_at"],
                }
            )
            result["changes"] = changes
    set_progress(run, "writing")
    spend = Spend()
    contents = dict(
        InsightUnit.objects.filter(run=run, status="done", included=True).values_list(
            "pk", "content"
        )
    )
    contents = {str(k): v or "" for k, v in contents.items()}
    if not run.settings.get("question"):
        run.settings = {
            **run.settings,
            "question": lens_for(s.get("lens", "usage")).title,
        }
    try:
        summary = report.write(run, result, units, contents, spend)
        insights = summary.pop("category_insights", {}) or {}
        for category in result["categories"]:
            if category["id"] in insights:
                category["insight"] = insights[category["id"]]
        result["summary"] = summary
    except InsightEngineMismatch:
        # Not a report written another way: the run fails.
        add_spend(run_id, spend)
        raise
    except InsightModelError:
        result["summary"] = {
            "title": "",
            "findings": [],
            "key_points": [],
            "highlights": [],
            "examples": [],
            "error": "unavailable",
        }
    result["engine"] = insight_agent.pinned(run.settings)
    now = timezone.now()
    values = {
        "result": result,
        "cost_usd": F("cost_usd") + spend.cost_usd,
        "status": "completed",
        "finished_at": now,
        "updated_at": now,
    }
    if _flag_spend(run, spend):
        values["settings"] = run.settings
    # Completed only while it still runs: a run failed while its report was
    # being written stays failed and uncharged, with what the writing spent
    # added to it.
    completed = InsightRun.objects.filter(id=run_id, status="running").update(**values)
    _log_spend(run_id, "write", spend)
    _forget_evidence(run_id)
    if not completed:
        logger.warning("insight_finish_run_not_running", run_id=str(run_id))
        add_spend(run_id, spend)
        return {"categories": len(measured["categories"])}
    run.result = result
    run.refresh_from_db(fields=["cost_usd", "status", "finished_at"])
    # Charged once the report is written; a failed report isn't charged.
    _settle_written(run, taxonomy)
    return {"categories": len(measured["categories"])}


def _forget_evidence(run_id) -> None:
    """Delete the run's unit snapshots from its evidence store: the bucket's,
    which every web pod read, or this host's directory (the web pods that
    built their own drop them in their sweep). Returns within
    EVIDENCE_DELETE_S, so the fail stage stays inside its own limit and
    finishing does not wait on a store that does not answer: the sweep
    deletes the rest."""
    from ee.agenthub.insights import evidence_store

    try:
        evidence_store.delete_run(run_id, budget_s=EVIDENCE_DELETE_S)
    except Exception:
        logger.warning("insight_evidence_forget_failed", run_id=str(run_id))


def fail(run_id: str, message: str, code: str = "") -> None:
    """Mark the run failed, with ``code`` (``progress["error_code"]``) when
    only an administrator can fix it. A run that already failed keeps its
    first reason (a lost run's, a stop, or the stage that gave up first)."""
    InsightRun, _ = _models()
    now = timezone.now()
    with transaction.atomic():
        run = (
            InsightRun.no_workspace_objects.select_for_update()
            .filter(id=run_id)
            .exclude(status="failed")
            .first()
        )
        if run is not None:
            _end(run, message, code, now)
    _forget_evidence(run_id)


def _end(run, message: str, code: str, now) -> None:
    run.status = "failed"
    run.error = message[:2000]
    run.finished_at = now
    fields = ["status", "error", "finished_at", "updated_at"]
    if code:
        run.progress = {**(run.progress or {}), "error_code": code}
        fields.append("progress")
    run.save(update_fields=fields)


STOPPED = "stopped"
STOP_KEY = "insight-stop:{}"
# How long a stage in flight can still ask whether its run was stopped: the
# longest a run may last (tfc/temporal/insights/client.py RUN_TIMEOUT).
STOP_KEY_TTL_S = 2 * 3600


def stop(run_id, message: str) -> bool:
    """Stop a run still in flight: it fails with ``message`` and the code
    STOPPED, and a stage under way reads no further units. False when it
    had already ended. A stopped run is never completed, but the work it
    did is charged, as a written report's is: its spend so far here, under
    the run's lock, and the calls already under way as their stage adds
    them (``_charge_after_stop``). A run that fails on its own isn't."""
    from django.core.cache import cache

    InsightRun, _ = _models()
    with transaction.atomic():
        run = (
            InsightRun.no_workspace_objects.select_for_update()
            .filter(id=run_id, status__in=["pending", "running"])
            .first()
        )
        if run is None:
            return False
        _end(run, message, STOPPED, timezone.now())
    billing.charge_run(run)
    try:
        cache.set(STOP_KEY.format(run_id), True, timeout=STOP_KEY_TTL_S)
    except Exception:
        # Each stage still checks the run before it starts.
        logger.warning("insight_stop_flag_failed", run_id=str(run_id))
    return True


def _stopped(run) -> bool:
    return run.status == "failed" and (run.progress or {}).get("error_code") == STOPPED


def _charge_after_stop(run, spend: Spend) -> None:
    """Charge spend a stage added to its run after the run was stopped (the
    calls under way when it stopped), on its own: stop() charged what was
    there before."""
    billing.charge(
        run.organization_id,
        spend.cost_usd,
        source="insights_report",
        source_id=str(run.id),
        spend=spend,
    )


def stop_requested(run_id) -> bool:
    from django.core.cache import cache

    try:
        return bool(cache.get(STOP_KEY.format(run_id)))
    except Exception:
        return False
