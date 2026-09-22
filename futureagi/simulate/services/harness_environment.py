"""Read model for the environments surface.

An environment is, today, the job that built it: the world itself is a tarball in
object storage addressed only by ``payload.metadata.authoring_object_key``. This
module is the projection of a job into the row the environments list renders, so
the surface can exist before the environment becomes a table of its own.

Everything here is derived from columns and stage outputs already stored. Nothing
new is computed at read time, and no field is invented when its source is absent
-- an absent value is ``None`` rather than a plausible-looking zero.
"""

from __future__ import annotations

import json
from typing import Any

from django.db.models import Count, F, Prefetch, Q, QuerySet

from simulate.models import (
    HostedHarnessJob,
    HostedHarnessReceipt,
    HostedHarnessStageOutput,
)
from simulate.utils.ended_reason import canonical_ended_reasons

# The four states the list's status pill knows. The pipeline reports far finer
# stages; the raw one travels alongside as ``stage`` so a caller that wants
# progress can still derive it.
STATUS_BUILDING = "building"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

_TERMINAL_FAILED = frozenset(
    {HostedHarnessJob.State.FAILED, HostedHarnessJob.State.CANCELED}
)

# A run that is winding down, or being cancelled, is still a run from the
# outside; reporting it as "building" would say the environment is not ready.
_RUN_STATES = frozenset(
    {
        HostedHarnessJob.State.RUNNING,
        HostedHarnessJob.State.FINALIZING,
        HostedHarnessJob.State.CLEANING_UP,
    }
)

# Transports that carry audio. A source that serves both voice and HTTP lists
# both connectors, and voice wins: the recording is the thing that would be lost
# if the environment were rendered as chat.
_VOICE_CONNECTORS = frozenset({"livekit", "vapi", "retell"})

AGENT_TYPE_VOICE = "voice"
AGENT_TYPE_CHAT = "chat"

# The authoring snapshots a list row counts from. Prefetched together so a page
# of rows costs one extra query rather than one per row per kind.
_ROW_STAGE_KINDS = ("contract", "sub_goals")


def annotate_for_list(
    queryset: QuerySet[HostedHarnessJob],
) -> QuerySet[HostedHarnessJob]:
    """Annotate an already-scoped job queryset for the list, without N+1 reads.

    Tenancy is the caller's to apply, using the same helper the jobs list uses,
    so an environment can never be visible on one surface and absent from the
    other.
    """
    return (
        queryset.annotate(
            # An annotation walks the relation directly, so the soft-delete
            # filter the model's manager would apply has to be restated here.
            registered_scenarios=Count(
                "scenario_registrations",
                filter=Q(scenario_registrations__deleted=False),
                distinct=True,
            )
        )
        .prefetch_related(
            # Only the snapshots the row counts from are needed, so the
            # environment, store and scenario snapshots stay in the database.
            Prefetch(
                "normalized_stage_outputs",
                queryset=HostedHarnessStageOutput.no_workspace_objects.filter(
                    kind__in=_ROW_STAGE_KINDS, deleted=False
                ),
                to_attr="row_outputs",
            )
        )
        # Newest change first. The column is nullable for any row a backfill
        # could not date, and Postgres sorts nulls first on DESC, which would
        # float exactly the least-informative rows to the top of the list.
        .order_by(F("content_updated_at").desc(nulls_last=True), "-created_at")
    )


def environment_row(job: HostedHarnessJob) -> dict[str, Any]:
    """One list row. Safe to call on a job that has never finished building."""
    contract = _contract_data(job)
    return {
        "id": str(job.id),
        "name": environment_name(job),
        "description": _description(contract),
        "domain": _domain(job),
        "source_kind": _source_kind(job),
        "agent_type": agent_type(job, contract),
        "status": status_for(job),
        "stage": job.current_stage,
        "scenario_count": scenario_count(job),
        "sub_goals_count": _sub_goals_count(_row_stage_output(job, "sub_goals")),
        "tools_count": _tools_count(contract),
        "runs_count": 1 if job.test_execution_id else 0,
        "last_updated": _isoformat(job.content_updated_at or job.created_at),
        "created_at": _isoformat(job.created_at),
    }


