"""
MCP tools for the fi-simulate lifecycle.

Four tools in the "simulation" category:

  fi_simulate_run     — Start a run and poll until terminal, return final summary.
  fi_simulate_status  — Return current status of an in-progress run.
  fi_simulate_results — Return the eval summary for a completed run.
  fi_simulate_list    — List recent simulation runs for the workspace.

These use Django ORM directly (same process as the backend) — no HTTP round-trips.
"""

from __future__ import annotations

import time
from typing import Optional
from uuid import UUID

import structlog
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import format_datetime, format_status, key_value_block, section
from ai_tools.registry import register_tool

logger = structlog.get_logger(__name__)

_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
_DEFAULT_THRESHOLD = 80
_DEFAULT_TIMEOUT_S = 300
_DEFAULT_POLL_INTERVAL_S = 5


# ---------------------------------------------------------------------------
# fi_simulate_run
# ---------------------------------------------------------------------------


class RunSimulationInput(PydanticBaseModel):
    run_test_id: UUID = Field(description="UUID of the RunTest (simulation suite) to execute")
    threshold: int = Field(
        default=_DEFAULT_THRESHOLD,
        ge=0,
        le=100,
        description="Pass-rate %% threshold; exit_code=0 only when pass_rate >= threshold",
    )
    timeout_s: int = Field(
        default=_DEFAULT_TIMEOUT_S,
        gt=0,
        description="Total seconds to wait before timing out",
    )
    poll_interval_s: int = Field(
        default=_DEFAULT_POLL_INTERVAL_S,
        gt=0,
        description="Seconds between status polls",
    )


