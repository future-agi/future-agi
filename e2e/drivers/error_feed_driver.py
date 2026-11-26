# ruff: noqa: E402 -- bootstrap Django before importing models.
"""Private managed-stack fixture driver. Never installed as a product endpoint.

Known investigation/grouping outputs replace model decisions, not publication,
status evaluation, counters, memberships, fencing, or Temporal execution.
"""

import asyncio
import hashlib
import json
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

if os.getenv("E2E_ERROR_FEED_PROFILE") != "th8613" or os.getenv("ENV_TYPE") != "local":
    raise SystemExit("Private Error Feed driver requires the dedicated local profile")
if os.getenv("PG_HOST") != "postgres" or os.getenv("TEMPORAL_HOST") != "temporal:7233":
    raise SystemExit("Private driver refuses external data/Temporal services")
sys.path.insert(0, "/app/backend")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
import django

django.setup()
from django.db import close_old_connections
from django.test import override_settings
from tfc.middleware.workspace_context import set_workspace_context
from tracer.models.project import Project
from tracer.models.trace_error_analysis import TraceErrorGroup
from tracer.models.trace_grouping import TraceGroupingIssueState
from tracer.models.trace_investigation import (
    TraceInvestigationFinding,
)
from tracer.models.trace_scan import TraceScanConfig
from tracer.services.grouping.accounting import reserve_call, settle_call
from tracer.services.grouping.control import (
    GroupingConflict,
    claim_feature_jobs,
    claim_grouping_work,
    checkpoint_attempt,
    update_grouping_attempt,
)
from tracer.services.grouping.feature_completion import (
    _text_digest,
    _view_texts,
    complete_feature_job,
)
from tracer.services.grouping.publish import publish_grouping
from tracer.queries.grouping import canonical_grouping_source_digest
from tracer.services.trace_investigation import (
    canonical_wire_result_digest,
    claim_due_investigations,
    publish_investigation,
    record_trace_notifications,
)


