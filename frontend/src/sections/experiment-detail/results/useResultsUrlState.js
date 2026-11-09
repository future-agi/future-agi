import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { parsePills } from "src/sections/common/Results/filterPills";
import { readR2Params } from "src/sections/common/Analysis/analysisLinks";

// URL state of the results surface (PRODUCT-SPEC §2.3). Replace is the
// default; opening a row pushes so Back closes it; J/K replace.
//   runs      compare page only: run ids (≤ 8)
//   baseline  run id deltas are measured against
//   diff      off | baseline | expected
//   agg       avg | median | pass | na_rate | mode | max | pass_at_k |
//             pass_hat_k | at_least:<n> (W1-8 trial reducers)
//   show      all | regressed | improved | changed
//   filters   pill JSON (§3.10.5)
//   sort      §3.10.6 sort string
//   row, run, dtab, trial   the result drawer
// R2 (read through readR2Params; the keys above keep their meaning):
//   vs        the drawer's right-hand trace: `<runId>`, `<runId>@<trial>`,
//             `none` (one trace) or `production`
//   group     Matrix rows grouping (`category`, `meta:<key>`, …)
//   matrixSort, matrixShow   the Matrix's reading of `sort` / `show`
export const DIFF_MODES = ["off", "baseline", "expected"];
export const AGG_MODES = [
  "avg",
  "median",
  "pass",
  "na_rate",
  // W1-8: trial reducers (`at_least:<n>` too, aggModeOf); `run` = each
  // run's own reducer (the default).
  "run",
  "mode",
  "max",
  "pass_at_k",
  "pass_hat_k",
];

/**
 * W1-8: an `agg=` value the page reads (pure). No `agg=` is `run`: each
 * run's own reducer (Review "Score each row from its tries"), which is the
 * Average for a run that set none.
 */
export const aggModeOf = (value) =>
  value == null || value === ""
    ? "run"
    : AGG_MODES.includes(value) || /^at_least:\d+$/.test(String(value))
      ? value
      : "avg";
export const SHOW_MODES = ["all", "regressed", "improved", "changed"];
export const MAX_COMPARE_RUNS = 8;

/**
 * Baseline on the experiment page (R3 MUST 3): `baseline=` when it is one of
 * the experiment's runs, else the dataset's pinned baseline when it is one of
 * them (TS #2 → #2B), else run A.
 */
export function experimentDefaultBaseline(runIds, requested, pinnedId) {
  if (requested && runIds.includes(requested)) return requested;
  if (pinnedId && runIds.includes(pinnedId)) return pinnedId;
  return runIds[0] ?? null;
}

const pick = (value, allowed, fallback) =>
  allowed.includes(value) ? value : fallback;

/** `runs=` -> ids (unique, ≤ 8). Pure; exported for tests. */
export function parseRunsParam(value) {
  return [
    ...new Set(
      String(value ?? "")
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
    ),
  ].slice(0, MAX_COMPARE_RUNS);
}

/** Read the results view state from search params. Pure; exported for tests. */
export function readResultsState(searchParams) {
  const get = (k) => searchParams.get(k);
  const trial = Number.parseInt(get("trial"), 10);
  return {
    runIds: parseRunsParam(get("runs")),
    baseline: get("baseline") || null,
    diff: pick(get("diff"), DIFF_MODES, "off"),
    agg: aggModeOf(get("agg")),
    // IT-R5 G3 (P2): one word — the URL says `show=worse`; `regressed`
    // (older links) still reads.
    show: pick(
      get("show") === "worse" ? "regressed" : get("show"),
      SHOW_MODES,
      "all",
    ),
    pills: parsePills(get("filters")),
    sort: get("sort") || "order",
    row: get("row") || null,
    run: get("run") || null,
    dtab: get("dtab") || "compare",
    // IT-R5 G3 (P5): whether the URL names a tab (else the drawer picks:
    // Trajectory for a row that failed at a step or on a tool).
    dtabNamed: Boolean(get("dtab")),
    trial: Number.isFinite(trial) && trial > 0 ? trial : 1,
    from: get("from") || null,
    // W1-3: compare runs scored with different eval versions (opt-in).
    mixVersions: get("mix_versions") === "1",
    // R2 additive keys (readR2Params validates them).
    ...pickR2(searchParams),
  };
}

/** The R2 keys of the results surface (additive; existing keys unchanged). */
function pickR2(searchParams) {
  const r2 = readR2Params(searchParams);
  return {
    vs: r2.vs,
    group: r2.group,
    matrixSort: r2.sort,
    matrixShow: r2.show,
  };
}

/** Apply `{key: value|null}` to a query string (null, "" or false drop it). */
export function patchSearch(search, updates) {
  const next = new URLSearchParams(search);
  Object.entries(updates).forEach(([k, v]) => {
    if (k === "show" && v === "regressed") next.set(k, "worse");
    else if (v == null || v === "" || v === false) next.delete(k);
    else next.set(k, String(v));
  });
  return next;
}

export default function useResultsUrlState() {
  const [searchParams, setSearchParams] = useSearchParams();
  const state = useMemo(() => readResultsState(searchParams), [searchParams]);

  // Merge with the live URL so back-to-back writes in one tick compose.
  const patch = useCallback(
    (updates, { push = false } = {}) => {
      const next = patchSearch(window.location.search, updates);
      setSearchParams(next, { replace: !push });
    },
    [setSearchParams],
  );

  return { ...state, patch };
}