@register_tool
class RunSimulationTool(BaseTool):
    name = "fi_simulate_run"
    description = (
        "Start a simulation run and poll until it reaches a terminal state "
        "(completed, failed, or cancelled). Returns the evaluation summary and "
        "whether the pass-rate threshold was met. Closes issues #80 and #81."
    )
    category = "simulation"
    input_model = RunSimulationInput

    def execute(self, params: RunSimulationInput, context: ToolContext) -> ToolResult:
        from simulate.models.run_test import RunTest
        from simulate.models.test_execution import TestExecution

        try:
            run_test = RunTest.objects.get(
                id=params.run_test_id,
                organization=context.organization,
                deleted=False,
            )
        except RunTest.DoesNotExist:
            return ToolResult.not_found("RunTest", str(params.run_test_id))

        # Trigger execution
        execution_id, start_error = _start_execution(run_test, context)
        if start_error:
            return ToolResult.error(start_error, error_code="START_FAILED")

        # Poll until terminal
        elapsed = 0.0
        polls = 0
        run_status = "pending"
        max_polls = max(1, params.timeout_s // params.poll_interval_s)

        while run_status not in _TERMINAL_STATUSES:
            if elapsed + params.poll_interval_s > params.timeout_s:
                return ToolResult.error(
                    f"Timed out after {elapsed:.0f}s waiting for execution {execution_id}",
                    error_code="TIMEOUT",
                    data={"execution_id": execution_id, "last_status": run_status, "elapsed_s": elapsed},
                )
            if polls >= max_polls:
                return ToolResult.error(
                    f"Exceeded maximum polls ({max_polls}) for execution {execution_id}",
                    error_code="TIMEOUT",
                    data={"execution_id": execution_id, "last_status": run_status},
                )
            time.sleep(params.poll_interval_s)
            elapsed += params.poll_interval_s
            polls += 1

            try:
                te = TestExecution.objects.get(id=execution_id)
                run_status = te.status
            except TestExecution.DoesNotExist:
                return ToolResult.error(
                    f"Execution {execution_id} not found during polling",
                    error_code="NOT_FOUND",
                )

        # NeverPollBeforeStart: we only reached here after execution_id was set
        # SummaryOnlyAfterTerminal: run_status is guaranteed terminal now
        summary, pass_rate, summary_error = _fetch_summary(run_test, execution_id)
        if summary_error:
            return ToolResult.error(summary_error, error_code="SUMMARY_FAILED")

        passed = run_status == "completed" and (pass_rate or 0) >= params.threshold
        exit_code = 0 if passed else 1

        info = key_value_block([
            ("Execution ID", f"`{execution_id}`"),
            ("Run Test", run_test.name),
            ("Status", format_status(run_status)),
            ("Pass Rate", f"{pass_rate:.1f}%" if pass_rate is not None else "n/a"),
            ("Threshold", f"{params.threshold}%"),
            ("Result", "PASS ✓" if passed else "FAIL ✗"),
            ("Polls", str(polls)),
            ("Elapsed", f"{elapsed:.0f}s"),
        ])
        content = section("Simulation Complete", info)

        if summary:
            rows = "\n".join(
                f"- **{item.get('name', '—')}**: "
                f"pass_rate={item.get('pass_rate', '—')}, "
                f"score={item.get('score') or item.get('avg_score', '—')}"
                for item in summary
                if isinstance(item, dict)
            )
            content += f"\n\n### Evaluation Metrics\n{rows}"

        return ToolResult(
            content=content,
            data={
                "execution_id": execution_id,
                "run_test_id": str(params.run_test_id),
                "status": run_status,
                "pass_rate": pass_rate,
                "threshold": params.threshold,
                "exit_code": exit_code,
                "polls": polls,
                "elapsed_s": round(elapsed, 1),
                "summary": summary,
            },
        )


# ---------------------------------------------------------------------------
# fi_simulate_status
# ---------------------------------------------------------------------------


class SimulateStatusInput(PydanticBaseModel):
    run_test_id: UUID = Field(description="UUID of the RunTest to check status for")
    execution_id: Optional[UUID] = Field(
        default=None,
        description="UUID of a specific execution (defaults to latest)",
    )


@register_tool
class SimulateStatusTool(BaseTool):
    name = "fi_simulate_status"
    description = (
        "Return the current status of a simulation run. "
        "Defaults to the latest execution for the given RunTest."
    )
    category = "simulation"
    input_model = SimulateStatusInput

    def execute(self, params: SimulateStatusInput, context: ToolContext) -> ToolResult:
        from simulate.models.run_test import RunTest
        from simulate.models.test_execution import TestExecution
        from simulate.models.call_execution import CallExecution

        try:
            run_test = RunTest.objects.get(
                id=params.run_test_id,
                organization=context.organization,
                deleted=False,
            )
        except RunTest.DoesNotExist:
            return ToolResult.not_found("RunTest", str(params.run_test_id))

        if params.execution_id:
            try:
                te = TestExecution.objects.get(
                    id=params.execution_id, run_test=run_test
                )
            except TestExecution.DoesNotExist:
                return ToolResult.not_found("TestExecution", str(params.execution_id))
        else:
            te = TestExecution.objects.filter(run_test=run_test).order_by("-created_at").first()
            if not te:
                return ToolResult.error("No executions found for this RunTest.", error_code="NOT_FOUND")

        calls = CallExecution.objects.filter(test_execution=te)
        total = calls.count()
        completed = calls.filter(status__in=["analyzing", "completed"]).count()
        failed = calls.filter(status="failed").count()

        info = key_value_block([
            ("Execution ID", f"`{te.id}`"),
            ("Run Test", run_test.name),
            ("Status", format_status(te.status)),
            ("Total Calls", str(total)),
            ("Completed", str(completed)),
            ("Failed", str(failed)),
            ("Started", format_datetime(te.started_at)),
            ("Completed At", format_datetime(te.completed_at) if te.completed_at else "—"),
        ])

        return ToolResult(
            content=section("Simulation Status", info),
            data={
                "execution_id": str(te.id),
                "run_test_id": str(run_test.id),
                "status": te.status,
                "total_calls": total,
                "completed_calls": completed,
                "failed_calls": failed,
            },
        )


# ---------------------------------------------------------------------------
# fi_simulate_results
# ---------------------------------------------------------------------------


class SimulateResultsInput(PydanticBaseModel):
    run_test_id: UUID = Field(description="UUID of the RunTest")
    execution_id: Optional[UUID] = Field(
        default=None,
        description="UUID of the execution (defaults to latest completed)",
    )
    threshold: int = Field(
        default=_DEFAULT_THRESHOLD,
        ge=0,
        le=100,
        description="Pass-rate threshold used to compute exit_code",
    )


@register_tool
class SimulateResultsTool(BaseTool):
    name = "fi_simulate_results"
    description = "Fetch the evaluation summary for a completed simulation run."
    category = "simulation"
    input_model = SimulateResultsInput

    def execute(self, params: SimulateResultsInput, context: ToolContext) -> ToolResult:
        from simulate.models.run_test import RunTest
        from simulate.models.test_execution import TestExecution

        try:
            run_test = RunTest.objects.get(
                id=params.run_test_id,
                organization=context.organization,
                deleted=False,
            )
        except RunTest.DoesNotExist:
            return ToolResult.not_found("RunTest", str(params.run_test_id))

        if params.execution_id:
            try:
                te = TestExecution.objects.get(
                    id=params.execution_id, run_test=run_test
                )
            except TestExecution.DoesNotExist:
                return ToolResult.not_found("TestExecution", str(params.execution_id))
        else:
            te = (
                TestExecution.objects.filter(run_test=run_test, status="completed")
                .order_by("-created_at")
                .first()
            )
            if not te:
                return ToolResult.error(
                    "No completed executions found for this RunTest.",
                    error_code="NOT_FOUND",
                )

        if te.status not in _TERMINAL_STATUSES:
            return ToolResult.error(
                f"Execution is still in progress (status={te.status}). "
                "Use fi_simulate_status to check progress.",
                error_code="NOT_READY",
            )

        summary, pass_rate, error = _fetch_summary(run_test, str(te.id))
        if error:
            return ToolResult.error(error, error_code="SUMMARY_FAILED")

        passed = te.status == "completed" and (pass_rate or 0) >= params.threshold
        info = key_value_block([
            ("Execution ID", f"`{te.id}`"),
            ("Run Test", run_test.name),
            ("Status", format_status(te.status)),
            ("Pass Rate", f"{pass_rate:.1f}%" if pass_rate is not None else "n/a"),
            ("Threshold", f"{params.threshold}%"),
            ("Result", "PASS ✓" if passed else "FAIL ✗"),
        ])

        content = section("Evaluation Results", info)
        if summary:
            rows = "\n".join(
                f"- **{item.get('name', '—')}**: pass_rate={item.get('pass_rate', '—')}"
                for item in summary
                if isinstance(item, dict)
            )
            content += f"\n\n### Metrics\n{rows}"

        return ToolResult(
            content=content,
            data={
                "execution_id": str(te.id),
                "run_test_id": str(run_test.id),
                "status": te.status,
                "pass_rate": pass_rate,
                "threshold": params.threshold,
                "exit_code": 0 if passed else 1,
                "summary": summary,
            },
        )


# ---------------------------------------------------------------------------
# fi_simulate_list
# ---------------------------------------------------------------------------


class SimulateListInput(PydanticBaseModel):
    limit: int = Field(default=10, ge=1, le=100, description="Maximum number of runs to return")


@register_tool
class SimulateListTool(BaseTool):
    name = "fi_simulate_list"
    description = "List recent simulation runs (RunTests) for the current workspace."
    category = "simulation"
    input_model = SimulateListInput

    def execute(self, params: SimulateListInput, context: ToolContext) -> ToolResult:
        from simulate.models.run_test import RunTest
        from simulate.models.test_execution import TestExecution

        run_tests = (
            RunTest.objects.filter(
                organization=context.organization,
                workspace=context.workspace,
                deleted=False,
            )
            .order_by("-created_at")[: params.limit]
        )

        if not run_tests:
            return ToolResult(content="No simulation runs found.", data={"runs": []})

        rows = []
        for rt in run_tests:
            latest = TestExecution.objects.filter(run_test=rt).order_by("-created_at").first()
            rows.append({
                "id": str(rt.id),
                "name": rt.name,
                "latest_execution_id": str(latest.id) if latest else None,
                "latest_status": latest.status if latest else "never_run",
                "created_at": rt.created_at.isoformat() if rt.created_at else None,
            })

        lines = [
            f"- **{r['name']}** (`{r['id']}`): "
            f"latest={r['latest_status'] or 'never_run'}"
            for r in rows
        ]
        content = section(f"Simulation Runs ({len(rows)})", "\n".join(lines))

        return ToolResult(content=content, data={"runs": rows})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _start_execution(run_test, context) -> tuple[Optional[str], Optional[str]]:
    """Trigger a new test execution. Returns (execution_id, error_message)."""
    from simulate.models.test_execution import TestExecution
    from simulate.models.scenarios import Scenarios

    scenarios = Scenarios.objects.filter(
        run_test=run_test, organization=context.organization
    )
    if not scenarios.exists():
        return None, "RunTest has no scenarios — cannot execute"

    from simulate.temporal.client import start_test_execution_workflow
    import uuid

    execution_id = str(uuid.uuid4())
    try:
        te = TestExecution.objects.create(
            id=execution_id,
            run_test=run_test,
            status="pending",
            total_scenarios=scenarios.count(),
        )
        start_test_execution_workflow(str(run_test.id), execution_id)
        return str(te.id), None
    except Exception as exc:
        logger.exception("fi_simulate_run_start_failed", error=str(exc))
        return None, f"Failed to start execution: {exc}"


def _fetch_summary(run_test, execution_id: str) -> tuple[list, Optional[float], Optional[str]]:
    """Fetch eval summary. Returns (summary_list, pass_rate, error_message)."""
    try:
        from simulate.utils.eval_summary import (
            _build_template_statistics,
            _calculate_final_template_summaries,
            _get_completed_call_executions,
        )
        from simulate.views.run_test import _get_eval_configs_with_template

        eval_configs = _get_eval_configs_with_template(run_test)
        if not eval_configs:
            return [], None, None

        call_executions = _get_completed_call_executions(run_test, execution_id)
        template_stats = _build_template_statistics(eval_configs, call_executions)
        summary = _calculate_final_template_summaries(template_stats)

        if not summary:
            return [], None, None

        scores = [
            item.get("pass_rate") or item.get("score") or 0
            for item in summary
            if isinstance(item, dict)
        ]
        pass_rate = sum(scores) / len(scores) if scores else 0.0
        return summary, pass_rate, None

    except Exception as exc:
        logger.exception("fi_simulate_fetch_summary_failed", error=str(exc))
        return [], None, f"Failed to fetch summary: {exc}"
