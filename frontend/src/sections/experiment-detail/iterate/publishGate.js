// K-3 (MUST-BUILD BL-06): one publish gate for the result banner's
// `Publish v3.1 and set as baseline` and the dock's `Publish v3.1`. A draft
// version is published only once its verdict is in: every try of the new
// run is scored and every row of the base run was run on the new version
// (none carried from the base, none left out). Until then the button stays
// on screen, disabled, and says why. Pure.

const plural = (n, one, many) => (n === 1 ? one : many);
const countOf = (v) =>
  Array.isArray(v) ? v.length : Math.max(0, Number(v) || 0);

/**
 * Rows of the new run whose results were carried over from the base run
 * itself (a chain's rows carried from an intermediate run count as run, as
 * the banner counts them).
 * @param {{rows?: object[]}|null} matrix  R3 `shape=matrix`
 * @param {string} candId
 * @param {string} baseLabel  `#2B`
 */
export function carriedFromBaseOf(matrix, candId, baseLabel) {
  if (!matrix || !candId || !baseLabel) return [];
  return (matrix.rows ?? []).filter(
    (r) => r.cells?.[candId]?.carried_from === baseLabel,
  );
}

/**
 * The base run's rows in the matrix and those the new run never ran (no
 * cell of its own: `Leave them out`, or a quick test's few rows). R3
 * `shape=matrix` gives a run no cell on a row it is not part of.
 * @param {{rows?: object[]}|null} matrix  R3 `shape=matrix` of base + cand
 * @param {string} baseId
 * @param {string} candId
 * @returns {{notRun: object[], baseRows: number}}
 */
export function coverageOf(matrix, baseId, candId) {
  if (!matrix || !baseId || !candId) return { notRun: [], baseRows: 0 };
  const ofBase = (matrix.rows ?? []).filter((r) => r.cells?.[baseId]);
  return {
    notRun: ofBase.filter((r) => !r.cells?.[candId]),
    baseRows: ofBase.length,
  };
}

/** `Waiting for 7 tries · Publish when the verdict is in`. */
export function waitingText(n) {
  return `Waiting for ${n} ${plural(n, "try", "tries")} · Publish when the verdict is in`;
}

/** `17 rows still come from #2B · Run them on v3.1 to publish`. */
export function carriedText(n, baseLabel, version) {
  return `${n} ${plural(n, "row still comes", "rows still come")} from ${baseLabel} · Run ${plural(n, "it", "them")} on ${version ?? "this version"} to publish`;
}

/**
 * `17 rows were left out of #2H · Publish needs every row run on v3.1`.
 * (No single action runs only the rows left out: the run has no result on
 * them to rerun from, so the reason states the rule, not a button.)
 */
export function leftOutText(n, candLabel, version) {
  return `${n} ${plural(n, "row was", "rows were")} left out of ${candLabel || "this run"} · Publish needs every row run on ${version ?? "this version"}`;
}

export const EDITORS_ONLY = "Only editors can publish";

/**
 * `A quick test ran 5 of 30 rows · Publish needs every row run on v3.1`; a
 * quick test that covered every row is still not a verdict (1 try, off the
 * leaderboard).
 */
export function quickTestText({ ran, total, version }) {
  const v = version ?? "this version";
  return Number.isFinite(ran) && Number.isFinite(total) && ran < total
    ? `A quick test ran ${ran} of ${total} ${plural(total, "row", "rows")} · Publish needs every row run on ${v}`
    : `A quick test is not a verdict · Publish needs a run of ${v} on the leaderboard`;
}

/** Quick tests and replays the dock starts (the `quickTestsOf` test). */
const isQuickTest = (run) =>
  run?.kind === "quick_test" || run?.source?.kind === "quick_test";