def instant(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() is None:
        raise ValueError("Fixture times require a timezone")
    if abs((result - datetime.now(UTC)).total_seconds()) > 48 * 3600:
        raise ValueError("Controlled fixture time must remain within 48h of this run")
    return result


@contextmanager
def clock(at):
    with patch("django.utils.timezone.now", lambda: at):
        yield


def owned_project(body):
    prefix = body["prefix"]
    if not prefix.startswith("e2e-h1-") or len(prefix) > 100:
        raise ValueError("Expected the public scopeActors fixture prefix")
    project = Project.no_workspace_objects.select_related(
        "workspace", "organization"
    ).get(
        pk=uuid.UUID(body["project_id"]),
        workspace_id=uuid.UUID(body["workspace_id"]),
        organization_id=uuid.UUID(body["organization_id"]),
    )
    if not project.name.startswith(prefix + "-"):
        raise ValueError("Refusing a project outside this test's minted names")
    set_workspace_context(
        workspace=project.workspace, organization=project.organization
    )
    return project


def issue_receipt(issue):
    issue.refresh_from_db()
    return {
        "id": str(issue.pk),
        "cluster_id": issue.cluster_id,
        "status": issue.status,
        "status_origin": issue.status_origin,
        "status_revision": issue.status_revision,
        "state": issue.escalation_state,
    }


def wire_result(claim, trace, number):
    findings = [
        {
            "finding_id": f"finding-{index}",
            "kind": "outcome",
            "statement": f"The refund tool executed 10 instead of the requested 100 (finding {index}).",
            "requirement_id": "refund-amount",
            "evidence_ids": ["root-evidence"],
            "recovery": "not_observed",
            "attribution": {
                role: {
                    "status": "unknown" if role == "origin" else "supported",
                    "span_id": None if role == "origin" else trace["root_span_id"],
                    "evidence_ids": [] if role == "origin" else ["root-evidence"],
                }
                for role in ("origin", "decisive", "symptom")
            },
        }
        for index in range(number)
    ]
    result = {
        key: claim[key]
        for key in (
            "contract_version",
            "organization_id",
            "workspace_id",
            "project_id",
            "job_id",
            "generation",
            "attempt_id",
            "trace_id",
            "engine_version",
            "read_cutoff",
        )
    }
    result.update(
        memory_snapshot_id=claim["memory"]["snapshot_id"],
        memory_digest=claim["memory"]["digest"],
        evidence_digest="sha256:" + "e" * 64,
        execution_status="completed",
        outcome="failure" if number else "success",
        findings=findings,
        requirement_checks=[
            {
                "requirement_id": "refund-amount",
                "requirement": "Execute the requested refund amount.",
                "status": "violated",
                "evidence_ids": ["root-evidence"],
            }
        ]
        if number
        else [],
        evidence_receipts=[
            {
                "evidence_id": "root-evidence",
                "span_id": trace["root_span_id"],
                "parent_span_id": None,
                "excerpt": "requested=100 executed=10",
            }
        ],
        verification_receipts=[],
        coverage={
            "scope": "collector trace at read cutoff",
            "observed_span_count": 2,
            "read_complete": True,
            "future_arrivals_known": False,
        },
        usage={
            "model_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": "0",
            "cost_status": "priced",
        },
        gateway_accounting=[],
    )
    result["result_digest"] = canonical_wire_result_digest(result)
    return result


def feature_rows(snapshot):
    source = canonical_grouping_source_digest(snapshot)
    return [
        {
            "occurrence_id": occurrence,
            "view": view,
            "source_digest": source,
            "evidence_revision": snapshot["report"]["evidence_digest"],
            "text_digest": _text_digest(text),
            "feature_digest": hashlib.sha256(
                f"{occurrence}:{view}".encode()
            ).hexdigest(),
            "model": "all-MiniLM-L6-v2",
            "model_revision": None,
            "serving_release": "local-e2e-fixture",
            "dimension": 384,
            "vector": [1.0] * 384,
            "index_buckets": [
                {"table": index, "signature": index} for index in range(8)
            ],
        }
        for (occurrence, view), text in _view_texts(snapshot).items()
    ]


def citations(ids):
    rows = {
        str(row.id): row
        for row in TraceInvestigationFinding.no_workspace_objects.filter(id__in=ids)
    }
    if set(rows) != set(ids):
        raise ValueError("Missing current fixture findings")
    return [
        {
            "occurrence_id": key,
            "evidence_id": "report",
            "evidence_digest": _text_digest(rows[key].statement),
            "quote": rows[key].statement[:24],
        }
        for key in ids
    ]


def mechanism(title):
    return {
        "title": title,
        "mechanism": "Refund amount differs from the requested amount",
        "fix_hypothesis": "Pass the requested amount to the refund tool",
        "falsifier": "A verified trace shows the requested amount was executed",
    }


def raw_group(ids, target, spec, own_ids=None):
    quotes = citations(own_ids or ids)
    return {
        "target_issue_id": target,
        "member_ids": ids,
        **spec,
        "predicted_observations": ["Requested and executed refund amounts differ"],
        "citations": [
            {
                "finding_id": item["occurrence_id"],
                **{key: value for key, value in item.items() if key != "occurrence_id"},
            }
            for item in quotes
        ],
        "contradictions": [],
        "alternatives": ["Transport retry"],
        "missing_evidence": [],
    }


def reserve_and_settle(claim, result):
    request = {
        "attempt_id": uuid.UUID(claim["attempt_id"]),
        "lease_token": claim["lease_token"],
        "request_key": f"local-e2e:{claim['attempt_id']}",
        "request_digest": "sha256:"
        + hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest(),
    }
    reserved = reserve_call(**request, max_cost_usd="0.001")
    settled = settle_call(
        **request,
        status="settled",
        result=result,
        model_used="local-e2e-fixture",
        cost_usd="0",
        input_tokens=0,
        output_tokens=0,
    )
    if reserved["receipt_id"] != settled["receipt_id"]:
        raise ValueError("Reservation identity changed")
    return settled["receipt_id"]


def publish_commands(claim, commands, raw, *, allow_rejection=False):
    receipt = reserve_and_settle(claim, raw)
    for index, command in enumerate(commands):
        if "admission" in command:
            command["admission"] = (
                {"primary_receipt_id": receipt}
                if command["type"] == "remove"
                else {
                    "primary_receipt_id": receipt,
                    "group_index": index,
                    "repair_receipt_id": None,
                }
            )
        if "admissions" in command:
            command["admissions"] = [
                {
                    "primary_receipt_id": receipt,
                    "group_index": item,
                    "repair_receipt_id": None,
                }
                for item in range(len(command["parts"]))
            ]
    checkpoint_attempt(
        attempt_id=uuid.UUID(claim["attempt_id"]),
        lease_token=claim["lease_token"],
        expected_revision=claim["checkpoint_revision"],
        checkpoint={"fixture_commands": commands},
    )
    try:
        return publish_grouping(
            attempt_id=uuid.UUID(claim["attempt_id"]),
            lease_token=claim["lease_token"],
            idempotency_key=f"local-e2e:{claim['attempt_id']}",
            snapshot_digest=claim["snapshot_digest"],
            registry_revision=claim["registry_revision"],
            commands=commands,
            receipt_ids=[receipt],
        )
    except GroupingConflict as exc:
        if not allow_rejection:
            raise
        update_grouping_attempt(
            attempt_id=uuid.UUID(claim["attempt_id"]),
            lease_token=claim["lease_token"],
            action="cancel",
        )
        return {
            "rejected": type(exc).__name__,
            "reason": str(exc),
            "receipt_id": receipt,
        }


def publish_source(body, project, now):
    from tracer.services.clickhouse.v2 import get_reader
    from tracer.services.feed_escalation_inputs import completion_time_utc

    traces = body["traces"]
    if not 1 <= len(traces) <= 10 or any(
        type(row["findings"]) is not int or not 0 <= row["findings"] <= 100
        for row in traces
    ):
        raise ValueError("Fixture publication exceeds source bounds")
    foreign = (
        TraceScanConfig.no_workspace_objects.filter(enabled=True)
        .exclude(project=project)
        .exists()
    )
    if foreign:
        raise ValueError("Another enabled scanner exists; run this profile serially")
    config, _ = TraceScanConfig.no_workspace_objects.update_or_create(
        project=project,
        defaults={
            "sampling_rate": 1.0,
            "enabled": True,
            "scan_version": "omega-v1",
            "omega_memory": [
                {"id": "refund-amount", "text": "Execute the requested refund amount."}
            ],
            "omega_limits": {"max_model_calls": 4},
        },
    )
    reports = []
    try:
        roots = get_reader().list_root_spans_by_trace_ids(
            [row["trace_id"] for row in traces],
            project_id=str(project.pk),
            include_heavy=False,
            columns=["trace_id", "end_time"],
        )
        for row in traces:
            actual = roots.get(row["trace_id"])
            if actual is None or completion_time_utc(actual["end_time"]) != instant(
                row["occurred_at"]
            ):
                raise ValueError(
                    "Collector occurrence time does not match fixture input"
                )
        with clock(now):
            deliveries = [
                {
                    "topic": "error-feed.trace-available.v1",
                    "partition": 0,
                    "offset": int(uuid.uuid4().int % (2**62)),
                    "value": {
                        "version": 1,
                        "event_id": uuid.uuid4(),
                        "organization_id": project.organization_id,
                        "workspace_id": project.workspace_id,
                        "project_id": project.pk,
                        "event_kind": "root_span_written",
                        "emitted_at": now,
                        "traces": [
                            {
                                "trace_id": uuid.UUID(row["trace_id"]),
                                "root_span_id": row["root_span_id"],
                                "root_end_time": instant(row["occurred_at"]),
                            }
                        ],
                    },
                }
                for row in traces
            ]
            recorded = record_trace_notifications(deliveries=deliveries)
        ready_at = now + timedelta(seconds=61)
        pending = {row["trace_id"]: row for row in traces}
        with clock(ready_at):
            while pending:
                claims = claim_due_investigations(
                    worker_id="local-e2e-fixture", engine_version="omega-v1", limit=10
                )["claims"]
                if not claims:
                    raise ValueError("Investigation did not claim the recorded source")
                for claim in claims:
                    row = pending.pop(str(claim["trace_id"]))
                    result = wire_result(claim, row, row["findings"])
                    accepted = publish_investigation(
                        idempotency_key=f"local-e2e:{claim['attempt_id']}",
                        lease_token=claim["lease_token"],
                        result=result,
                        wire_result_digest=canonical_wire_result_digest(result),
                    )
                    reports.append(accepted)
            while True:
                claims = claim_feature_jobs(worker_id="local-e2e-fixture", limit=10)[
                    "claims"
                ]
                if not claims:
                    break
                for claim in claims:
                    if str(claim["snapshot"]["report"]["project_id"]) != str(
                        project.pk
                    ):
                        raise ValueError("Feature claim escaped the owned project")
                    complete_feature_job(
                        feature_job_id=uuid.UUID(claim["feature_job_id"]),
                        lease_token=claim["lease_token"],
                        status="ready",
                        features=feature_rows(claim["snapshot"]),
                    )
        with clock(ready_at + timedelta(seconds=6)):
            claims = claim_grouping_work(worker_id="local-e2e-fixture", limit=10)[
                "claims"
            ]
            if not claims:
                # Success reports with no findings can invalidate old membership without new grouping work.
                return {
                    "source_reports": reports,
                    "recorded": recorded,
                    "created_issue_ids": {},
                }
            if len(claims) != 1 or claims[0]["project_id"] != str(project.pk):
                raise ValueError("Grouping did not claim exactly the owned cohort")
            claim = claims[0]
            if body.get("prepare_only"):
                # Use the normal cancel/reclaim protocol rather than editing lease or work rows.
                update_grouping_attempt(
                    attempt_id=uuid.UUID(claim["attempt_id"]),
                    lease_token=claim["lease_token"],
                    action="cancel",
                )
                return {"source_reports": reports, "recorded": recorded}
            ids = claim["pending_ids"]
            target = body.get("target_issue_id")
            if target:
                state = TraceGroupingIssueState.no_workspace_objects.get(
                    cluster_id=target, scope__project=project
                )
                spec = state.mechanism
                own_ids = list(dict.fromkeys([*ids, *state.prototype_occurrence_ids]))
                command = {
                    "type": "attach",
                    "issue_id": target,
                    "expected_issue_revision": state.revision,
                    "occurrence_ids": ids,
                    "citations": citations(own_ids),
                    "admission": {},
                }
            else:
                own_ids = ids
                spec = mechanism(body["title"])
                command = {
                    "type": "create",
                    "temporary_id": "fixture-issue",
                    "occurrence_ids": ids,
                    "prototype_occurrence_ids": ids[:5],
                    "mechanism": spec,
                    "citations": citations(ids),
                    "admission": {},
                }
            result = publish_commands(
                claim,
                [command],
                {"groups": [raw_group(ids, target, spec, own_ids)], "deferred": []},
                allow_rejection=body.get("allow_rejection", False),
            )
            result["source_reports"] = reports
            result["recorded"] = recorded
            if not result.get("rejected"):
                identity = target or result["created_issue_ids"]["fixture-issue"]
                result["issue"] = issue_receipt(
                    TraceErrorGroup.no_workspace_objects.get(
                        pk=identity, project=project
                    )
                )
            return result
    finally:
        config.enabled = False
        config.save(update_fields=["enabled", "updated_at"])


async def evaluate_through_temporal(body, project, now):
    from temporalio.worker import Worker, UnsandboxedWorkflowRunner
    from tfc.temporal.common.client import get_client
    from tfc.temporal.drop_in.decorator import (
        _ACTIVITY_WRAPPERS,
        get_temporal_activities,
    )
    from tfc.temporal.drop_in.workflow import TaskRunnerInput, TaskRunnerWorkflow
    from tracer.services.feed_escalation import sweep_feed_escalations

    queue = f"th8613-e2e-{uuid.uuid4()}"
    client = await get_client()
    # Register the production activity wrapper, DB-connection handling and workflow.
    get_temporal_activities(queue="tasks_s")
    wrapper = _ACTIVITY_WRAPPERS[sweep_feed_escalations.name]
    run_id = f"th8613-evaluation-{uuid.uuid4()}"
    with clock(now), ThreadPoolExecutor(max_workers=1) as executor:
        async with Worker(
            client,
            task_queue=queue,
            workflows=[TaskRunnerWorkflow],
            activities=[wrapper],
            activity_executor=executor,
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await client.execute_workflow(
                TaskRunnerWorkflow.run,
                TaskRunnerInput(
                    activity_name="sweep_feed_escalations",
                    args=[],
                    kwargs={"workspace_id": str(project.workspace_id)},
                    queue=queue,
                    time_limit=120,
                    max_retries=0,
                ),
                id=run_id,
                task_queue=queue,
                execution_timeout=timedelta(seconds=150),
            )
    return {
        "workflow_id": run_id,
        "boundary_clock": now.isoformat(),
        "result": asdict(result),
    }


def publish_legacy(body, project, now):
    """Controlled scanner/eval outputs enter the real clustering writers."""
    from model_hub.models.evals_metric import EvalTemplate
    from tracer.models.custom_eval_config import CustomEvalConfig
    from tracer.models.observation_span import EvalLogger
    from tracer.models.trace_scan import TraceScanIssue, TraceScanResult
    from tracer.queries import eval_clustering, scan_clustering
    from tracer.types.eval_cluster_types import ClusterableEvalResult, EvalClusterMeta
    from tracer.types.scan_types import ClusterableIssue

    traces = body["traces"]
    if not 1 <= len(traces) <= 100:
        raise ValueError("Legacy fixture exceeds its bounded batch")
    identity = None
    source_ids = []
    embedding = [1.0] * 384
    with clock(now):
        if body["source"] == "scanner":
            with patch.object(scan_clustering, "_seed_severity", return_value="low"):
                for trace in traces:
                    scan = TraceScanResult.no_workspace_objects.create(
                        project=project,
                        trace_id=trace["trace_id"],
                        status="completed",
                        has_issues=True,
                    )
                    if not 1 <= trace["findings"] <= 100:
                        raise ValueError("Invalid scanner fixture finding count")
                    for _index in range(trace["findings"]):
                        row = TraceScanIssue.no_workspace_objects.create(
                            scan_result=scan,
                            category="Tool Failures",
                            group="Tool Failures",
                            fix_layer="Tools",
                            confidence="H",
                            brief=body["title"],
                        )
                        item = ClusterableIssue(
                            issue_id=str(row.pk),
                            trace_id=trace["trace_id"],
                            project_id=str(project.pk),
                            category=row.category,
                            group=row.group,
                            fix_layer=row.fix_layer,
                            brief=row.brief,
                            confidence="H",
                        )
                        if identity is None:
                            identity = scan_clustering.create_cluster(
                                str(project.pk), item, embedding
                            )
                        else:
                            scan_clustering.assign_to_cluster(
                                identity, str(project.pk), item, embedding
                            )
                        source_ids.append(str(row.pk))
        elif body["source"] == "eval":
            template = EvalTemplate.no_workspace_objects.filter(
                owner="system", deleted=False
            ).first()
            if template is None:
                raise ValueError(
                    "Managed bootstrap did not install evaluation templates"
                )
            config = CustomEvalConfig.no_workspace_objects.create(
                project=project, name=body["title"], eval_template=template, config={}
            )
            with patch.object(
                eval_clustering,
                "_eval_cluster_meta",
                return_value=EvalClusterMeta(
                    title=body["title"], fix_layer="Tools", severity="low"
                ),
            ):
                for trace in traces:
                    entry = EvalLogger.no_workspace_objects.create(
                        custom_eval_config=config,
                        target_type="span",
                        trace_id=trace["trace_id"],
                        observation_span_id=trace["root_span_id"],
                        output_bool=False,
                        eval_explanation="Requested refund amount differs from execution",
                        config_hash="a" * 64,
                        eval_task_id=f"local-e2e-{uuid.uuid4()}",
                    )
                    item = ClusterableEvalResult(
                        eval_logger_id=str(entry.pk),
                        project_id=str(project.pk),
                        eval_config_id=str(config.pk),
                        eval_name=config.name,
                        target_type="span",
                        trace_id=trace["trace_id"],
                        explanation=entry.eval_explanation,
                        score=0,
                    )
                    if identity is None:
                        identity = eval_clustering.create_cluster(
                            str(project.pk), item, embedding
                        )
                    else:
                        eval_clustering.assign_to_cluster(
                            identity, str(project.pk), item, embedding
                        )
                    source_ids.append(str(entry.pk))
        else:
            raise ValueError("Unsupported legacy fixture source")
    return {
        "issue": issue_receipt(
            TraceErrorGroup.no_workspace_objects.get(
                project=project, cluster_id=identity
            )
        ),
        "source_ids": source_ids,
    }


def reconcile(body, project, now):
    """Known reconciler outputs use normal receipts and fenced publication."""
    from tracer.models.trace_grouping import TraceGroupingConstraint

    kind = body["kind"]
    states = list(
        TraceGroupingIssueState.no_workspace_objects.filter(
            scope__project=project, cluster_id__in=body["issue_ids"], retired=False
        ).order_by("cluster_id")
    )
    if len(states) != len(body["issue_ids"]) or not 1 <= len(states) <= 2:
        raise ValueError("Reconciliation escaped current owned issues")
    members = {
        str(state.cluster_id): list(
            TraceInvestigationFinding.no_workspace_objects.filter(
                cluster_id=state.cluster_id
            )
            .order_by("id")
            .values_list("id", flat=True)
        )
        for state in states
    }
    members = {key: [str(value) for value in values] for key, values in members.items()}
    if any(not values or len(values) > 16 for values in members.values()):
        raise ValueError("Reconciliation must use the full bounded membership")
    with clock(now):
        if kind == "remove":
            ids = members[str(states[0].cluster_id)]
            # A hard-rule feedback input, not a final membership/status seed.
            TraceGroupingConstraint.no_workspace_objects.get_or_create(
                scope=states[0].scope,
                kind="cannot_link",
                first_finding_id=ids[0],
                second_finding_id=ids[1],
                defaults={"reason": "Known local fixture hard-rule feedback"},
            )
        claims = claim_grouping_work(worker_id="local-e2e-reconciler", limit=10)[
            "claims"
        ]
        if len(claims) != 1 or claims[0]["project_id"] != str(project.pk):
            raise ValueError("Reconciliation requires exactly one owned pending cohort")
        claim = claims[0]
        if kind == "merge":
            ids = [item for values in members.values() for item in values]
            spec = mechanism("Merged refund amount mismatch")
            commands = [
                {
                    "type": "merge",
                    "source_issue_ids": list(members),
                    "expected_revisions": {
                        str(state.cluster_id): state.revision for state in states
                    },
                    "temporary_id": "merged",
                    "mechanism": spec,
                    "prototype_occurrence_ids": ids[:5],
                    "citations": citations(ids),
                    "admission": {},
                }
            ]
            raw = {"action": "merge", "groups": [raw_group(ids, None, spec)]}
        elif kind == "split":
            state = states[0]
            ids = members[str(state.cluster_id)]
            halves = [ids[: len(ids) // 2], ids[len(ids) // 2 :]]
            parts = [
                {
                    "temporary_id": f"split-{index}",
                    "occurrence_ids": half,
                    "prototype_occurrence_ids": half[:5],
                    "mechanism": mechanism(f"Refund mismatch part {index}"),
                }
                for index, half in enumerate(halves)
            ]
            commands = [
                {
                    "type": "split",
                    "issue_id": str(state.cluster_id),
                    "expected_issue_revision": state.revision,
                    "parts": parts,
                    "citations": citations(ids),
                    "admissions": [],
                }
            ]
            raw = {
                "action": "split",
                "groups": [
                    raw_group(part["occurrence_ids"], None, part["mechanism"])
                    for part in parts
                ],
            }
        elif kind == "remove":
            state = states[0]
            ids = members[str(state.cluster_id)]
            commands = [
                {
                    "type": "remove",
                    "issue_id": str(state.cluster_id),
                    "expected_issue_revision": state.revision,
                    "occurrence_ids": ids[:1],
                    "hard_constraints": [[ids[0], ids[1]]],
                    "reason": "Known hard-rule feedback",
                    "admission": {},
                }
            ]
            raw = {"action": "remove", "removed_ids": ids[:1]}
        else:
            raise ValueError("Unsupported fixture reconciliation")
        commands.append(
            {
                "type": "defer",
                "occurrence_ids": claim["pending_ids"],
                "reason": "Fixture trigger intentionally remains unassigned",
            }
        )
        result = publish_commands(claim, commands, raw)
        result["removed_ids"] = ids[:1] if kind == "remove" else []
        result["issues"] = [
            issue_receipt(TraceErrorGroup.no_workspace_objects.get(pk=key))
            for key in result.get("created_issue_ids", {}).values()
        ]
        return result


def main():
    body = json.load(sys.stdin)
    project = owned_project(body)
    now = instant(body["now"])
    with override_settings(
        ERROR_FEED_GROUPING_ENABLED=True,
        ERROR_FEED_GROUPING_ALL_PROJECTS=False,
        ERROR_FEED_GROUPING_PROJECT_IDS=(str(project.pk),),
        ERROR_FEED_GROUPING_PROJECT_BUDGET_USD="10",
        ERROR_FEED_GROUPING_WORK_BUDGET_USD="10",
        ERROR_FEED_GROUPING_TENANT_BUDGET_USD="10",
        ERROR_FEED_ESCALATION_MODE="enabled",
        ERROR_FEED_ESCALATION_WORKSPACE_IDS=(str(project.workspace_id),),
        ERROR_FEED_ESCALATION_SOURCES=("scanner", "eval"),
    ):
        if body["operation"] == "qualify_session":
            from tracer.services.clickhouse.v2 import get_reader
            from tracer.services.feed_escalation_inputs import source_occurrence_time

            session_id = str(uuid.UUID(body["session_id"]))
            occurred_at, provenance = source_occurrence_time(
                project_id=str(project.pk), session_id=session_id
            )
            breadth = get_reader().aggregate_by_session_ids(
                [session_id], project_id=str(project.pk)
            )
            receipt = {
                "occurred_at": occurred_at.isoformat(),
                "provenance": provenance,
                "traces_count": breadth[session_id]["traces_count"],
            }
        elif body["operation"] == "publish":
            receipt = publish_source(body, project, now)
        elif body["operation"] == "evaluate":
            receipt = asyncio.run(evaluate_through_temporal(body, project, now))
        elif body["operation"] == "publish_legacy":
            receipt = publish_legacy(body, project, now)
        elif body["operation"] == "reconcile":
            receipt = reconcile(body, project, now)
        else:
            raise ValueError("Unknown private fixture operation")
    print("TH8613_RECEIPT=" + json.dumps(receipt, default=str, separators=(",", ":")))
    close_old_connections()


if __name__ == "__main__":
    main()