def environment_name(job: HostedHarnessJob) -> str:
    """The name a person recognises, in the order the run detail already uses.

    A name set through the rename endpoint wins outright: it is the only one a
    person chose, while everything below it is inferred from the request.
    """
    renamed = str(job.name or "").strip()
    if renamed:
        return renamed[:255]
    payload = job.payload or {}
    metadata = payload.get("metadata") or {}
    for key in ("agent_name", "name"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value[:255]
    source = payload.get("source") or {}
    repository = str(source.get("repository") or "").strip()
    if repository:
        return repository.rsplit("/", 1)[-1] or repository
    return f"simulation-{str(job.id)[:8]}"


def status_for(job: HostedHarnessJob) -> str:
    """Collapse the lifecycle into the four states the list draws.

    Everything before the calls start is "building", because from the outside
    acquiring a source and seeding a world are the same activity: the
    environment is not ready yet.
    """
    if job.state == HostedHarnessJob.State.COMPLETED:
        return STATUS_COMPLETED
    if job.state in _TERMINAL_FAILED:
        return STATUS_FAILED
    if job.state in _RUN_STATES:
        return STATUS_RUNNING
    return STATUS_BUILDING


def agent_type(job: HostedHarnessJob, contract: dict[str, Any] | None = None) -> str:
    """Voice or chat, from the strongest evidence available.

    The user's explicit connector wins. Under ``auto`` it is genuinely unknown
    until authoring reports a modality, and chat is the safe default: a chat run
    rendered as chat merely looks plainer, whereas a voice run rendered as chat
    loses its recordings.
    """
    agent = (job.payload or {}).get("agent") or {}
    connector = str(agent.get("connector") or "").strip().lower()
    if connector in _VOICE_CONNECTORS:
        return AGENT_TYPE_VOICE
    if connector and connector != "auto":
        return AGENT_TYPE_CHAT
    if contract is None:
        contract = _contract_data(job)
    modality = str((contract or {}).get("modality") or "").strip().lower()
    return AGENT_TYPE_VOICE if modality == "voice" else AGENT_TYPE_CHAT


def scenario_count(job: HostedHarnessJob) -> int:
    """How many scenarios this environment has, not how many were asked for.

    Before authoring registers them the only number available is the request, so
    that is what is reported; afterwards the registrations are the truth, and
    they can differ when a suite is extended.
    """
    registered = getattr(job, "registered_scenarios", None)
    if registered is None:
        registered = job.scenario_registrations.filter(deleted=False).count()
    return int(registered or 0) or int(job.scenario_count or 0)


def _source_kind(job: HostedHarnessJob) -> str:
    source = (job.payload or {}).get("source") or {}
    return str(source.get("kind") or "") or "unknown"


def _domain(job: HostedHarnessJob) -> str | None:
    """The business area the submitter named, or ``None`` when they named none.

    Nothing infers it: a wrong domain on a list row is worse than a blank one,
    and neither the contract nor the source carries the answer.
    """
    metadata = (job.payload or {}).get("metadata") or {}
    return str(metadata.get("domain") or "").strip()[:255] or None


def _contract_data(job: HostedHarnessJob) -> dict[str, Any]:
    """The contract snapshot, preferring the verified copy over the live one."""
    data = _row_stage_output(job, "contract")
    return data if isinstance(data, dict) else {}


def _row_stage_output(job: HostedHarnessJob, kind: str) -> Any:
    """One authoring snapshot without touching the database.

    The list reads it from the prefetch the queryset set up; a job fetched on
    its own has no prefetch, so the live copy on the row is used instead. Either
    way no query is issued, which is what keeps a page of rows to one query.
    """
    for output in getattr(job, "row_outputs", None) or []:
        if output.kind == kind:
            return output.data
    for output in job.stage_outputs or []:
        if isinstance(output, dict) and output.get("kind") == kind:
            return output.get("data")
    return None


def _description(contract: dict[str, Any]) -> str | None:
    one_liner = str(contract.get("one_liner") or "").strip()
    return one_liner[:1000] or None


def _tools_count(contract: dict[str, Any]) -> int | None:
    tools = contract.get("tools")
    return len(tools) if isinstance(tools, list) else None


def _sub_goals_count(catalogue: Any) -> int | None:
    """How many sub-goals the catalogue holds, or ``None`` before it exists.

    The catalogue is written a stage after the contract, so a row can have tools
    and still have no sub-goals yet. That gap is reported as absent rather than
    as a zero, which would read as "this environment checks nothing".
    """
    entries = catalogue.get("sub_goals") if isinstance(catalogue, dict) else None
    if not isinstance(entries, list):
        return None
    return sum(1 for entry in entries if isinstance(entry, dict) and entry.get("name"))


def _isoformat(value) -> str | None:
    return value.isoformat() if value else None


_AUTHORING_STAGES = frozenset(
    {
        "acquiring_source",
        "understanding_agent",
        "generating_environment",
        "building_environment",
        "generating_data",
        "generating_scenarios",
        "validating_environment",
        "validating_scenarios",
    }
)

_CONTRACT_FIELDS = (
    "agent",
    "one_liner",
    "modality",
    "call_direction",
    "system_prompt_excerpt",
    "tools",
    "real_use_cases",
    "hard_constraints",
    "runtime",
    "dependencies",
    "runtime_dependencies",
    "implementation",
    "tool_entrypoints",
    "data_store",
    "open_questions",
    "amendments",
    "notes",
    "chosen_evals",
)

_WORLD_RUNTIME_FIELDS = (
    "services",
    "processes",
    "project",
    "managed",
    "overrides",
    "runtime",
    "capabilities",
    "seed",
    "readiness",
)

_SOURCE_FIELDS = (
    "kind",
    "repository",
    "ref",
    "commit_sha",
    "visibility",
    "archive_artifact_id",
    "endpoint",
)

_SETTINGS_FIELDS = ("runtime", "security", "artifacts", "scenario_count", "seed")

_PERSONA_COLUMNS = ("persona", "situation", "outcome")


def environment_detail(job: HostedHarnessJob) -> dict[str, Any]:
    """The whole environment, section by section, from what the job already holds.

    A section is ``None`` until the stage that produces it has finished, so a
    caller can tell "not built yet" from "built with nothing in it". Nothing is
    computed that ALK did not write; the contract is passed through whole.
    """
    contract = _contract_data(job)
    environment = _stage_output(job, "environment")
    scenario_docs = _stage_output(job, "scenarios")
    catalogue = _stage_output(job, "sub_goals")
    stores = _stage_output(job, "stores")
    registrations = list(
        job.scenario_registrations.filter(deleted=False)
        .select_related("scenario")
        .order_by("created_at")
    )
    receipts = _receipts(job)
    rows = _persona_rows(registrations)
    scenarios = _scenarios(registrations, scenario_docs, catalogue, receipts, rows)
    personas = _personas(scenarios)
    selected = _selected_evals(job)
    overview = environment_row(job)
    overview.update(
        {
            # The detail read already has the verified catalogue in hand, so the
            # count is restated from it rather than from the live copy the row
            # fell back to.
            "sub_goals_count": _sub_goals_count(catalogue),
            "flows_count": _count(contract.get("real_use_cases")),
            "guardrails_count": _count(contract.get("hard_constraints")),
            "personas_count": len(personas) if personas else None,
            "evaluations_count": len(selected),
            "run": _run_link(job),
        }
    )
    return {
        "id": str(job.id),
        "overview": overview,
        "contract": _contract_section(
            job, contract, environment, catalogue, scenario_docs
        ),
        "world": _world_section(environment, personas, stores),
        "scenarios": scenarios,
        "evaluations": {"selected": selected, "results": _results(receipts)},
        "settings": _settings(job),
    }


def _stage_output(job: HostedHarnessJob, kind: str) -> Any:
    """One authoring document: the verified snapshot, else the live copy, else None."""
    normalized = (
        HostedHarnessStageOutput.no_workspace_objects.filter(
            job=job, kind=kind, deleted=False
        )
        .values_list("data", flat=True)
        .first()
    )
    if normalized is not None:
        return normalized
    for output in job.stage_outputs or []:
        if isinstance(output, dict) and output.get("kind") == kind:
            return output.get("data")
    return None


def _count(value: Any) -> int | None:
    return len(value) if isinstance(value, list) else None


def _receipts(job: HostedHarnessJob) -> dict[str, HostedHarnessReceipt]:
    """The accepted receipt for each scenario registration, keyed by registration id."""
    return {
        str(receipt.scenario_id): receipt
        for receipt in HostedHarnessReceipt.no_workspace_objects.filter(
            job=job, deleted=False
        ).select_related("scenario")
    }


def _persona_rows(registrations: list) -> dict[str, dict[str, str]]:
    """The persona, situation and outcome cells behind each registration's row."""
    from model_hub.models.develop_dataset import Cell

    row_ids = [reg.dataset_row_id for reg in registrations if reg.dataset_row_id]
    if not row_ids:
        return {}
    cells: dict[str, dict[str, str]] = {}
    for cell in Cell.objects.filter(
        row_id__in=row_ids, column__name__in=_PERSONA_COLUMNS, deleted=False
    ).select_related("column"):
        cells.setdefault(str(cell.row_id), {})[cell.column.name] = cell.value or ""
    return cells


def _persona_of(cells: dict[str, str]) -> dict[str, Any] | None:
    raw = cells.get("persona")
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return {"name": raw}
    return value if isinstance(value, dict) else None


def _catalogue_index(catalogue: Any) -> dict[str, dict[str, Any]]:
    """Each sub-goal by name: what it checks and whether code or a judge settles it."""
    entries = catalogue.get("sub_goals") if isinstance(catalogue, dict) else None
    index: dict[str, dict[str, Any]] = {}
    for entry in entries or []:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        name = str(entry["name"])
        index[name] = {
            "name": name,
            "what": str(entry.get("what") or ""),
            "kind": "judge" if entry.get("judged") else "checkpoint",
            "claim": str(entry.get("judged") or ""),
        }
    return index


def _status_of(reg, receipt: HostedHarnessReceipt | None) -> str:
    if not reg.call_execution_id:
        return "registered"
    return receipt.status if receipt is not None else "running"


def _scenarios(
    registrations: list,
    docs: Any,
    catalogue: Any,
    receipts: dict[str, HostedHarnessReceipt],
    rows: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for doc in docs if isinstance(docs, list) else []:
        if isinstance(doc, dict):
            key = str(doc.get("scenario_key") or doc.get("name") or "")
            if key:
                by_key[key] = doc
    goals = _catalogue_index(catalogue)
    scenarios: list[dict[str, Any]] = []
    for reg in registrations:
        platform_name = getattr(reg.scenario, "name", "") or ""
        doc = by_key.get(reg.scenario_key) or by_key.get(platform_name) or {}
        cells = rows.get(str(reg.dataset_row_id), {}) if reg.dataset_row_id else {}
        fixture = doc.get("fixture")
        steps = doc.get("steps")
        scenarios.append(
            {
                "scenario_key": reg.scenario_key,
                "scenario_id": str(reg.scenario_id),
                "name": platform_name or doc.get("name") or reg.scenario_key,
                "instruction": getattr(reg.scenario, "prompt", None)
                or doc.get("instruction")
                or "",
                "use_case": doc.get("use_case")
                or getattr(reg.scenario, "use_case", None)
                or None,
                "branch": doc.get("branch") or None,
                "tests": doc.get("tests") or None,
                "fixture": fixture if isinstance(fixture, dict) else None,
                "steps": steps if isinstance(steps, int) else None,
                "sub_goals": [
                    goals.get(name, {"name": name})
                    for name in doc.get("sub_goals") or []
                    if isinstance(name, str)
                ],
                "persona": _persona_of(cells),
                "situation": cells.get("situation") or None,
                "outcome": cells.get("outcome") or None,
                "status": _status_of(reg, receipts.get(str(reg.id))),
                "call_execution_id": str(reg.call_execution_id)
                if reg.call_execution_id
                else None,
            }
        )
    return scenarios


def _personas(scenarios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Distinct callers across the suite, each with the scenarios it appears in."""
    seen: dict[str, dict[str, Any]] = {}
    for scenario in scenarios:
        persona = scenario.get("persona")
        if not persona:
            continue
        key = str(persona.get("name") or scenario["scenario_key"])
        entry = seen.setdefault(key, {**persona, "scenario_keys": []})
        entry["scenario_keys"].append(scenario["scenario_key"])
    return list(seen.values())


def eval_modality(job: HostedHarnessJob) -> str:
    """The modality the eval catalogue is filtered by: ``voice`` or ``text``.

    Which sources an eval can read is decided by what a run leaves behind, a
    recording or a transcript, so this follows the agent type rather than the
    connector, which for ``auto`` can still turn out to be either.
    """
    return "voice" if agent_type(job) == AGENT_TYPE_VOICE else "text"


def _selected_evals(job: HostedHarnessJob) -> list[dict[str, Any]]:
    """The platform evals bound to this environment's run test, in the order chosen."""
    from simulate.models.eval_config import SimulateEvalConfig

    if not job.run_test_id:
        return []
    return [
        {
            "id": str(config.id),
            "name": config.name or getattr(config.eval_template, "name", "") or "",
            "description": str(
                getattr(config.eval_template, "description", "") or ""
            )[:500],
            "runnable": bool(config.mapping),
        }
        for config in SimulateEvalConfig.objects.filter(
            run_test_id=job.run_test_id, deleted=False
        )
        .select_related("eval_template")
        .order_by("created_at")
    ]


def _results(receipts: dict[str, HostedHarnessReceipt]) -> list[dict[str, Any]]:
    """Per-scenario verdicts from the accepted receipts, in the platform eval shape."""
    from simulate.services.hosted_harness_ingestion import (
        _receipt_evaluation_coverage,
        _receipt_evaluations,
    )

    results = [
        {
            "scenario_key": receipt.scenario.scenario_key,
            "status": receipt.status,
            "attempt_number": receipt.attempt_number,
            "evaluations": _receipt_evaluations(receipt.body or {}),
            "coverage": _receipt_evaluation_coverage(receipt.body or {}),
        }
        for receipt in receipts.values()
    ]
    return sorted(results, key=lambda item: item["scenario_key"])


def _contract_section(
    job: HostedHarnessJob,
    contract: dict[str, Any],
    environment: Any,
    catalogue: Any,
    scenario_docs: Any,
) -> dict[str, Any] | None:
    if not contract:
        return None
    section = {field: contract.get(field) for field in _CONTRACT_FIELDS}
    section["amendments"] = _amendments(contract.get("amendments"))
    section["sub_goals"] = _catalogue_entries(catalogue)
    section["end_conditions"] = _end_conditions(job, contract, scenario_docs)
    section["provenance"] = _provenance(job, environment)
    return section


def _catalogue_entries(catalogue: Any) -> list[dict[str, Any]]:
    """The sub-goal catalogue with the code that settles each entry.

    The same entries the scenarios reference by name, carried whole here so the
    contract can show what a checkpoint actually asserts. ``check`` is the
    Python ALK wrote; a judged sub-goal has none and carries its claim instead.
    """
    entries = catalogue.get("sub_goals") if isinstance(catalogue, dict) else None
    goals: list[dict[str, Any]] = []
    for entry in entries or []:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        judged = str(entry.get("judged") or "")
        goals.append(
            {
                "name": str(entry["name"]),
                "what": str(entry.get("what") or ""),
                "kind": "judge" if judged else "checkpoint",
                "claim": judged,
                "check": str(entry.get("check") or ""),
            }
        )
    return goals


def _end_conditions(
    job: HostedHarnessJob, contract: dict[str, Any], scenario_docs: Any
) -> dict[str, Any]:
    """What stops a run, from the values that actually stop it.

    Only two limits truncate a run: a scenario's own turn budget and the job's
    wall-clock ceiling. Everything else is a way the conversation ended, which
    the platform records per call rather than deciding up front, so the reasons
    travel as the vocabulary a caller may see rather than as thresholds.
    """
    runtime = (job.payload or {}).get("runtime") or {}
    voice = agent_type(job, contract) == AGENT_TYPE_VOICE
    return {
        "max_turns": _max_turns(scenario_docs),
        "max_duration_seconds": runtime.get("max_duration_seconds"),
        "clock": "real-time" if voice else "stepped",
        "ended_reasons": canonical_ended_reasons(),
    }


def _max_turns(docs: Any) -> int | None:
    """The largest turn budget any scenario carries, or ``None`` before authoring.

    Scenarios may each set their own, so a single number can only honestly be
    the ceiling across the suite. The documents are passed in because the detail
    read has already loaded them; fetching them again would cost a second query
    for a number already in hand.
    """
    budgets = [
        doc["max_turns"]
        for doc in (docs if isinstance(docs, list) else [])
        if isinstance(doc, dict) and isinstance(doc.get("max_turns"), int)
    ]
    return max(budgets) if budgets else None


def _amendments(value: Any) -> list[dict[str, str]]:
    """ALK's amendment lines split into the subject changed and the reason why.

    ALK writes one sentence per amendment in a ``subject: reason`` shape
    (``tool removed - lookup_order: no runnable seam``). Splitting here rather
    than in the client keeps the reading of ALK's format in one place, and a
    line that does not carry a reason is kept whole instead of being dropped.
    """
    amendments: list[dict[str, str]] = []
    for entry in value or []:
        if not isinstance(entry, str) or not entry.strip():
            continue
        subject, separator, note = entry.partition(": ")
        if separator and subject.strip() and note.strip():
            amendments.append({"subject": subject.strip(), "note": note.strip()})
        else:
            amendments.append({"subject": "", "note": entry.strip()})
    return amendments


def _provenance(job: HostedHarnessJob, environment: Any) -> dict[str, Any]:
    """Where the contract was read from and what read it, from stored columns only."""
    source = (job.payload or {}).get("source") or {}
    attempt = job.attempts.order_by("-attempt_number").first()
    started, completed = _authoring_window(attempt)
    managed = environment.get("managed") if isinstance(environment, dict) else None
    return {
        "source": {
            field: source.get(field)
            for field in _SOURCE_FIELDS
            if source.get(field) is not None
        },
        "built_by": None if managed is None else ("alk" if managed else "repository"),
        "attempt": attempt.attempt_number if attempt else None,
        "snapshot": attempt.snapshot_name if attempt else None,
        "digests": {
            "source": attempt.source_digest if attempt else None,
            "bundle": (attempt.bundle_digest if attempt else None) or job.bundle_digest,
            "snapshot": attempt.snapshot_digest if attempt else None,
        },
        "authored_at": {"started": started, "completed": completed},
    }


def _authoring_window(attempt) -> tuple[str | None, str | None]:
    """When authoring started and when the first post-authoring stage began."""
    if attempt is None:
        return None, None
    started = completed = None
    for event in attempt.events.filter(accepted=True).order_by("sequence"):
        if started is None and event.stage in _AUTHORING_STAGES:
            started = event.emitted_at
        if event.event_type != "stage_changed" or started is None:
            continue
        target = (event.payload or {}).get("to")
        if target and target not in _AUTHORING_STAGES and completed is None:
            completed = event.emitted_at
    return _isoformat(started), _isoformat(completed)


def _world_section(
    environment: Any, personas: list[dict[str, Any]], stores: Any
) -> dict[str, Any] | None:
    if not isinstance(environment, dict) and not personas and not stores:
        return None
    runtime = (
        {
            field: environment.get(field)
            for field in _WORLD_RUNTIME_FIELDS
            if field in environment
        }
        if isinstance(environment, dict)
        else {}
    )
    return {
        "runtime": runtime,
        "personas": personas,
        "stores": stores if isinstance(stores, list) else [],
    }


def _run_link(job: HostedHarnessJob) -> dict[str, Any]:
    run_test_id = str(job.run_test_id) if job.run_test_id else None
    test_execution_id = str(job.test_execution_id) if job.test_execution_id else None
    return {
        "run_test_id": run_test_id,
        "test_execution_id": test_execution_id,
        "simulation_url": (
            f"/dashboard/simulate/test/{run_test_id}/{test_execution_id}/call-details"
            if run_test_id and test_execution_id
            else None
        ),
    }


def _settings(job: HostedHarnessJob) -> dict[str, Any]:
    """The request the environment was built from, with every secret value removed."""
    from simulate.services.harness_credentials import is_credential_file_ref
    from simulate.services.hosted_harness_gateway import _secret_safe

    payload = job.payload or {}
    agent = payload.get("agent") or {}
    source = payload.get("source") or {}
    settings = {field: payload.get(field) for field in _SETTINGS_FIELDS}
    settings["schema_version"] = payload.get("schema_version")
    settings["source"] = {
        field: source.get(field)
        for field in _SOURCE_FIELDS
        if source.get(field) is not None
    }
    refs = agent.get("secret_refs") or {}
    settings["agent"] = {
        "connector": agent.get("connector"),
        "mode": agent.get("mode"),
        "call_direction": agent.get("call_direction"),
        "config": _secret_safe(agent.get("config") or {}),
        "secret_refs": sorted(str(name) for name in refs),
        "secrets": sorted(
            str(name) for name, ref in refs.items() if not is_credential_file_ref(ref)
        ),
        "credential_files": [
            {"environment_name": str(name)}
            for name in sorted(
                name for name, ref in refs.items() if is_credential_file_ref(ref)
            )
        ],
    }
    return settings
