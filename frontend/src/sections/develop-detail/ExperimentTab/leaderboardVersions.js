// MUST 7 (R4-SPEC D22, D26): the leaderboard's versions and
// numbers. Pure. Every number comes from F's runNumbers.js (runRate,
// compareRuns, costPerSolved, bestOf over EVERY run) and every name from
// provenanceModel's agentVersionOf / latestVsPrevious, so the leaderboard,
// the Compare chain and the Matrix print the same text for the same pair.
// Sentence templates hold no digit literals (§C10).
import {
  agentVersionOf,
  latestVsPrevious,
} from "src/sections/common/Runs/provenanceModel";
import {
  bestOf,
  bestValueOf,
  bestValueText,
  compareRuns,
  costPerSolved,
  diffRange95,
  runRate,
  runsOfMatrix,
} from "src/sections/common/Runs/runNumbers";
import { passHatK } from "src/sections/common/Analysis/runStats";
import { reduceRun } from "src/sections/common/Analysis/trialReducers";
import { EMPTY, fmtPts } from "src/sections/common/format/fmt";
import { VOCAB, plural } from "src/sections/common/vocab";
import { isUnrankedRun } from "./leaderboardModel";

/** `Support Agent v3.1 (draft)` (+ `#2G` kept apart), or null. */
export function identityOf(run) {
  const v = agentVersionOf(run);
  if (!v) return null;
  return { ...v, title: `${v.name} ${v.text}`.trim() };
}

/** When a run last changed (finished, else updated, else created). */
export const updatedOf = (run) =>
  run?.completed_at ?? run?.updated_at ?? run?.created_at ?? null;

/**
 * A run's success rate for the table: the leaderboard's one numbers object
 * (runRate over the matrix) when it has the run, else the server's
 * `success_rate`. `{passed, total, rate, text, note}` or null.
 */
export function successFrom(numbers, run) {
  const r = numbers?.byRun?.[run?.run_id]?.rate;
  if (r && r.n > 0 && r.rate != null)
    return {
      passed: r.k,
      total: r.n,
      rate: r.rate,
      text: r.text,
      note: r.note,
    };
  const s = run?.success_rate;
  if (!s || !(Number(s.total) > 0)) return null;
  const passed = s.passed ?? 0;
  return {
    passed,
    total: s.total,
    rate: passed / s.total,
    text: null,
    note: null,
  };
}

/** `Support Agent v3.1 (draft)` or null (no agent version on this run). */
export const runTitleOf = (run) => identityOf(run)?.title ?? null;

/**
 * A run whose target reports no cost (an HTTP endpoint, an upload without
 * cost) is never "Best value": its matrix cells carry 0.
 */
export const costReported = (run) => Number(run?.metrics?.cost_usd) > 0;

const labelsOf = (runs) =>
  Object.fromEntries((runs ?? []).map((r) => [r.run_id, r.label]));

/** The version before `run`'s in its agent's line (null when first). */
function previousVersionRun(run, runs) {
  const me = agentVersionOf(run);
  if (!me) return null;
  const cmp = (a, b) => {
    const n = Math.max(a.length, b.length);
    for (let i = 0; i < n; i += 1) {
      const d = (a[i] ?? -1) - (b[i] ?? -1);
      if (d) return d;
    }
    return 0;
  };
  let best = null;
  (runs ?? []).forEach((r) => {
    if (r.run_id === run.run_id || r.listed === false) return;
    const v = agentVersionOf(r);
    if (!v || v.agentKey !== me.agentKey || v.version === me.version) return;
    if (cmp(v.order, me.order) >= 0) return;
    if (!best || cmp(v.order, best.v.order) > 0) best = { run: r, v };
  });
  return best;
}

/**
 * IT-R4 G2 P5: the run of the same agent made just before `run` (another
 * version, or the same version on an earlier dataset version) →
 * `{run, kind: "version"|"run"}`, or null.
 */
export function earlierRunOfAgent(run, runs) {
  const prev = previousVersionRun(run, runs);
  if (prev) return { run: prev.run, kind: "version" };
  const me = agentVersionOf(run);
  if (!me) return null;
  const earlier = (runs ?? [])
    .filter(
      (r) =>
        r.run_id !== run.run_id &&
        r.listed !== false &&
        agentVersionOf(r)?.agentKey === me.agentKey &&
        String(r.created_at ?? "") < String(run.created_at ?? ""),
    )
    .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
  return earlier.length ? { run: earlier[0], kind: "run" } : null;
}

