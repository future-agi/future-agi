"""Insights report pipeline, run as Temporal activities.

prepare  -> load each sampled unit's text and metrics
extract  -> per-unit facets (fast model), in parallel batches
organise -> discover categories, or classify into the config's saved taxonomy
           (new themes in the leftovers become "emerging" categories)
finish   -> statistics, executive summary, freeze the taxonomy on first run
"""

from __future__ import annotations

import random
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import structlog
from django.db import transaction
from django.utils import timezone

from ee.agenthub.insights import agent as insight_agent
from ee.agenthub.insights import clustering, report
from ee.agenthub.insights.facets import clean_attributes, extract, included, lens_for
from ee.agenthub.insights.llm import FAST, THINKING, InsightModelError, Spend, model_for
from ee.agenthub.insights.stats import compare_runs, measure
from ee.agenthub.insights.units import load_units

logger = structlog.get_logger(__name__)

MAX_SAMPLE = 1000
EXTRACT_BATCH = 24
EXTRACT_WORKERS = 8
STAGES = ["queued", "reading", "summarising", "grouping", "measuring", "writing", "done"]


def _models():
    from tracer.models.insights import InsightRun, InsightUnit

    return InsightRun, InsightUnit


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


def set_progress(run, stage: str, done: int | None = None, total: int | None = None, **extra):
    progress = dict(run.progress or {})
    progress.update({"stage": stage, "stage_index": STAGES.index(stage)})
    if done is not None:
        progress["done"] = done
    if total is not None:
        progress["total"] = total
    progress.update(extra)
    run.progress = progress
    run.stage = stage
    run.save(update_fields=["progress", "stage", "updated_at"])


def create_run(*, config, scope: dict, population: list[dict], sample_size: int, user):
    """Create a run with its sampled units; the workflow does the rest."""
    InsightRun, InsightUnit = _models()
    seen, unique = set(), []
    for entry in population:
        unit_id = str(entry.get("id") or "").strip()
        if unit_id and unit_id not in seen:
            seen.add(unit_id)
            unique.append(entry)
    size = max(1, min(MAX_SAMPLE, int(sample_size or config.sample_size)))
    sampled = sample_evenly(unique, size)
    taxonomy = config.taxonomy or {}
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
            },
            population_size=len(unique),
            sample_size=len(sampled),
            taxonomy_mode="classify" if taxonomy.get("subs") else "discover",
            models_used={"fast": model_for(FAST), "thinking": model_for(THINKING)},
            progress={"stage": "queued", "stage_index": 0, "done": 0, "total": len(sampled)},
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
    run = InsightRun.objects.get(id=run_id)
    run.status = "running"
    run.started_at = run.started_at or timezone.now()
    run.save(update_fields=["status", "started_at", "updated_at"])
    set_progress(run, "reading", 0, run.sample_size)
    units = list(InsightUnit.objects.filter(run=run))
    window = (run.scope or {}).get("window") or []
    since = _parse_time(window[0]) if len(window) == 2 else None
    until = _parse_time(window[1]) if len(window) == 2 else None
    loaded = {}
    ids = [u.unit_id for u in units]
    for start in range(0, len(ids), 200):
        loaded.update(
            load_units(run.unit, str(run.project_id), ids[start : start + 200], since, until)
        )
    ready = []
    for unit in units:
        text = loaded.get(unit.unit_id)
        if text is None or not text.content.strip():
            unit.status = "skipped"
            unit.error = "No readable input or output."
        else:
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
        ["status", "error", "content", "excerpt", "label", "user_id", "session_id", "time", "metrics"],
        batch_size=200,
    )
    set_progress(run, "summarising", 0, len(ready))
    return ready


