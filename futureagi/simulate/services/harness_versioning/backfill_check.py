"""``harness_versions_backfill --check``: what the backfill would do, and what would go wrong.

Database reads run in transactions that are rolled back, and read-only on PostgreSQL, so an
accidental write fails instead of landing. No transaction is open while the archive is
fetched. Storage is only read. Results carry ids, counts and codes, never scenario text,
prompts or secret values.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

from django.db import connection, transaction

from simulate.models.harness_versioning import HashKind, VersionStatus
from simulate.models.hosted_harness import MAX_SCENARIOS_PER_JOB
from simulate.services.harness_versioning import archives
from simulate.services.harness_versioning.assembly import certifies_bundle
from simulate.services.harness_versioning.backfill import (
    READ_ATTEMPTS,
    SETTLED_STATES,
    UNREADABLE_CODES,
    Models,
    _by_revision,
    _definition_id,
    _legacy_fields,
    _live,
    _live_rows,
    _metadata,
    _settled,
    _unchanged,
    _unpinned_runs,
    agent_candidate,
    bundle_certificate,
    default_models,
    environments_to_backfill,
    get_archive,
    match_members,
    run_plan,
    side_row_plan,
    split_archive,
)

ERROR = "error"
WARNING = "warning"
INFO = "info"


@contextmanager
def _read_only() -> Iterator[None]:
    outermost = not connection.in_atomic_block
    with transaction.atomic():
        if outermost and connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
        try:
            yield
        finally:
            transaction.set_rollback(True)


class _Report:
    def __init__(self, job_id: Any) -> None:
        self.data: dict[str, Any] = {
            "job_id": str(job_id),
            "short_id": str(job_id)[:8],
            "outcome": "",
            "layout": None,
            "facts": {},
            "findings": [],
        }

    def fact(self, **values: Any) -> None:
        self.data["facts"].update(values)

    def finding(self, severity: str, code: str, **details: Any) -> None:
        self.data["findings"].append({"severity": severity, "code": code, **details})


def _existing_active(model: Any, job: Any, hash_value: str, hash_kind: str) -> Any:
    return (
        _live(model)
        .filter(
            job_id=job.id,
            hash=hash_value,
            hash_kind=hash_kind,
            status=VersionStatus.ACTIVE,
        )
        .order_by("-version_number")
        .first()
    )


def _fetch_archive(
    job: Any, key: str, revision: str, report: _Report, attempts: int
) -> tuple[Any, str, bool]:
    """The archive the backfill would read and the revision it would record.

    Returns ``(archive, recorded_revision, storage_failed)``; runs outside any transaction.
    """
    try:
        body = get_archive(key, attempts=attempts)
    except Exception as exc:  # noqa: BLE001 - the backfill would fail here
        report.finding(ERROR, "storage_error", error=type(exc).__name__)
        return None, revision, True
    if body is None:
        report.finding(WARNING, "archive_missing", detail="combined fallback version")
        report.data["layout"] = "combined"
        return None, revision, False
    report.fact(archive_bytes=len(body))
    try:
        stats = archives.archive_stats(body)
    except Exception as exc:  # noqa: BLE001 - not a tar.gz
        report.finding(
            ERROR,
            "archive_unreadable",
            error=type(exc).__name__,
            detail="not versioned; fix or replace the archive",
        )
        return None, revision, False
    report.fact(archive=stats)
    archive = split_archive(job, body)
    if not stats["within_caps"] or archive.error_code == "too_large":
        report.finding(
            ERROR,
            "archive_over_caps",
            detail="not versioned; fix or replace the archive",
            caps={
                "entries": archives.MAX_ARCHIVE_ENTRIES,
                "files": archives.MAX_ARCHIVE_FILES,
                "inflated_bytes": archives.MAX_ARCHIVE_INFLATED_BYTES,
            },
        )
        return None, revision, False
    if archive.error_code:
        report.finding(
            ERROR,
            f"archive_{archive.error_code}",
            detail=f"{archive.error}; not versioned",
        )
        return None, revision, False
    report.data["layout"] = "split"
    if not revision:
        report.finding(
            INFO, "revision_computed", detail="no authoring_revision on the job"
        )
    elif revision != archive.revision:
        report.finding(
            WARNING,
            "revision_mismatch",
            detail="the job's revision does not match its archive; runs carrying it "
            "keep only their revision",
        )
    return archive, archive.revision, False


def _check_members(
    models: Models, job: Any, archive: Any, report: _Report
) -> tuple[int, Any]:
    """The suite's scenario count and how live rows match it; findings come later."""
    rows = _live_rows(models, job)
    if archive is None:
        report.fact(live_rows=len(rows), members=len(rows))
        return len(rows), None
    scenarios = archive.scenarios or []
    match = match_members(rows, scenarios)
    report.fact(
        live_rows=len(rows),
        archive_scenarios=len(scenarios),
        members=len(match.pairs),
        matched_by_key=match.by_key,
        matched_by_name=match.by_name,
        scenarios_without_row=match.scenarios_without_row,
        rows_without_scenario=match.rows_without_scenario,
        duplicate_rows=match.duplicate_rows,
    )
    return len(scenarios), match


