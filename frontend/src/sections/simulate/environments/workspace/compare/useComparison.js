import { useMemo } from "react";
import { useQueries } from "@tanstack/react-query";
import {
  fetchRunCalls,
  mapCallRow,
} from "src/api/simulate-environments/runCalls";

// The most calls read per run, and the page size the calls API allows.
export const CALL_CAP = 2000;
export const CALLS_PAGE_SIZE = 500;
const MAX_PAGES = Math.ceil(CALL_CAP / CALLS_PAGE_SIZE);

/**
 * Every call of one run, page by page, up to CALL_CAP. Each page is mapped
 * with its own evaluation columns, the way the run's table reads it.
 * @param {string} executionId
 * @returns {Promise<{ tasks: Array, evalColumns: Array, count: number, capped: boolean }>}
 */
export async function fetchAllRunCalls(executionId) {
  const tasks = [];
  const columns = new Map();
  let count = 0;
  let totalPages = 1;
  for (let page = 1; page <= Math.min(totalPages, MAX_PAGES); page += 1) {
    // eslint-disable-next-line no-await-in-loop
    const data = await fetchRunCalls(executionId, {
      page,
      page_size: CALLS_PAGE_SIZE,
      search: "",
      filters: JSON.stringify({}),
    });
    const evalColumns = data?.evaluation_columns ?? [];
    evalColumns.forEach((col) => columns.set(col.id, col));
    tasks.push(...(data?.results ?? []).map((r) => mapCallRow(r, evalColumns)));
    if (page === 1) {
      count = data?.count ?? tasks.length;
      totalPages = data?.total_pages ?? 1;
    }
  }
  return {
    tasks: tasks.slice(0, CALL_CAP),
    evalColumns: [...columns.values()],
    count,
    capped: count > CALL_CAP,
  };
}

// The scenario a call ran: its harness key, else the scenario name the run's
// table shows for an untagged call.
const scenarioKeyOf = (task) => task.sourceScenarioKey ?? task.sourceScenario;

const VOTING = new Set(["passed", "failed", "flaky"]);
const LIVE = new Set(["queued", "in_progress"]);

/**
 * One run's verdict on one scenario, over its trials. Only passed and failed
 * calls are measured; a scenario that passed some trials and failed others is
 * flaky, and one with no measured call has no verdict.
 */
export function verdictOf(trials = []) {
  if (!trials.length) {
    return { status: "missing", passes: 0, measured: 0, calls: 0, share: null };
  }
  const measured = trials.filter(
    (t) => t.status === "passed" || t.status === "failed",
  );
  const passes = measured.filter((t) => t.status === "passed").length;
  let status;
  if (measured.length === 0) {
    if (trials.some((t) => LIVE.has(t.status))) status = "in_progress";
    else if (trials.some((t) => t.status === "error")) status = "error";
    else status = "unmeasured";
  } else if (passes === measured.length) status = "passed";
  else if (passes === 0) status = "failed";
  else status = "flaky";
  return {
    status,
    passes,
    measured: measured.length,
    calls: trials.length,
    share: measured.length ? passes / measured.length : null,
  };
}

const verdictWord = (verdict) => {
  if (!verdict || verdict.status === "missing") return "not run";
  if (!VOTING.has(verdict.status)) return "not measured";
  return verdict.status;
};

/** passed → failed reads as a regression; failed → passed as an improvement. */
export function flipOf(a, b) {
  const from = verdictWord(a);
  const to = verdictWord(b);
  let kind = "moved";
  if (from === to) kind = "same";
  else if (from === "passed" && (to === "failed" || to === "flaky"))
    kind = "regressed";
  else if ((from === "failed" || from === "flaky") && to === "passed")
    kind = "improved";
  return { a: from, b: to, kind, text: from === to ? from : `${from} → ${to}` };
}

// Whether a scenario pairs between the baseline and the newest run. Scenario
// versions aren't in the API, so a scenario both runs played pairs; one only
// the newer run played is new, and one only the baseline played is removed.
function pairOf(base, target) {
  const inA = base.status !== "missing";
  const inB = target.status !== "missing";
  let cls = null;
  if (inA && inB) cls = "paired";
  else if (inB) cls = "new";
  else if (inA) cls = "removed";
  return cls ? { cls, flip: flipOf(base, target) } : null;
}

const mean = (xs) => {
  const vals = xs.filter((v) => v != null);
  return vals.length ? vals.reduce((a, v) => a + v, 0) / vals.length : null;
};

// What each run scored on the whole comparison, from its calls.
function statsOf(runId, tasks, rows, common, evals) {
  const measured = tasks.filter(
    (t) => t.status === "passed" || t.status === "failed",
  );
  const shared = common
    .map((row) => row.verdicts[runId].share)
    .filter((v) => v != null);
  const evalPassRates = Object.fromEntries(
    evals.map((e) => {
      const scored = tasks
        .map((t) => t.evalResults.find((r) => r.id === e.id))
        .filter((r) => r && r.passed != null);
      return [
        e.id,
        scored.length
          ? Math.round(
              (scored.filter((r) => r.passed).length / scored.length) * 100,
            )
          : null,
      ];
    }),
  );
  const pct = (v) => (v == null ? null : Math.round(v * 100));
  return {
    calls: tasks.length,
    passRate: pct(
      measured.length
        ? measured.filter((t) => t.status === "passed").length / measured.length
        : null,
    ),
    sharedPassRate: pct(mean(shared)),
    flaky: rows.filter((row) => row.verdicts[runId].status === "flaky").length,
    avgDurationMs: mean(tasks.map((t) => t.durationMs)),
    tokens: tasks.some((t) => t.tokens != null)
      ? tasks.reduce((a, t) => a + (t.tokens || 0), 0)
      : null,
    avgCsat: mean(tasks.map((t) => t.csat)),
    evalPassRates,
  };
}