def extract_batch(run_id: str, unit_pks: list[int]) -> dict:
    InsightRun, InsightUnit = _models()
    run = InsightRun.objects.get(id=run_id)
    s = run.settings
    attributes = s.get("attributes") or []
    spend = Spend()
    units = list(InsightUnit.objects.filter(run=run, pk__in=unit_pks, status="pending"))

    def one(unit):
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
            )
            unit.facets = facets
            unit.summary = facets["summary"]
            unit.lens_answer = facets["lens_answer"]
            unit.topic = facets["topic"]
            unit.included = included(facets, s.get("lens", "usage"), attributes)
            unit.status = "done"
        except InsightModelError as exc:
            unit.status = "failed"
            unit.error = str(exc)
        return unit

    if insight_agent.enabled():
        finished = _read_with_agent(run, units, attributes, spend)
    else:
        with ThreadPoolExecutor(max_workers=EXTRACT_WORKERS) as pool:
            finished = list(pool.map(one, units))
    InsightUnit.objects.bulk_update(
        finished,
        ["facets", "summary", "lens_answer", "topic", "included", "status", "error"],
        batch_size=100,
    )
    with transaction.atomic():
        locked = InsightRun.objects.select_for_update().get(id=run_id)
        done = InsightUnit.objects.filter(run_id=run_id, status__in=["done", "failed"]).count()
        progress = dict(locked.progress or {})
        progress["done"] = done
        recent = [u.summary for u in finished if u.summary][-6:]
        if recent:
            progress["recent"] = (progress.get("recent", []) + recent)[-8:]
        locked.progress = progress
        locked.cost_usd = (locked.cost_usd or 0) + spend.cost_usd
        locked.save(update_fields=["progress", "cost_usd", "updated_at"])
    return {"done": len(finished), "failed": sum(1 for u in finished if u.status == "failed")}


def _read_with_agent(run, units, attributes, spend) -> list:
    """One agent call reads the batch; a unit it couldn't read fails alone.
    An unreachable agent raises, so Temporal retries the batch."""
    lens_key = run.settings.get("lens", "usage")
    context = insight_agent.run_context(run.settings, run.unit, run.id)
    results = insight_agent.read_units(
        context, [(str(u.pk), u.content) for u in units], attributes, spend
    )
    for unit in units:
        result = results.get(str(unit.pk), {})
        if result.get("error"):
            unit.status = "failed"
            unit.error = result["error"]
            continue
        facets = result["facets"]
        unit.facets = facets
        unit.summary = facets["summary"]
        unit.lens_answer = facets["lens_answer"]
        unit.topic = facets["topic"]
        unit.included = included(facets, lens_key, attributes)
        unit.status = "done"
    return units


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


def _namer(run, spend):
    if not insight_agent.enabled():
        return None
    context = insight_agent.run_context(run.settings, run.unit, run.id)

    def name(clusters, target):
        return insight_agent.name_taxonomy(context, clusters, target, spend)

    return name


