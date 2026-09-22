import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  getTestRunDetailColumnQuery,
  TestRunErrorStatus,
  CallExecutionLoadingStatus,
} from "src/sections/test-detail/common";
import { normalizeEvalResult } from "src/sections/develop-detail/DataTab/common";
import { TRACE_COLUMNS } from "src/sections/simulate/environments/workspace/runs/detail/trace/traceTable.constants";

/**
 * The per-call table data source (Phase 2). Turns the product's real
 * `testExecutions.list(executionId)` payload — the same `{ column_order, results,
 * count }` the product's TestRunDetailGrid consumes — into the fixed `RunTask` /
 * `TraceColumn` view-models the ported designer TraceTable renders against.
 *
 * The two adapters are pure and separately exported so the mapping is unit-
 * tested without the network.
 */

// A per-call row's status is two axes: whether the call itself ran (its own
// `status`), and — for a call that ran — whether its evals agreed. `error` and
// `unmeasured` come from the call status; a completed call is `failed` when any
// eval explicitly failed, else `passed`. No real per-call "flaky" signal exists,
// so `flaky` is never emitted (the Mixed chip reads 0 and disables — honest).
function callRan(row) {
  const s = String(row?.status || "").toLowerCase();
  if (TestRunErrorStatus.includes(s)) return "error";
  if (CallExecutionLoadingStatus.includes(s)) return "unmeasured";
  return "completed";
}

// One `evalResults[]` cell from a call row's `eval_metrics[evalId]`. The output
// type (Pass/Fail vs score vs choices) drives the normalisation via the product's
// `normalizeEvalResult`; `score` is coerced to the 0–1 scale the designer's Score
// cell expects (`Math.round(score*100)%`, `interpolateColorBasedOnScore(_, 1)`).
// `passed` is explicit only for pass/fail and thresholded score; choices carry no
// verdict (null) so they never count a call as failed.
function evalResultFor(row, col) {
  const data = row?.eval_metrics?.[col.id];
  if (!data) return null;
  const outputType = data.type ?? col.eval_config?.output;
  const norm = normalizeEvalResult(data.value, outputType);
  if (norm.kind === "empty") return null;

  const to01 = (n) => (n == null ? null : n <= 1 ? n : n / 100);
  let score = null;
  let passed = null;
  let label = null;
  if (norm.kind === "score") {
    score = to01(norm.score);
    passed = score != null ? score >= 0.5 : null;
  } else if (norm.kind === "passfail") {
    passed = norm.pass;
    score = passed == null ? null : passed ? 1 : 0;
    label = norm.label ?? null;
  } else if (norm.kind === "choices") {
    score = to01(norm.score);
    label = (norm.items || []).join(", ") || null;
  }

  return {
    id: col.id,
    name: data.name || col.column_name || col.id,
    score,
    passed,
    label,
    reason: data.reason || "",
    threshold: 0.5,
  };
}

/**
 * Maps one raw call row → a `RunTask`. Pure.
 * @param {Object} row          A `results[]` row from `testExecutions.list`.
 * @param {Array} evalColumns   The `column_order` entries of type "evaluation".
 * @returns {import("./runDetail").RunTask}
 */
export function mapCallRow(row, evalColumns = []) {
  const evalResults = evalColumns
    .map((col) => evalResultFor(row, col))
    .filter(Boolean);

  const ran = callRan(row);
  const status =
    ran === "completed"
      ? evalResults.some((r) => r.passed === false)
        ? "failed"
        : "passed"
      : ran;

  return {
    id: row?.id,
    scenario: row?.scenario || row?.customer_name || "Untitled scenario",
    persona: row?.customer_name || null,
    status,
    // No per-call `critical` field on the executions payload — the release-
    // blocker flag has no real feed yet, so it stays false (never fires the
    // critical banner on a real run) while the failedCritical seam is kept live.
    critical: false,
    csat: row?.overall_score != null ? Math.round(row.overall_score * 10) / 10 : null,
    turns: row?.turn_count ?? null,
    latencyMs: row?.avg_agent_latency ?? null,
    // No per-call token total in the payload — left null (renders "—" under a
    // Sample header), the My-Environments precedent for an unfed column.
    tokens: null,
    durationMs: row?.duration != null ? Math.round(row.duration * 1000) : null,
    // Routing hints for the call drawer. `simulation_call_type` is the product's
    // authoritative voice-vs-chat signal (a chat sim is "text"); `provider` feeds
    // the meta chip. Both live on the list row (not detail_mode-gated).
    simulationCallType: row?.simulation_call_type ?? null,
    provider: row?.provider ?? null,
    evalResults,
  };
}

/**
 * Builds the `TraceColumn[]` descriptor list from the payload's `column_order`.
 * The fixed system columns come from the picker vocabulary; one column is
 * appended per real evaluation column (keyed by the eval id so it matches
 * `RunTask.evalResults[].id`). Ideal-outcome / conversation-branch columns from
 * the designer are dropped — the executions payload carries no per-call field
 * for them. Pure.
 * @param {Array} columnOrder
 * @returns {import("./runDetail").TraceColumn[]}
 */
export function buildTraceColumns(columnOrder = []) {
  const staticCols = TRACE_COLUMNS.filter((c) => c.key !== "evals").map((c) => ({
    key: c.key,
    label: c.label,
    defaultOn: c.defaultOn,
    width: c.width,
    group: c.group,
  }));
  const evalCols = (columnOrder || [])
    .filter((c) => c.type === "evaluation")
    .map((c) => ({
      key: c.id,
      label: c.column_name || c.id,
      defaultOn: true,
      width: 150,
      group: "Evaluations",
    }));
  return [...staticCols, ...evalCols];
}

/**
 * The per-call table hook. Reads the product's real executions list and adapts
 * it to `{ tasks, columns, count }`. `opts` mirror the product grid's params.
 * @param {string} executionId
 * @param {{ page?: number, limit?: number, search?: string, filters?: Array }} [opts]
 * @returns {{ tasks: import("./runDetail").RunTask[],
 *   columns: import("./runDetail").TraceColumn[], count: number,
 *   isLoading: boolean }}
 */
export function useRunCalls(executionId, opts = {}) {
  const { page = 1, limit = 100, search = "", filters = [] } = opts;

  const productQuery = getTestRunDetailColumnQuery(executionId, page - 1, search, filters, limit);
  const query = useQuery({
    ...productQuery,
    // The product's query key omits `pageSize`; this view requests a different
    // limit than the product grid, so fold it into the key to avoid the two
    // serving each other a wrong-sized page from cache.
    queryKey: [...productQuery.queryKey, limit],
    enabled: !!executionId,
    select: (res) => res.data,
  });

  const data = query.data;
  const { tasks, columns, count } = useMemo(() => {
    if (!data) return { tasks: [], columns: [], count: 0 };
    const columnOrder = data.column_order ?? [];
    const evalColumns = columnOrder.filter((c) => c.type === "evaluation");
    const rows = (data.results ?? []).map((r) => mapCallRow(r, evalColumns));
    return {
      tasks: rows,
      columns: buildTraceColumns(columnOrder),
      count: data.count ?? rows.length,
    };
  }, [data]);

  return { tasks, columns, count, isLoading: !!executionId && query.isPending };
}