/**
 * The comparison, built from each run's calls. Pure.
 *
 * Rows are the union of the scenarios the runs played, matched on the
 * scenario key; each run's calls for a scenario line up by trial. The first
 * run is the baseline and the last is the one read against it.
 * @param {string[]} runIds
 * @param {Object<string, {tasks: Array, evalColumns: Array}>} callsByRun
 */
export function buildComparison(runIds, callsByRun) {
  const byKey = new Map();
  runIds.forEach((runId) => {
    (callsByRun[runId]?.tasks ?? []).forEach((task) => {
      const key = scenarioKeyOf(task);
      if (!byKey.has(key)) {
        byKey.set(key, {
          id: key,
          scenarioKey: key,
          title: key,
          goal: task.goal ?? null,
          persona: task.persona ?? null,
          personaDetails: task.personaDetails ?? null,
          scenarioDetails: task.scenarioDetails ?? null,
          cells: Object.fromEntries(runIds.map((id) => [id, []])),
        });
      }
      byKey.get(key).cells[runId].push(task);
    });
  });

  const baseId = runIds[0];
  const targetId = runIds[runIds.length - 1];
  const rows = [...byKey.values()].map((row) => {
    Object.values(row.cells).forEach((trials) =>
      trials.sort((a, b) => (a.trialIndex ?? 0) - (b.trialIndex ?? 0)),
    );
    const verdicts = Object.fromEntries(
      runIds.map((id) => [id, verdictOf(row.cells[id])]),
    );
    const voting = new Set(
      Object.values(verdicts)
        .map((v) => v.status)
        .filter((s) => VOTING.has(s)),
    );
    return {
      ...row,
      verdicts,
      pair:
        runIds.length > 1 ? pairOf(verdicts[baseId], verdicts[targetId]) : null,
      changed: voting.size > 1,
      flaky: Object.values(verdicts).some((v) => v.status === "flaky"),
      missing: Object.values(verdicts).some((v) => v.status === "missing"),
      unmeasured: Object.values(verdicts).some(
        (v) => v.calls > 0 && v.measured === 0,
      ),
    };
  });

  const evalsById = new Map();
  runIds.forEach((id) =>
    (callsByRun[id]?.evalColumns ?? [])
      .filter((col) => col.kind !== "sub_goal")
      .forEach((col) =>
        evalsById.set(col.id, { id: col.id, name: col.name || col.id }),
      ),
  );
  const evals = [...evalsById.values()];

  const shared = rows.filter((r) => !r.missing);
  const common = shared.filter((r) =>
    Object.values(r.verdicts).every((v) => v.measured > 0),
  );
  const runStats = Object.fromEntries(
    runIds.map((id) => [
      id,
      statsOf(id, callsByRun[id]?.tasks ?? [], rows, common, evals),
    ]),
  );
  const paired = rows.filter((r) => r.pair?.cls === "paired");

  return {
    rows,
    evals,
    runStats,
    flips: {
      fixed: paired.filter((r) => r.pair.flip.kind === "improved").length,
      regressed: paired.filter((r) => r.pair.flip.kind === "regressed").length,
    },
    changed: rows.filter((r) => r.changed).length,
    coverage: {
      total: rows.length,
      shared: shared.length,
      common: common.length,
      partial: rows.length - shared.length,
      unmeasured: rows.filter((r) => r.unmeasured).length,
    },
  };
}

/**
 * The runs in `runIds`, compared scenario by scenario from their real calls
 * (every page, up to CALL_CAP per run).
 * @param {string} envId
 * @param {string[]} runIds  Baseline first.
 */
export function useComparison(envId, runIds) {
  const queries = useQueries({
    queries: runIds.map((runId) => ({
      queryKey: ["environment-compare-calls", envId, runId],
      queryFn: () => fetchAllRunCalls(runId),
      staleTime: 1000 * 60,
    })),
  });

  const settled = queries.every((q) => q.data || q.error);
  const dataKey = queries.map((q) => q.dataUpdatedAt).join(",");
  const comparison = useMemo(() => {
    const callsByRun = Object.fromEntries(
      runIds.map((id, i) => [id, queries[i]?.data]),
    );
    return buildComparison(runIds, callsByRun);
    // `queries` is a new array every render; its data changes with dataKey.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runIds, dataKey]);

  const cappedRuns = runIds
    .map((runId, i) => ({ runId, count: queries[i]?.data?.count ?? 0 }))
    .filter((_, i) => queries[i]?.data?.capped);

  return {
    ...comparison,
    capped: cappedRuns.length > 0,
    cappedRuns,
    isLoading: !settled,
    error: queries.find((q) => q.error)?.error ?? null,
    refetch: () => queries.forEach((q) => q.refetch()),
  };
}