/**
 * Everything the leaderboard reads, from the runs and their matrix.
 * @param {object[]} runs  every run on the dataset (never a filtered list)
 * @param {object|null} matrix  R3 `shape=matrix` for those runs
 * @param {{noun?: string}} [o]
 */
export function leaderboardNumbers(runs, matrix, { noun = "task" } = {}) {
  const all = (runs ?? []).filter((r) => r && r.listed !== false);
  const labels = labelsOf(all);
  const haveMatrix = Boolean(matrix?.rows?.length);
  const byRun = {};
  if (haveMatrix)
    all.forEach((r) => {
      byRun[r.run_id] = {
        rate: runRate({ matrix, runId: r.run_id }),
        value: costReported(r)
          ? costPerSolved({ matrix, runId: r.run_id }, { noun })
          : { usd: null, solved: null, text: `${EMPTY} · cost not reported` },
        passK: passHatK(
          (matrix.rows ?? [])
            .map((row) => row.cells?.[r.run_id])
            .filter(Boolean)
            .map((c) => (Array.isArray(c) ? c : c.trials ?? [])),
          Math.max(1, Number(r.trials) || 1),
        ),
        // W1-8: the run's own reducer (Review "Score each row from its
        // tries"), over the same tries, with its 95% interval.
        ...(r.trial_reducer
          ? {
              own: reduceRun(
                (matrix.rows ?? [])
                  .map((row) => row.cells?.[r.run_id])
                  .filter(Boolean)
                  .map((c) => (Array.isArray(c) ? c : c.trials ?? [])),
                {
                  ...r.trial_reducer,
                  k: Math.max(1, Number(r.trials) || 1),
                },
              ),
            }
          : {}),
      };
    });
  const pair = latestVsPrevious(all);
  const chips = {};
  let headlineCmp = null;
  if (pair) {
    chips[pair.latest.run_id] = VOCAB.LATEST_VERSION;
    chips[pair.previous.run_id] = VOCAB.PREVIOUS_VERSION;
    if (haveMatrix)
      headlineCmp = compareRuns({
        matrix,
        baselineId: pair.previous.run_id,
        candidateId: pair.latest.run_id,
        scope: "all",
        labels,
        noun,
      });
  }
  const worse = {};
  if (haveMatrix)
    all.forEach((r) => {
      const prev = previousVersionRun(r, all);
      if (!prev) return;
      const c = compareRuns({
        matrix,
        baselineId: prev.run.run_id,
        candidateId: r.run_id,
        scope: "all",
        labels,
        noun,
      });
      // IT-R1 G2 P2 / IT-R2 G2 P5: name what it regressed from, as the
      // previous version (never `baseline`): `▼ −1.1 pts vs #1B`; the
      // tooltip `#2B vs #1B (previous version): −1.1 pts · 6 tasks worse`.
      if (c.deltaPts != null && c.deltaPts < 0 && c.worse > 0)
        worse[r.run_id] = {
          n: c.worse,
          text: `▼ ${fmtPts(c.deltaPts)} vs ${prev.run.label}`,
          long: `${r.label} vs ${prev.run.label} ${prev.v.text} (previous version): ${fmtPts(c.deltaPts)} · ${plural(c.worse, noun)} worse`,
          previous: prev.run,
        };
    });
  // IT-R4 G2 P5: each run's earlier run of the same agent, with the pair's
  // numbers (the baseline row's `vs #1B (previous version)` line and the
  // `Regressed −8.2 pts vs #1B` chip read them).
  const prevOf = {};
  if (haveMatrix)
    all.forEach((r) => {
      const prev = earlierRunOfAgent(r, all);
      if (!prev) return;
      const c = compareRuns({
        matrix,
        baselineId: prev.run.run_id,
        candidateId: r.run_id,
        scope: "all",
        labels,
        noun,
      });
      const both = c.rows.filter((x) => x.change !== "not_run").length;
      if (!both) return;
      const range = diffRange95(c.candidate.rate, c.baseline.rate, both);
      prevOf[r.run_id] = {
        previous: prev.run,
        kind: prev.kind,
        cand: c.candidate.rate,
        base: c.baseline.rate,
        deltaPts: c.deltaPts,
        n: both,
        worse: c.worse,
        worseIds: c.worseIds,
        regressed: Boolean(range?.real) && (c.deltaPts ?? 0) < 0,
      };
    });
  // IT-R5 G2 P6: a text-only voice run is not ranked (best / cheapest).
  const pool = haveMatrix
    ? runsOfMatrix(matrix, {
        labels,
        runIds: all.filter((r) => !isUnrankedRun(r)).map((r) => r.run_id),
      })
    : [];
  const bestValue = haveMatrix
    ? bestValueOf(
        pool.filter((p) => costReported(all.find((r) => r.run_id === p.id))),
        { noun },
      )
    : null;
  return {
    byRun,
    pair,
    chips,
    headlineCmp,
    worse,
    prevOf,
    best: haveMatrix ? bestOf(pool, { by: "success", noun }) : null,
    // IT-R2 G2 P4: Best value has a quality floor (runNumbers.bestValueOf):
    // the cheapest run that solves little is named `cheapest`, apart.
    bestValue,
    bestValueText: bestValueText(bestValue, { noun }),
    total: all.length,
  };
}