def _member_findings(match: Any, suite_created: bool, report: _Report) -> None:
    """Member findings matter only when a new suite version would get these members."""
    if match is None:
        return
    if not suite_created:
        report.finding(
            INFO, "suite_reused", detail="an existing suite version is reused"
        )
        return
    if match.scenarios_without_row:
        report.finding(
            ERROR,
            "scenarios_without_row",
            count=match.scenarios_without_row,
            detail="these archive scenarios would have no suite member",
        )
    if match.rows_without_scenario:
        report.finding(
            WARNING, "rows_without_scenario", count=match.rows_without_scenario
        )
    if match.duplicate_rows:
        report.finding(WARNING, "duplicate_rows", count=match.duplicate_rows)
    if match.by_name:
        report.finding(INFO, "matched_by_name", count=match.by_name)


def _predict_versions(
    models: Models,
    job: Any,
    archive: Any,
    key: str,
    revision: str,
    count: int,
    report: _Report,
) -> bool:
    """The outcome and scenario count; ``True`` when the suite version would be new."""
    if archive is not None:
        hashes = (archive.pieces.environment_hash, archive.pieces.suite_hash)
        hash_kind = HashKind.TREE_V1
    else:
        value, hash_kind, _, _ = _legacy_fields(job, key, revision)
        hashes = (value, value)
    existing_environment = _existing_active(
        models.HarnessEnvironmentVersion, job, hashes[0], hash_kind
    )
    existing_suite = _existing_active(
        models.HarnessSuiteVersion, job, hashes[1], hash_kind
    )
    suite_created = existing_suite is None
    if job.environment_version_id is None:
        report.data["outcome"] = "versioned"
    elif existing_environment is None or suite_created:
        report.data["outcome"] = "appended"
    elif (job.environment_version_id, job.suite_version_id) != (
        existing_environment.id,
        existing_suite.id,
    ):
        report.data["outcome"] = "default_moved"
    else:
        report.data["outcome"] = "revision_noted"
    report.fact(
        hash_kind=hash_kind,
        environment_version_new=existing_environment is None,
        suite_version_new=suite_created,
    )
    after = job.scenario_count
    if (
        suite_created
        and archive is not None
        and _settled(job)
        and 1 <= count <= MAX_SCENARIOS_PER_JOB
        and count != job.scenario_count
    ):
        after = count
    report.fact(scenario_count_now=job.scenario_count, scenario_count_after=after)
    return suite_created


def _check_agent(models: Models, job: Any, report: _Report) -> tuple[Any, Any]:
    agent, source = agent_candidate(models, job)
    report.fact(
        agent_source=source,
        agent_version_id=str(agent.id) if agent is not None else None,
        agent_already_pinned=job.agent_version_id is not None,
    )
    if agent is None:
        report.finding(WARNING, "no_agent", detail="no A1; runs keep the unpinned path")
        return None, None
    existing, created = side_row_plan(models, job, agent)
    report.fact(side_row="exists" if existing is not None else "create")
    return agent, existing or created


def _check_runs(
    models: Models,
    job: Any,
    agent: Any,
    runtime: Any,
    predicted: Mapping[str, Any],
    report: _Report,
) -> None:
    # The run gives every active environment version without an agent A1 first.
    fill_agent_id = agent.id if agent is not None else None
    environments = {
        revision: (
            SimpleNamespace(id=version.id, agent_version_id=fill_agent_id)
            if version.agent_version_id is None and fill_agent_id is not None
            else version
        )
        for revision, version in _by_revision(
            models.HarnessEnvironmentVersion, job
        ).items()
    }
    environments.update(predicted)
    suites = {**_by_revision(models.HarnessSuiteVersion, job), **predicted}
    definition_id = _definition_id(models, job, runtime)
    tally: Counter[str] = Counter()
    executions: set[Any] = set()
    for run in _unpinned_runs(models, job):
        plan = run_plan(models, run, environments, suites, definition_id, fill_agent_id)
        if plan.pinned:
            tally["already_pinned"] += 1
        elif plan.matched:
            tally["would_pin"] += 1
        elif plan.legacy_revision:
            tally["legacy_revision"] += 1
        elif not plan.revision:
            tally["no_revision"] += 1
        elif not plan.settled:
            tally["busy"] += 1
        else:
            tally["already_marked"] += 1
        tally["agent_pins_to_fill"] += int(plan.agent_version_id is not None)
        if plan.fill_execution and plan.execution.id not in executions:
            # Runs can share an execution; the run fills it once.
            executions.add(plan.execution.id)
            tally["test_execution_pins_to_fill"] += 1
    report.fact(runs=dict(tally))
    if tally["busy"]:
        report.finding(WARNING, "runs_busy", count=tally["busy"])


