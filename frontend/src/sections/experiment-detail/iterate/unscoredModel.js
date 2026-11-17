// W1-9 B6 · rows a fix run left unscored (pure). A row is unscored when an
// eval of the run has no score on it: skipped (`no expected value`), N/A
// (the judge said it does not apply) or a score error (§5 cell statuses).
// The banner says it in one line, with a link to exactly those rows:
//   `2 rows not scored · Correctness skipped (no expected value)`
// Carried rows are the parent's results, not this run's, so they are left
// out. "Not reached" checks (an earlier check failed) are a verdict, not a
// missing score.

import { primaryPassMetric } from "src/sections/experiment-detail/results/resultsSimple";

const WORD = { skipped: "skipped", na: "N/A", error: "score error" };

const statusOf = (s) => {
  if (!s || s.value != null) return null;
  if (s.status === "na") return "na";
  if (s.status === "error") return "error";
  if (s.status === "skipped") return "skipped";
  return null;
};

/**
 * @param {object[]} rows  R3 rows (`example_id`, `results[runId].scores`)
 * @param {string} runId
 * @param {{carried?: Set<string>, nameOf?: (key: string) => string,
 *   leftOut?: string[]}} [opts]  `leftOut` = rows the verdict leaves out
 *   (verdictLeftOut, the ones its counts would have read), said at the end:
 *   `· 20 left out of the verdict`
 * @returns {{rowIds: string[], linkIds: string[], groups: {key: string,
 *   name: string, status: string, reason: string|null, rows: number}[],
 *   text: string|null}}  `linkIds` = the rows the line opens (not scored
 *   here, or left out of the verdict)
 */
export function unscoredOf(
  rows,
  runId,
  { carried = new Set(), nameOf, leftOut = [] } = {},
) {
  const rowIds = [];
  const groups = new Map();
  (rows ?? []).forEach((row) => {
    const id = row?.example_id;
    if (!id || carried.has(id)) return;
    const scores = row?.results?.[runId]?.scores ?? {};
    let missing = false;
    Object.entries(scores).forEach(([key, s]) => {
      const status = statusOf(s);
      if (!status) return;
      missing = true;
      const reason = s.skip_reason ?? s.reason ?? null;
      const gk = `${key}|${status}|${reason ?? ""}`;
      if (!groups.has(gk))
        groups.set(gk, {
          key,
          name: nameOf?.(key) ?? key,
          status,
          reason,
          rows: 0,
        });
      groups.get(gk).rows += 1;
    });
    if (missing) rowIds.push(id);
  });
  const list = [...groups.values()].sort(
    (a, b) => b.rows - a.rows || (a.name < b.name ? -1 : 1),
  );
  const n = rowIds.length;
  const k = leftOut.length;
  const text = n
    ? [
        `${n} ${n === 1 ? "row" : "rows"} not scored`,
        ...list.map(
          (g) =>
            `${g.name} ${WORD[g.status]}${g.reason ? ` (${g.reason})` : ""}${list.length > 1 ? ` on ${g.rows}` : ""}`,
        ),
        ...(k ? [`${k} left out of the verdict`] : []),
      ].join(" · ")
    : k
      ? `${k} ${k === 1 ? "row" : "rows"} left out of the verdict: no score on the baseline`
      : null;
  const linkIds = [...new Set([...rowIds, ...leftOut])];
  return { rowIds, linkIds, groups: list, text };
}

/**
 * W1-9 B6 · rows the fix verdict leaves out: a row whose
 * primary eval (the pass / fail eval the grid's verdict dot reads,
 * results/resultsSimple.primaryPassMetric, among the evals that run
 * scores) has no score on it in either run — skipped, N/A or a score
 * error. Its tries' pass / fail is then not an eval's verdict, so it is in
 * no fixed / still fail / better / worse count. A try that errored (no
 * score because it never answered) is a failure, not left out.
 * @param {{rows?: object[], summary?: {metrics?: object[]}}|null} data  R3
 *   results with both runs
 * @param {string[]} runIds
 * @returns {{rowIds: string[], keyOf: Record<string, string|null>}}
 */