/**
 * The version headline (D26):
 *   `Support Agent v3.1 (draft) vs v3: +20.0 pts on all 30 tasks · 24 carried over from #2B`
 *   `Only one version of Support Agent ran here (v3).`
 * @returns {{text, primary: {label, kind: "pair"|"best"}}|null}
 */
export function versionHeadline(n, runs, { baselineRun } = {}) {
  if (n?.pair) {
    const latest = identityOf(n.pair.latest);
    const prev = identityOf(n.pair.previous);
    const delta = n.headlineCmp?.deltaPts;
    const scope = n.headlineCmp?.scopeText;
    return {
      text: `${latest.title} vs ${prev.text}${delta != null ? `: ${fmtPts(delta)}${scope ? ` ${scope}` : ""}` : ""}`,
      primary: { label: "Compare latest vs previous", kind: "pair" },
    };
  }
  const single = (runs ?? [])
    .filter((r) => r && r.listed !== false)
    .map(identityOf)
    .filter(Boolean);
  if (!single.length) return null;
  const byAgent = new Map();
  single.forEach((v) =>
    byAgent.set(v.agentKey, [...(byAgent.get(v.agentKey) ?? []), v]),
  );
  // The agent with the most runs; on a tie, the baseline's agent.
  const baseKey = identityOf(baselineRun)?.agentKey ?? null;
  const [, list] = [...byAgent.entries()].sort(
    (a, b) =>
      b[1].length - a[1].length ||
      (b[0] === baseKey ? 1 : 0) - (a[0] === baseKey ? 1 : 0),
  )[0];
  return {
    text: `Only one version of ${list[0].name} ran here (${list[0].text}).`,
    primary: { label: "Compare the best run with the baseline", kind: "best" },
  };
}

/**
 * `Best overall: #2D 91.0% (81/89) · Best value: #2A $0.0075 per solved
 * task`; with a search or filter on: `Best overall (all 7 runs): #2D … (the
 * filter hides 6)`.
 */
export function bestLine(n, { shown, filtered } = {}) {
  if (!n?.best) return null;
  const hidden = filtered ? Math.max(0, n.total - (shown ?? n.total)) : 0;
  // IT-R1 G2 P1: `Best overall` (every run on the dataset); a group row
  // says `best in #2`, so one word never names two scopes.
  const head = filtered
    ? `Best overall (all ${plural(n.total, "run")})`
    : "Best overall";
  const parts = [
    `${head}: ${n.best.label ?? ""} ${n.best.text}`.replace(/\s+/g, " "),
  ];
  if (n.bestValue)
    parts.push(
      `${VOCAB.BEST_VALUE}: ${n.bestValue.label ?? ""} ${n.bestValue.text}`.replace(
        /\s+/g,
        " ",
      ),
    );
  return `${parts.join(" · ")}${hidden ? ` (the filter hides ${hidden})` : ""}`;
}

/**
 * Merge matrix reads of disjoint run sets into one matrix: rows by id
 * (row_id), cells and columns combined.
 */
export function mergeMatrices(parts) {
  const rows = new Map();
  const columns = {};
  (parts ?? []).forEach((m) => {
    Object.assign(columns, m?.columns ?? {});
    (m?.rows ?? []).forEach((row) => {
      const id = row.id ?? row.row_id;
      const cur = rows.get(id);
      if (cur) cur.cells = { ...cur.cells, ...(row.cells ?? {}) };
      else rows.set(id, { ...row, cells: { ...(row.cells ?? {}) } });
    });
  });
  return { ...(parts?.[0] ?? {}), rows: [...rows.values()], columns };
}