def _load(models: Models, job_id: Any, report: _Report) -> tuple[Any, str, str, bool]:
    """The job and what the backfill would start from; ``job`` is ``None`` when done."""
    job = _live(models.HostedHarnessJob).filter(id=job_id).first()
    if job is None:
        report.data["outcome"] = "not_found"
        return None, "", "", False
    if job.environment_id is not None:
        report.data["outcome"] = "not_environment"
        return None, "", "", False
    metadata = _metadata(job)
    key = str(metadata.get("authoring_object_key") or "").strip()
    if not key:
        report.data["outcome"] = "no_archive"
        return None, "", "", False
    revision = str(metadata.get("authoring_revision") or "")
    stale_marker = bool(metadata.get("scenario_extend")) and job.state in SETTLED_STATES
    report.fact(
        state=job.state,
        settled=_settled(job),
        revision_present=bool(revision),
        stale_extend_marker=stale_marker,
        already_versioned=job.environment_version_id is not None,
    )
    if stale_marker:
        report.finding(
            WARNING,
            "stale_extend_marker",
            detail="versioned, but its scenario count is left as is",
        )
    elif not _settled(job):
        report.finding(
            WARNING, "busy", detail="versions and pins only, no payload writes"
        )
    return job, key, revision, _unchanged(models, job, revision)


def _finish(
    models: Models,
    job: Any,
    key: str,
    revision: str,
    unchanged: bool,
    fetched: tuple[Any, str] | None,
    report: _Report,
) -> None:
    agent, runtime = _check_agent(models, job, report)
    predicted: dict[str, Any] = {}
    if unchanged:
        report.data["outcome"] = "current"
    else:
        archive, recorded = fetched or (None, revision)
        if any(
            finding["code"]
            in {f"archive_{code}" for code in UNREADABLE_CODES} | {"archive_over_caps"}
            for finding in report.data["findings"]
        ):
            # The backfill pins the agent and records nothing else.
            report.data["outcome"] = "skipped_unreadable"
            return
        count, match = _check_members(models, job, archive, report)
        suite_created = _predict_versions(
            models, job, archive, key, recorded, count, report
        )
        _member_findings(match, suite_created, report)
        if recorded:
            agent_id = agent.id if agent is not None else None
            predicted[recorded] = SimpleNamespace(id=None, agent_version_id=agent_id)
        eligible = (
            archive is not None
            and runtime is not None
            and bundle_certificate(archive, HashKind.TREE_V1) is not None
            and certifies_bundle(archive.sealed or {})
        )
        report.fact(
            bundle_row_eligible=eligible,
            suite_version_new=suite_created,
            # A settled environment's default pair is kept for a roll back.
            default_pair_kept=_settled(job),
        )
    _check_runs(models, job, agent, runtime, predicted, report)


def check_environment(
    job_id: Any,
    models: Models | None = None,
    *,
    attempts: int = READ_ATTEMPTS,
) -> dict[str, Any]:
    """What the backfill would do to one environment; writes nothing."""
    models = models or default_models()
    report = _Report(job_id)
    with _read_only():
        job, key, revision, unchanged = _load(models, job_id, report)
    if job is None:
        report.finding(
            ERROR,
            "not_backfillable",
            detail=f"{report.data['outcome']}: the backfill cannot use this id",
        )
        return report.data
    fetched = None
    if not unchanged:
        archive, recorded, storage_failed = _fetch_archive(
            job, key, revision, report, attempts
        )
        if storage_failed:
            report.data["outcome"] = "would_raise"
            report.fact(storage_failed=True)
            return report.data
        fetched = (archive, recorded)
    with _read_only():
        _finish(models, job, key, revision, unchanged, fetched, report)
    return report.data


def check_all(
    models: Models | None = None, job_ids: list[Any] | None = None
) -> dict[str, Any]:
    """Every environment the backfill would visit, or only these ids, with totals."""
    models = models or default_models()
    if job_ids is None:
        with _read_only():
            ids = environments_to_backfill(models)
    else:
        ids = list(job_ids)
    results = []
    attempts = READ_ATTEMPTS
    for job_id in ids:
        try:
            result = check_environment(job_id, models, attempts=attempts)
        except Exception as exc:  # noqa: BLE001 - one environment never stops the rest
            report = _Report(job_id)
            report.data["outcome"] = "check_failed"
            report.finding(ERROR, "check_failed", error=type(exc).__name__)
            result = report.data
        if result["facts"].get("storage_failed"):
            # Storage looks down: keep checking, without waiting on retries each time.
            attempts = 1
        results.append(result)
    errors = [
        {"job_id": one["job_id"], **finding}
        for one in results
        for finding in one["findings"]
        if finding["severity"] == ERROR
    ]
    warnings = [
        {"job_id": one["job_id"], **finding}
        for one in results
        for finding in one["findings"]
        if finding["severity"] == WARNING
    ]
    return {
        "environments": results,
        "totals": {
            "environments": len(results),
            "outcomes": dict(Counter(one["outcome"] for one in results)),
            "layouts": dict(Counter(one["layout"] for one in results if one["layout"])),
            "errors": len(errors),
            "warnings": len(warnings),
        },
        "errors": errors,
        "warnings": warnings,
    }
