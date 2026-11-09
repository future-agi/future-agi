// Agent runs, built from the request logs that carried a run id (the
// x-agentcc-run-id header). There is no runs endpoint: the list groups the
// newest requests by run, and a run's page reads every request with its id
// through the logs API's run_id filter.

import { costTotal, isUnpriced } from "../utils/formatters";

const text = (value) => (value == null ? "" : String(value).trim());

const num = (value) => {
  const n = Number(value);
  return value != null && value !== "" && Number.isFinite(n) ? n : 0;
};

const time = (iso) => {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isFinite(t) ? t : null;
};

const isError = (row) => Boolean(row.is_error) || num(row.status_code) >= 400;

function pushUnique(list, value) {
  const v = text(value);
  if (v && !list.includes(v)) list.push(v);
}

function oldestFirst(rows) {
  return [...rows].sort(
    (a, b) => (time(a.started_at) ?? 0) - (time(b.started_at) ?? 0),
  );
}

function span(rows) {
  let start = null;
  let end = null;
  rows.forEach((row) => {
    const t = time(row.started_at);
    if (t == null) return;
    start = start == null ? t : Math.min(start, t);
    end = Math.max(end ?? t, t + num(row.latency_ms));
  });
  return { start, end, durationMs: start == null ? 0 : end - start };
}

// `unpriced` counts the calls the gateway found no price for: each is
// stored at 0, so a cost over them is a floor or unknown, never free
//.
function totals(rows) {
  return rows.reduce(
    (acc, row) => {
      acc.requests += 1;
      acc.errors += isError(row) ? 1 : 0;
      acc.unpriced += isUnpriced(row) ? 1 : 0;
      acc.cost += num(row.cost);
      acc.tokens += num(row.total_tokens);
      return acc;
    },
    { requests: 0, errors: 0, unpriced: 0, cost: 0, tokens: 0 },
  );
}

/** A run's or a group's cost as read: { text, title, unknown } (costTotal). */
export function runCost(totalsRow, { cell = false } = {}) {
  return costTotal(totalsRow?.cost, {
    unpriced: totalsRow?.unpriced,
    requests: totalsRow?.requests,
    cell,
  });
}

/** Runs in these requests, newest first. */
export function groupRuns(rows) {
  const byRun = new Map();
  (Array.isArray(rows) ? rows : []).forEach((row) => {
    const runId = text(row.agent_run_id);
    if (!runId) return;
    if (!byRun.has(runId)) byRun.set(runId, []);
    byRun.get(runId).push(row);
  });

  const runs = [...byRun.entries()].map(([runId, calls]) => {
    const ordered = oldestFirst(calls);
    const steps = [];
    const models = [];
    let agentId = "";
    let workflow = "";
    let env = "";
    let parentRunId = "";
    ordered.forEach((row) => {
      pushUnique(steps, row.agent_step);
      pushUnique(models, row.model);
      agentId = agentId || text(row.agent_id);
      workflow = workflow || text(row.agent_workflow);
      env = env || text(row.agent_env);
      parentRunId = parentRunId || text(row.agent_parent_run_id);
    });
    const { start, end, durationMs } = span(ordered);
    return {
      runId,
      agentId,
      workflow,
      env,
      parentRunId,
      steps,
      models,
      ...totals(ordered),
      startedAt: ordered[0]?.started_at ?? null,
      lastAt: ordered[ordered.length - 1]?.started_at ?? null,
      durationMs,
      _sort: end ?? start ?? 0,
    };
  });

  return runs
    .sort((a, b) => (time(b.lastAt) ?? 0) - (time(a.lastAt) ?? 0))
    .map(({ _sort, ...run }) => run);
}

function breakdown(rows, keyOf) {
  const groups = new Map();
  rows.forEach((row) => {
    const key = text(keyOf(row)) || "Not set";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  });
  return [...groups.entries()].map(([key, group]) => ({
    key,
    ...totals(group),
    latencyMs: group.reduce((sum, row) => sum + num(row.latency_ms), 0),
  }));
}

/** One run's calls on its own time axis, with step and model breakdowns. */
export function summarizeRun(rows) {
  const calls = oldestFirst(Array.isArray(rows) ? rows : []);
  const { start, durationMs } = span(calls);
  const first = (key) =>
    calls.map((row) => text(row[key])).find(Boolean) || null;
  return {
    ...totals(calls),
    agentId: first("agent_id"),
    workflow: first("agent_workflow"),
    env: first("agent_env"),
    framework: first("agent_framework"),
    parentRunId: first("agent_parent_run_id"),
    rootRunId: first("agent_root_run_id"),
    startedAt: calls[0]?.started_at ?? null,
    totalMs: durationMs,
    calls: calls.map((row) => {
      const t = time(row.started_at);
      return {
        ...row,
        offsetMs: start == null || t == null ? null : t - start,
        durationMs: row.latency_ms == null ? null : num(row.latency_ms),
      };
    }),
    bySteps: breakdown(calls, (row) => row.agent_step),
    byModels: breakdown(calls, (row) => row.model),
  };
}