/**
 * @param {{rerunCmp: {partial?: {scored: number, total: number}|null}|null,
 *   carriedFromBase: object[]|number, notRun?: object[]|number,
 *   baseRows?: number, draftVersion: {draft?: boolean,
 *   version?: string}|null, cand: object|null, baseLabel: string,
 *   candLabel?: string, canEdit: boolean}} args
 *   `rerunCmp` = runNumbers.compareRuns({scope: "rerun"}) of base vs cand
 *   (the numbers of the banner's `Partial · k of N tries scored`);
 *   `carriedFromBase` (carriedFromBaseOf) and `notRun` / `baseRows`
 *   (coverageOf) come from the same matrix.
 * @returns {{show: boolean, ready: boolean, reason: string|null,
 *   state: "published"|"editor"|"waiting"|"quick"|"carried"|"left_out"
 *   |"ready", waiting: number, carried: number, notRun: number}}
 *   `show`: the banner offers Publish (a draft version, the run is not
 *   already the dataset baseline, and it is listed). `ready`: the verdict is
 *   in and the viewer may publish. `reason`: why not, else null.
 *   `published`: the version is no longer a draft (published from the
 *   banner or elsewhere), so there is nothing to publish.
 */
export function publishGateOf({
  rerunCmp,
  carriedFromBase,
  notRun: notRunRows,
  baseRows,
  draftVersion,
  cand,
  baseLabel,
  candLabel,
  canEdit,
}) {
  // v2.1 g4 (banner:publish-and-set-baseline): an agent edit never saved as
  // a version (`v3 (edited)`) has nothing to publish.
  const unsaved =
    Boolean(cand?.config?.agent?.unsaved) ||
    / \(edited\)$/.test(String(draftVersion?.version ?? ""));
  const show =
    Boolean(draftVersion?.draft) &&
    !unsaved &&
    Boolean(cand) &&
    !cand.is_dataset_baseline &&
    cand.listed !== false;
  const partial = rerunCmp?.partial ?? null;
  const waiting = partial
    ? Math.max(0, (partial.total ?? 0) - (partial.scored ?? 0))
    : 0;
  const carried = countOf(carriedFromBase);
  const notRun = countOf(notRunRows);
  const version = draftVersion?.version ?? null;
  const out = (state, reason) => ({
    show,
    ready: state === "ready",
    reason,
    state,
    waiting,
    carried,
    notRun,
  });
  if (draftVersion && draftVersion.draft === false)
    return out("published", null);
  if (!canEdit) return out("editor", EDITORS_ONLY);
  if (!cand) return out("waiting", null);
  if (partial && waiting > 0) return out("waiting", waitingText(waiting));
  if (cand.listed === false || isQuickTest(cand)) {
    const total = Number.isFinite(baseRows) && baseRows > 0 ? baseRows : null;
    return out(
      "quick",
      quickTestText({
        ran: total == null ? null : Math.max(0, total - notRun),
        total,
        version,
      }),
    );
  }
  if (carried > 0)
    return out("carried", carriedText(carried, baseLabel, version));
  if (notRun > 0)
    return out(
      "left_out",
      leftOutText(notRun, candLabel ?? cand.label, version),
    );
  return out("ready", null);
}

/**
 * The run whose verdict decides a version the dock saved: the run that
 * saved it, or its newest descendant that reran it as is (`Run the other
 * 17` / `Rerun as is`: `changes: []`), so the dock waits for the rows the
 * fix carried over, exactly like the banner of that run. Pure.
 * @param {object[]} runs  R1 `include=config,lineage` results
 * @param {string|null} runId
 * @returns {string|null}
 */
export function gateRunIdOf(runs, runId) {
  if (!runId) return null;
  const list = runs ?? [];
  const seen = new Set([runId]);
  let id = runId;
  for (;;) {
    const kids = list.filter(
      (r) =>
        r?.parent_run_id === id &&
        Array.isArray(r.changes) &&
        r.changes.length === 0 &&
        r.listed !== false &&
        !r.deleted &&
        !seen.has(r.run_id),
    );
    if (!kids.length) return id;
    kids.sort((a, b) =>
      String(b.created_at ?? "").localeCompare(String(a.created_at ?? "")),
    );
    id = kids[0].run_id;
    seen.add(id);
  }
}

/**
 * The agent version Publish activates for a run: the version its config
 * says it ran (a Test a fix child records the version it saved,
 * r2-iterate-model createIteration), else its target's. A child copies its
 * parent's target, so the target alone names the parent's version (M8: the
 * banner published v3 while the dock's draft v3.1 stayed draft).
 */
export function publishVersionIdOf(run) {
  return run?.config?.agent?.version_id ?? run?.target?.version_id ?? null;
}