def organise(run_id: str) -> dict:
    InsightRun, InsightUnit = _models()
    run = InsightRun.objects.get(id=run_id)
    set_progress(run, "grouping")
    spend = Spend()
    units = list(InsightUnit.objects.filter(run=run, status="done", included=True))
    failed = InsightUnit.objects.filter(run=run, status="failed").count()
    done = InsightUnit.objects.filter(run=run, status="done").count()
    if done == 0:
        raise InsightModelError(
            "Nothing could be read from these items." if not failed
            else "The analysis model could not read these items. Try again shortly.",
            unavailable=bool(failed),
        )
    items = [clustering.Item(key=str(u.pk), text=_item_text(u)) for u in units]
    context = _context(run)
    info: dict = {}
    taxonomy = run.config.taxonomy or {}
    if run.taxonomy_mode == "classify" and taxonomy.get("subs"):
        assigned = clustering.classify(items, taxonomy["subs"], context=context, spend=spend)
        subs = [dict(s) for s in taxonomy["subs"]]
        tops = [dict(t) for t in taxonomy["tops"]]
        parent_of = {s["id"]: s["parent_id"] for s in subs}
        leftovers = [i for i in items if assigned.get(i.key) == clustering.NONE]
        sub_of = {k: v for k, v in assigned.items() if v != clustering.NONE}
        if len(leftovers) >= max(5, 0.08 * len(items)):
            new_tops, new_subs, info = clustering.discover(
                leftovers, context=context, spend=spend, namer=_namer(run, spend)
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
        top_nodes, sub_nodes, info = clustering.discover(
            items, context=context, spend=spend, namer=_namer(run, spend)
        )
        tops = [t.as_dict() for t in top_nodes]
        subs = [s.as_dict() for s in sub_nodes]
        parent_of = {s.id: s.parent_id for s in sub_nodes}
        sub_of = {key: s.id for s in sub_nodes for key in s.members}
        info["mode"] = "discover"
    by_pk = {str(u.pk): u for u in units}
    for key, unit in by_pk.items():
        sub_id = sub_of.get(key, "")
        unit.sub_category_id = sub_id
        unit.category_id = parent_of.get(sub_id, "") or ""
    InsightUnit.objects.bulk_update(units, ["category_id", "sub_category_id"], batch_size=200)
    result = dict(run.result or {})
    result["taxonomy"] = {"tops": tops, "subs": subs}
    result["clustering"] = info
    run.result = result
    run.cost_usd = (run.cost_usd or 0) + spend.cost_usd
    run.analysed_count = len(units)
    run.save(update_fields=["result", "cost_usd", "analysed_count", "updated_at"])
    return {"categories": len(tops), "subcategories": len(subs)}


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


def finish(run_id: str) -> dict:
    InsightRun, InsightUnit = _models()
    run = InsightRun.objects.get(id=run_id)
    set_progress(run, "measuring")
    s = run.settings
    attributes = s.get("attributes") or []
    taxonomy = (run.result or {}).get("taxonomy", {"tops": [], "subs": []})
    units = [_unit_dict(u) for u in InsightUnit.objects.filter(run=run, status="done", included=True)]
    children = {t["id"]: [] for t in taxonomy["tops"]}
    for sub in taxonomy["subs"]:
        children.setdefault(sub.get("parent_id"), []).append(sub["id"])
    tops = [{**t, "children": children.get(t["id"], [])} for t in taxonomy["tops"]]
    measured = measure(units, tops, taxonomy["subs"], attributes)
    # Single-level reports (a top with one identical child) read as one level.
    for category in measured["categories"]:
        category["has_children"] = bool(category.get("children"))
    measured["categories"] = [c for c in measured["categories"] if c["stats"]["count"] > 0]
    result = dict(run.result or {})
    result.update(measured)
    result["population_size"] = run.population_size
    result["sample_size"] = run.sample_size
    result["excluded"] = InsightUnit.objects.filter(run=run, status="done", included=False).count()
    result["failed"] = InsightUnit.objects.filter(run=run, status="failed").count()
    result["skipped"] = InsightUnit.objects.filter(run=run, status="skipped").count()
    previous = _previous_result(run)
    if previous:
        changes = compare_runs(result, previous["result"])
        if changes:
            changes.update({
                "previous_run_id": str(previous["id"]),
                "previous_finished_at": previous["finished_at"],
            })
            result["changes"] = changes
    set_progress(run, "writing")
    spend = Spend()
    contents = dict(
        InsightUnit.objects.filter(run=run, status="done", included=True)
        .values_list("pk", "content")
    )
    contents = {str(k): v or "" for k, v in contents.items()}
    if not run.settings.get("question"):
        run.settings = {**run.settings, "question": lens_for(s.get("lens", "usage")).title}
    try:
        summary = report.write(run, result, units, contents, spend)
        insights = summary.pop("category_insights", {}) or {}
        for category in result["categories"]:
            if category["id"] in insights:
                category["insight"] = insights[category["id"]]
        result["summary"] = summary
    except InsightModelError:
        result["summary"] = {
            "title": "", "findings": [], "key_points": [], "highlights": [], "examples": [],
            "error": "unavailable",
        }
    result["engine"] = insight_agent.engine()
    run.result = result
    run.cost_usd = (run.cost_usd or 0) + spend.cost_usd
    run.status = "completed"
    run.finished_at = timezone.now()
    run.save(update_fields=["result", "cost_usd", "status", "finished_at", "updated_at"])
    set_progress(run, "done")
    config = run.config
    if run.taxonomy_mode == "discover" and not (config.taxonomy or {}).get("subs"):
        config.taxonomy = {
            "tops": taxonomy["tops"],
            "subs": taxonomy["subs"],
            "source_run_id": str(run.id),
            "frozen_at": timezone.now().isoformat(),
        }
        config.save(update_fields=["taxonomy", "updated_at"])
    return {"categories": len(measured["categories"])}


def fail(run_id: str, message: str) -> None:
    InsightRun, _ = _models()
    run = InsightRun.objects.filter(id=run_id).first()
    if run is None:
        return
    run.status = "failed"
    run.error = message[:2000]
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "error", "finished_at", "updated_at"])


def run_inline(run_id: str) -> None:
    """Run every stage in-process (tests and environments without Temporal)."""
    try:
        ready = prepare(run_id)
        for start in range(0, len(ready), EXTRACT_BATCH):
            extract_batch(run_id, ready[start : start + EXTRACT_BATCH])
        retry = retry_failed(run_id)
        for start in range(0, len(retry), EXTRACT_BATCH):
            extract_batch(run_id, retry[start : start + EXTRACT_BATCH])
        organise(run_id)
        finish(run_id)
    except Exception as exc:  # pragma: no cover - surfaced on the run
        logger.exception("insight_run_failed", run_id=run_id)
        fail(run_id, str(exc) if isinstance(exc, InsightModelError) else "The report could not be completed.")
        raise


def random_preview_sample(population: list[dict], n: int = 3) -> list[dict]:
    rng = random.Random(7)
    return rng.sample(population, min(n, len(population)))
