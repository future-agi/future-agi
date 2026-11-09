// One candidate per page (R4-SPEC D26, lane X 8a). Pure. The compare and
// experiment pages pick ONE run they are about; the header, What changed,
// verdicts, worklist, Review `Start here`, `Choose winner` and the Summary
// follow it through `cand=`.
//
//   pageCandidate({runs, runIds, baselineId, cand, fallback}) → {id, why}
//     cand= (valid) › the latest version of the agent with the most runs
//     (when its previous version is the baseline or in view) › `fallback`
//     (the analysis' best verdict) › the first other run. A text-only voice
//     run is not a default candidate against a full-voice baseline.
//   defaultCompare(runs) → {runIds, baselineId, cand} | null  (no `runs=`)
//   versionTags(runs) → {[runId]: {text, role}}  (`v3.1 (draft)`, `Latest version`)
import {
  agentVersionOf,
  latestVsPrevious,
} from "src/sections/common/Runs/provenanceModel";
import { stripCandidate } from "src/sections/common/Runs/configDiff";
import { VOCAB } from "src/sections/common/vocab";

const idOf = (r) => r?.run_id ?? r?.id ?? null;
const depthOf = (r) => r?.config?.voice?.depth ?? null;

/** Latest vs previous version among some runs (ids), or null. */
export function versionPair(runs) {
  const lvp = latestVsPrevious(runs ?? []);
  if (!lvp) return null;
  return {
    latest: idOf(lvp.latest),
    previous: idOf(lvp.previous),
    latestVersion: lvp.latestVersion,
    previousVersion: lvp.previousVersion,
  };
}

/**
 * @param {{runs: object[], runIds: string[], baselineId?: string|null,
 *   cand?: string|null, fallback?: string|null}} args  `runs` = R1 runs
 *   with `config` (any superset of the runs in view). `holdSubject`
 *   (IT-R4 G3 P2): with `cand` = the baseline, no candidate (`why:
 *   "subject"`) instead of a pick of its own.
 * @returns {{id: string|null, why: "picked"|"latest"|"best"|"first"|null}}
 */
export function pageCandidate({
  runs,
  runIds,
  baselineId,
  cand,
  fallback,
  holdSubject = false,
}) {
  const ids = runIds ?? [];
  // IT-R4 G3 (P2): a fix loop on the baseline itself (`cand` = `baseline`,
  // `compared=` set by Fix this step) keeps its subject: no other run
  // becomes the page's candidate behind the dock.
  if (holdSubject && cand && cand === baselineId)
    return { id: null, why: "subject" };
  const ok = (id) => Boolean(id) && id !== baselineId && ids.includes(id);
  if (ok(cand)) return { id: cand, why: "picked" };
  const inView = (runs ?? []).filter((r) => ids.includes(idOf(r)));
  const base = inView.find((r) => idOf(r) === baselineId);
  const comparable = (id) => {
    const r = inView.find((x) => idOf(x) === id);
    return !(depthOf(base) === "full_voice" && depthOf(r) === "text_only");
  };
  const pair = versionPair(inView);
  if (pair && ok(pair.latest) && comparable(pair.latest))
    return { id: pair.latest, why: "latest" };
  if (ok(fallback) && comparable(fallback))
    return { id: fallback, why: "best" };
  const first = ids.find((id) => ok(id) && comparable(id)) ?? ids.find(ok);
  return { id: first ?? null, why: first ? "first" : null };
}

/**
 * The comparison a compare page opens with when the link names no runs:
 * the agent's previous version (baseline) vs its latest.
 */
export function defaultCompare(runs) {
  const listed = (runs ?? []).filter((r) => r?.listed !== false);
  const pair = versionPair(listed);
  if (!pair) return null;
  return {
    runIds: [pair.previous, pair.latest],
    baselineId: pair.previous,
    cand: pair.latest,
  };
}

/**
 * Version chips: `{text: "v3.1 (draft)", role: "Latest version"}` for the
 * latest / previous pair, `{text, role: null}` for other versioned runs.
 */
export function versionTags(runs) {
  const out = {};
  (runs ?? []).forEach((r) => {
    const v = agentVersionOf(r);
    if (v) out[idOf(r)] = { text: v.text, name: v.name, role: null };
  });
  const pair = versionPair(runs);
  if (pair) {
    if (out[pair.latest]) out[pair.latest].role = VOCAB.LATEST_VERSION;
    if (out[pair.previous]) out[pair.previous].role = VOCAB.PREVIOUS_VERSION;
  }
  return out;
}

/**
 * The candidate the page header shows (iterate r2 G3 P2, rule 12), as the
 * experiment / compare wrappers pick it: experiment = `cand=` › the open
 * row's run (`run=`) › the page candidate; compare = the page candidate
 * (which already holds `cand=`) › `run=`. `pageCand` = pageCandidate's id.
 */
export function headerCandidate({
  mode,
  cand,
  run,
  pageCand,
  baselineId,
  runIds,
}) {
  return stripCandidate({
    cand: mode === "compare" ? pageCand : cand,
    focused: run ?? pageCand,
    baselineId,
    runIds: runIds ?? [],
  });
}

/**
 * iterate r3 G3 (S2): the run a `Fix this step` link says the subject was
 * compared with (`compared=`), as a chip only — never a candidate or a
 * baseline. Null unless it is one of `runIds` and neither the baseline
 * nor `subjectId`.
 */
export function comparedChipOf({ search, runIds, baselineId, subjectId }) {
  let id = null;
  try {
    id = new URLSearchParams(search ?? "").get("compared");
  } catch {
    return null;
  }
  if (!id || id === baselineId || id === subjectId) return null;
  return (runIds ?? []).includes(id) ? id : null;
}