export function verdictLeftOut(data, runIds) {
  const metrics = data?.summary?.metrics ?? [];
  const keyOf = Object.fromEntries(
    (runIds ?? []).map((id) => [
      id,
      primaryPassMetric(
        metrics.filter((m) => !Array.isArray(m.runs) || m.runs.includes(id)),
      )?.key ?? null,
    ]),
  );
  const rowIds = (data?.rows ?? [])
    .filter((row) =>
      (runIds ?? []).some((id) => {
        const key = keyOf[id];
        const s = key ? row?.results?.[id]?.scores?.[key] : null;
        return Boolean(s) && statusOf(s) != null && s.override?.value == null;
      }),
    )
    .map((row) => row.example_id);
  return { rowIds, keyOf };
}

/**
 * The rows a verdict leaves out that its counts would have read: a rerun's
 * verdict reads the rows the child ran (carried rows are the parent's), a
 * full run's every row both ran.
 * @param {{unscored?: string[], carried?: string[]}|null} s
 *   summarizeIteration output
 * @param {{partial?: boolean}} [opts]
 * @returns {string[]}
 */
export function leftOutOfVerdict(s, { partial = false } = {}) {
  const carried = new Set(s?.carried ?? []);
  return (s?.unscored ?? []).filter((id) => !partial || !carried.has(id));
}

/**
 * v2.1 g4 (banner:unscored-link, banner:verdict): a rerun comparison's
 * `partial` ({scored, total}, runNumbers.compareRuns) counts a try with no
 * score as still running. A try whose row's primary eval was skipped, N/A
 * or a score error on the new run never gets one, so when every try still
 * missing a score is on such a row the run is done: no `Partial · …`, the
 * verdict reads the scored tries (skipped ones leave the denominator) and
 * the unscored line links them. Pure.
 * @param {{partial?: {scored: number, total: number}|null}|null} cmp
 * @param {{rows?: object[]}|null} matrix  the pair's matrix (R3 cells)
 * @param {object[]|null} resultRows  R3 rows (`results[runId].scores`)
 * @param {string} candId
 * @param {string|null} primaryKey  the eval the verdict reads on candId
 * @returns {object|null} cmp, with `partial: null` once settled
 */
export function settleUnscoredPartial(
  cmp,
  matrix,
  resultRows,
  candId,
  primaryKey,
) {
  if (!cmp?.partial || !primaryKey) return cmp;
  const unscoredRows = new Set(
    (resultRows ?? [])
      .filter((row) => statusOf(row?.results?.[candId]?.scores?.[primaryKey]))
      .map((row) => row.example_id),
  );
  if (!unscoredRows.size) return cmp;
  let open = 0;
  (matrix?.rows ?? []).forEach((r) => {
    const cell = r.cells?.[candId];
    if (!cell || cell.carried_from) return;
    const trials = Array.isArray(cell)
      ? cell
      : Array.isArray(cell.trials)
        ? cell.trials
        : [];
    const waiting = trials.filter(
      (t) => t === null || t === undefined || t === "running",
    ).length;
    if (waiting && !unscoredRows.has(r.row_id ?? r.id)) open += waiting;
  });
  const missing = cmp.partial.total - cmp.partial.scored;
  const onUnscored = (matrix?.rows ?? []).reduce((sum, r) => {
    const cell = r.cells?.[candId];
    if (!cell || cell.carried_from || !unscoredRows.has(r.row_id ?? r.id))
      return sum;
    const trials = Array.isArray(cell)
      ? cell
      : Array.isArray(cell.trials)
        ? cell.trials
        : [];
    return (
      sum +
      trials.filter((t) => t === null || t === undefined || t === "running")
        .length
    );
  }, 0);
  if (open > 0 || onUnscored < missing) return cmp;
  return { ...cmp, partial: null };
}
