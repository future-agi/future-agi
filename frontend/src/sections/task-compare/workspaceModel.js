// Side by side · pure model: lane order and defaults, trial defaults, the
// lane verdict chip, rail filters + dots, lane metrics with Δ / × toggles,
// blind names and the verdict / link copy. No React; tested in __tests__.
import { disagreement } from "src/sections/common/Analysis/runStats";
import { TASK_COMPARE_MAX_LANES } from "src/sections/common/Analysis/analysisLinks";
import {
  fmtCost,
  fmtCostDelta,
  fmtDeltaMs,
  fmtMs,
  fmtMultiple,
  fmtTokens,
  MINUS,
} from "src/sections/common/format/fmt";

export const MAX_LANES = TASK_COMPARE_MAX_LANES;
export const SESSION_PREFIX = "session:";
export const isSessionId = (id) => String(id ?? "").startsWith(SESSION_PREFIX);

// ------------------------------------------------------------- outcomes

const SCORED = new Set(["pass", "fail", "error"]);
/** The pass/fail of a trial, reading carried trials' own outcome. */
export const effectiveOutcome = (t) =>
  t?.outcome === "carried" ? t.carried_outcome ?? "carried" : t?.outcome;

/** `pass` · `fail` · `error` · `infra` · `carried` · `production`. */
export function trialTone(t) {
  const o = t?.outcome;
  if (o === "production") return "production";
  if (o === "carried") return "carried";
  if (o === "pass" || o === "fail" || o === "error" || o === "infra") return o;
  return "none";
}

const TRIAL_WORDS = {
  pass: "passed",
  fail: "failed",
  error: "errored · counted as a fail",
  infra: "didn't run · not counted",
  carried: "carried, not rerun",
  production: "production trace",
};
export const trialWord = (t) => TRIAL_WORDS[trialTone(t)] ?? "no result";

/**
 * The lane's verdict on this task: `Passed 3/3` · `Failed 0/3` ·
 * `Flaky 2/3` · `Errored · counted as a fail` · `Didn't run · not counted`
 * · `Production`. `tone` feeds RunChip's verdict dot.
 */
export function laneVerdict(lane) {
  if (!lane) return { tone: "none", label: "—", pass: 0, scored: 0 };
  if (lane.kind === "production")
    return {
      tone: "none",
      label: "Production",
      key: "production",
      pass: 0,
      scored: 0,
    };
  const trials = lane.trials ?? [];
  if (lane.ran_row === false || !trials.length)
    return {
      tone: "none",
      label: "Didn't run · not counted",
      key: "not_run",
      pass: 0,
      scored: 0,
    };
  const scored = trials.filter((t) => SCORED.has(effectiveOutcome(t)));
  if (!scored.length)
    return {
      tone: "none",
      label: "Didn't run · not counted",
      key: "infra",
      pass: 0,
      scored: 0,
    };
  const pass = scored.filter((t) => effectiveOutcome(t) === "pass").length;
  const n = scored.length;
  const carried = trials.find((t) => t.outcome === "carried")?.carried_from;
  const suffix = carried ? ` · carried from ${carried}` : "";
  if (scored.every((t) => effectiveOutcome(t) === "error"))
    return {
      tone: "error",
      label: "Errored · counted as a fail",
      key: "error",
      pass,
      scored: n,
    };
  if (pass === n)
    return {
      tone: "pass",
      label: `Passed ${pass}/${n}${suffix}`,
      key: "pass",
      pass,
      scored: n,
    };
  if (pass === 0)
    return {
      tone: "fail",
      label: `Failed 0/${n}${suffix}`,
      key: "fail",
      pass,
      scored: n,
    };
  return {
    tone: "flaky",
    label: `Flaky ${pass}/${n}${suffix}`,
    key: "flaky",
    pass,
    scored: n,
  };
}

/**
 * Default trial of a lane (L4): failing or flaky → the first failing trial;
 * passing → the median-cost passing trial; else the first.
 */
export function defaultTrialOf(lane) {
  const trials = lane?.trials ?? [];
  if (!trials.length) return 1;
  const v = laneVerdict(lane);
  if (v.key === "fail" || v.key === "flaky" || v.key === "error") {
    const t = trials.find((x) =>
      ["fail", "error"].includes(effectiveOutcome(x)),
    );
    if (t) return t.trial;
  }
  if (v.key === "pass") {
    const passing = trials
      .filter((x) => effectiveOutcome(x) === "pass")
      .sort(
        (a, b) => (a.cost_usd ?? 0) - (b.cost_usd ?? 0) || a.trial - b.trial,
      );
    if (passing.length)
      return passing[Math.floor((passing.length - 1) / 2)].trial;
  }
  const ran = trials.find((x) => x.outcome !== "infra");
  return (ran ?? trials[0]).trial;
}

/** The lane's trial object for `picked` ({runId: n}) or its default. */
export function trialOfLane(lane, picked = {}) {
  const trials = lane?.trials ?? [];
  const want = picked?.[lane?.run_id] ?? defaultTrialOf(lane);
  return trials.find((t) => t.trial === want) ?? trials[0] ?? null;
}

/** Pip tooltip: `Trial 2 · failed · order_found ✕ · $0.010 · 9.1 s`. */
export function pipTooltip(t) {
  if (!t) return "";
  const failed = (t.checks ?? []).find((c) => c.passed === false);
  const parts = [`Trial ${t.trial}`, trialWord(t)];
  if (failed) parts.push(`${failed.key} ✕`);
  if (t.no_trace_reason && t.outcome !== "carried")
    parts.push(t.no_trace_reason);
  if (Number.isFinite(t.cost_usd) && t.outcome !== "infra")
    parts.push(fmtCost(t.cost_usd));
  if (Number.isFinite(t.wall_ms) && t.outcome !== "infra")
    parts.push(fmtMs(t.wall_ms));
  return parts.join(" · ");
}

/** `Trial 2 failed; trials 1 and 3 passed` (flaky lanes' pip summary). */
export function trialsSummary(lane) {
  const trials = lane?.trials ?? [];
  const by = (o) =>
    trials.filter((t) => effectiveOutcome(t) === o).map((t) => t.trial);
  const list = (ns) =>
    ns.length === 1
      ? `${ns[0]}`
      : `${ns.slice(0, -1).join(", ")} and ${ns[ns.length - 1]}`;
  const parts = [];
  const fail = [...by("fail"), ...by("error")].sort();
  const pass = by("pass");
  if (fail.length)
    parts.push(
      `${fail.length === 1 ? "Trial" : "Trials"} ${list(fail)} failed`,
    );
  if (pass.length)
    parts.push(
      `${pass.length === 1 ? "trial" : "trials"} ${list(pass)} passed`,
    );
  const s = parts.join("; ");
  return s ? s[0].toUpperCase() + s.slice(1) : "";
}

// ------------------------------------------------------------ lanes

/** Baseline pinned left; the rest keep the URL order; max 4. */
export function orderLanes(runIds, baselineId) {
  const ids = [...new Set(runIds ?? [])];
  const base = ids.includes(baselineId)
    ? baselineId
    : ids.find((id) => !isSessionId(id));
  return [base, ...ids.filter((id) => id !== base)].filter(Boolean);
}

/** Baseline: the URL's, else the dataset's pinned run when it is a lane, else the first lane. */
export function resolveBaseline(runIds, urlBaseline, pinnedId) {
  const ids = (runIds ?? []).filter((id) => !isSessionId(id));
  if (urlBaseline && ids.includes(urlBaseline)) return urlBaseline;
  if (pinnedId && ids.includes(pinnedId)) return pinnedId;
  return ids[0] ?? null;
}

const cellPass = (cell) => {
  const ts = cell?.trials ?? [];
  const scored = ts.filter((t) => t === 1 || t === 0 || t === "error");
  return { pass: ts.filter((t) => t === 1).length, n: scored.length };
};

/**
 * Lanes that fit the lane area by default: `max(2, floor(width / 300))`
 * (1280 → 3, 1920 → 4), never above MAX_LANES (ONE-VIEW-SPEC A4-15).
 * @param {number} laneAreaWidth  px left for the lanes (window − nav − rail)
 */
export function maxLanesFor(laneAreaWidth) {
  const n = Math.floor((Number(laneAreaWidth) || 0) / 300);
  return Math.min(MAX_LANES, Math.max(2, n));
}

/**
 * The default lanes for a task from the matrix row's cells: the baseline +
 * every run that disagrees with it, ranked, up to `maxLanes` (the room on
 * screen, see maxLanesFor; never above MAX_LANES). "Most different" =
 * largest |pass − base pass|, then the largest step-count difference, then
 * selection order. Nobody disagrees → baseline + the most different run.
 */
export function defaultLaneIds({
  selection,
  baselineId,
  cells,
  maxLanes = MAX_LANES,
}) {
  const ids = (selection ?? []).filter((id) => cells?.[id]);
  const base = ids.includes(baselineId) ? baselineId : ids[0];
  if (!base) return [];
  const b = cellPass(cells[base]);
  const bSteps = cells[base]?.steps ?? 0;
  const ranked = ids
    .filter((id) => id !== base)
    .map((id, i) => {
      const c = cellPass(cells[id]);
      return {
        id,
        i,
        diff: Math.abs(c.pass - b.pass),
        steps: Math.abs((cells[id]?.steps ?? 0) - bSteps),
      };
    })
    .sort((x, y) => y.diff - x.diff || y.steps - x.steps || x.i - y.i);
  if (!ranked.length) return [base];
  const cap = Math.min(MAX_LANES, Math.max(2, maxLanes));
  const disagree = ranked.filter((r) => r.diff > 0).slice(0, cap - 1);
  return [
    base,
    ...(disagree.length ? disagree : ranked.slice(0, 1)).map((r) => r.id),
  ];
}

/**
 * IT-R5 G4 P4: the two lanes a link with more runs than lanes opens on (no
 * card picker): `baseline` + `cand` when the link names them, else the 2
 * most different runs on this task, else (every run gives the same result)
 * the baseline and the newest run. Pure.
 * @param {{urlIds: string[], baselineId?: string, candId?: string,
 *   cells?: object, order?: string[]}} a  `order` = runs oldest → newest
 * @returns {string[]|null}
 */
export function openPairIds({ urlIds = [], baselineId, candId, cells, order }) {
  const base = urlIds.includes(baselineId) ? baselineId : urlIds[0];
  if (!base) return null;
  if (candId && candId !== base && urlIds.includes(candId))
    return [base, candId];
  if (!cells) return null;
  const ids = defaultLaneIds({ selection: urlIds, baselineId: base, cells });
  const b = cellPass(cells[base] ?? {});
  const differs = ids
    .slice(1)
    .filter((id) => cellPass(cells[id] ?? {}).pass !== b.pass);
  if (differs.length) return [base, differs[0]];
  const pos = new Map((order ?? urlIds).map((id, i) => [id, i]));
  const newest = urlIds
    .filter((id) => id !== base)
    .sort((x, y) => (pos.get(y) ?? -1) - (pos.get(x) ?? -1))[0];
  return newest ? [base, newest] : null;
}

/**
 * IT-R5 G4 P4: the first task (in `ids` order) where `candId` passes a
 * smaller share of its tries than `baseId`, or null. Pure.
 */
export function firstWorseRow(matrix, ids = [], baseId, candId) {
  if (!matrix?.rows || !baseId || !candId) return null;
  const byId = new Map(matrix.rows.map((r) => [r.row_id, r]));
  const rate = (c) => {
    const x = cellPass(c ?? {});
    return x.n ? x.pass / x.n : null;
  };
  return (
    ids.find((id) => {
      const r = byId.get(id);
      const b = rate(r?.cells?.[baseId]);
      const c = rate(r?.cells?.[candId]);
      return b != null && c != null && c < b;
    }) ?? null
  );
}

/** Rail selection: the `from` Matrix runs + the lanes' experiment siblings. */
export function selectionOf({ lanes = [], fromRuns = [], datasetRuns = [] }) {
  const exps = new Set(lanes.map((l) => l.experiment_id).filter(Boolean));
  const siblings = datasetRuns
    .filter((r) => exps.has(r.experiment_id))
    .map((r) => r.run_id ?? r.id);
  const laneIds = lanes
    .map((l) => l.run_id ?? l)
    .filter((id) => !isSessionId(id));
  return [...new Set([...fromRuns, ...siblings, ...laneIds])].filter(
    (id) => id && !isSessionId(id),
  );
}

// ------------------------------------------------------------ rail

export const RAIL_FILTERS = Object.freeze([
  {
    value: "all",
    label: "All",
    long: "All tasks",
    help: "Every task in this comparison",
  },
  {
    value: "differ",
    label: "Differ",
    long: "Tasks where runs differ",
    help: "The compared runs disagree: one passes every trial, another passes none",
  },
  {
    value: "flaky",
    label: "Flaky",
    long: "Tasks with mixed tries",
    help: "At least one run passes some trials and fails others",
  },
  {
    value: "regressed",
    label: "Regressed",
    long: "Tasks worse than baseline",
    help: "At least one run passes fewer trials than the baseline",
  },
  {
    value: "unreviewed",
    label: "Unreviewed",
    long: "Tasks you haven't reviewed",
    help: "Tasks you have not marked viewed or judged yet",
  },
]);

/** `rail=failed:<runId>` (hc-7): tasks a run failed. */
export const FAILED_PREFIX = "failed:";
const failedTry = (cell) =>
  (cell?.trials ?? []).some((t) => t === 0 || t === "error");

/**
 * The rail filter for a `rail=` value, including the R4 ones:
 * `failed:any`, `failed:<runId>` (`Tasks #2B failed`) and `worse`
 * (`Tasks where #2B is worse than #3A`, with `focus=`).
 * @param {string} rail
 * @param {{labelOf?: (id) => string, focusId?: string, baselineId?: string}} [o]
 */
export function railFilterOf(
  rail,
  { labelOf = (id) => id, focusId, baselineId } = {},
) {
  const base = RAIL_FILTERS.find((f) => f.value === rail);
  if (base) return base;
  if (rail === `${FAILED_PREFIX}any`)
    return {
      value: rail,
      label: "Failed by any run",
      long: "Tasks a run failed",
      help: "At least one compared run failed at least one try",
    };
  if (rail?.startsWith(FAILED_PREFIX)) {
    const l = labelOf(rail.slice(FAILED_PREFIX.length));
    return {
      value: rail,
      label: `Failed by ${l}`,
      long: `Tasks ${l} failed`,
      help: `${l} failed at least one try on these tasks`,
    };
  }
  if (rail === "worse") {
    const f = focusId ? labelOf(focusId) : "the run";
    const b = baselineId ? labelOf(baselineId) : "the baseline";
    return {
      value: rail,
      label: "Worse",
      long: `Tasks where ${f} is worse than ${b}`,
      help: `${f} passes fewer tries than ${b}, or failed a try`,
    };
  }
  return RAIL_FILTERS.find((f) => f.value === "differ");
}

/** Matrix `show` → rail filter (`disagree` → `differ`). */
export function railFromMatrixShow(show) {
  if (show === "disagree") return "differ";
  if (show === "flaky") return "flaky";
  return "all";
}

/** Rows of the matrix in row-number order. */
export function railRows(matrix) {
  return [...(matrix?.rows ?? [])].sort((a, b) => a.n - b.n);
}

/**
 * The runs the rail's filters count over: the runs on screen (the lanes, or
 * every card), not the whole experiment. So `Differ` for #2B vs #2D equals
 * the Matrix's `Differ` chip for those two runs. Falls back to the selection
 * while fewer than two lanes are known (a task-only link before its lanes
 * are filled in).
 */
export function railRunIds({
  cards = false,
  urlIds = [],
  laneIds = [],
  selection = [],
} = {}) {
  const shown = (cards ? urlIds : laneIds).filter(
    (id) => id && !isSessionId(id),
  );
  return shown.length >= 2 ? [...new Set(shown)] : selection;
}

/**
 * IT-R1 G4 · runs differ on a task when their results differ (pass rate)
 * or their check outcomes differ (which checks failed, any try). Step and
 * tool-call counts differ on almost every task between two agents, so they
 * are not counted (the filter would list every task). Runs without a scored
 * try don't count.
 * @param {object} cells  matrix row cells of the runs on screen
 */
export function runsDiffer(cells) {
  const runs = Object.values(cells ?? {}).filter((c) => cellPass(c).n > 0);
  if (runs.length < 2) return false;
  const rate = (c) => {
    const p = cellPass(c);
    return p.pass / p.n;
  };
  const failedChecks = (c) =>
    [...new Set((c.failed_evals ?? []).flat().filter(Boolean))]
      .sort()
      .join("|");
  const same = (f) => runs.every((c) => f(c) === f(runs[0]));
  return !same(rate) || !same(failedChecks);
}

/** Row-id sets per rail filter over `runIds` (counts = the Matrix chips for those runs). */
export function railSets(
  matrix,
  { runIds = [], baselineId, rowsState = {}, focusId = null } = {},
) {
  const keep = new Set(runIds);
  const rows = railRows(matrix).map((r) => ({
    ...r,
    cells: Object.fromEntries(
      Object.entries(r.cells ?? {}).filter(([id]) => keep.has(id)),
    ),
  }));
  const d = disagreement({ rows });
  const inOrder = (set) =>
    rows.map((r) => r.row_id).filter((id) => set.has(id));
  const regressed = new Set(
    rows
      .filter((r) => {
        const base = r.cells?.[baselineId];
        if (!base) return false;
        const b = cellPass(base);
        return Object.entries(r.cells).some(
          ([id, c]) =>
            id !== baselineId && cellPass(c).n > 0 && cellPass(c).pass < b.pass,
        );
      })
      .map((r) => r.row_id),
  );
  const unreviewed = new Set(
    rows
      .filter(
        (r) =>
          !rowsState?.[r.row_id]?.viewed && !rowsState?.[r.row_id]?.verdicts,
      )
      .map((r) => r.row_id),
  );
  // Tasks each run failed (any try), any run, and `worse`.
  const failedBy = {};
  runIds.forEach((id) => {
    failedBy[`${FAILED_PREFIX}${id}`] = rows
      .filter((r) => failedTry(r.cells?.[id]))
      .map((r) => r.row_id);
  });
  const worse =
    focusId && keep.has(focusId)
      ? rows
          .filter((r) => {
            const f = r.cells?.[focusId];
            if (!f) return false;
            const fp = cellPass(f);
            if (!fp.n) return false;
            const bp = baselineId ? cellPass(r.cells?.[baselineId]) : null;
            const rate = (c) => (c.n ? c.pass / c.n : 0);
            return failedTry(f) || (bp && bp.n && rate(fp) < rate(bp));
          })
          .map((r) => r.row_id)
      : [];
  return {
    all: rows.map((r) => r.row_id),
    // Any difference in result or checks (never 0 while the lanes on screen
    // differ); `disagree` stays the strict all-vs-none set.
    differ: rows.filter((r) => runsDiffer(r.cells)).map((r) => r.row_id),
    disagree: inOrder(new Set(d.disagree)),
    flaky: inOrder(new Set(d.flaky)),
    regressed: inOrder(regressed),
    unreviewed: inOrder(unreviewed),
    [`${FAILED_PREFIX}any`]: rows
      .filter((r) => runIds.some((id) => failedTry(r.cells?.[id])))
      .map((r) => r.row_id),
    ...failedBy,
    worse,
  };
}

/**
 * A rail dot for one run on one task: `●` passed every trial, `○` failed
 * every trial, `◐` flaky, `!` errored, `–` didn't run; `carried` = dashed ring.
 */
export function dotOf(cell, label = "") {
  const ts = cell?.trials ?? [];
  const scored = ts.filter((t) => t === 1 || t === 0 || t === "error");
  const carried = Boolean(cell?.carried_from);
  const lead = label ? `${label} · ` : "";
  if (!cell || !scored.length)
    return { tone: "none", glyph: "–", carried, label: `${lead}didn't run` };
  const pass = scored.filter((t) => t === 1).length;
  const errors = scored.filter((t) => t === "error").length;
  const of = `${pass} of ${scored.length} ${scored.length === 1 ? "trial" : "trials"} passed`;
  const tail = carried ? ` · carried from ${cell.carried_from}` : "";
  if (errors && !pass)
    return {
      tone: "error",
      glyph: "!",
      carried,
      label: `${lead}${of} · ${errors} errored${tail}`,
    };
  if (pass === scored.length)
    return { tone: "pass", glyph: "●", carried, label: `${lead}${of}${tail}` };
  if (pass === 0)
    return { tone: "fail", glyph: "○", carried, label: `${lead}${of}${tail}` };
  return { tone: "flaky", glyph: "◐", carried, label: `${lead}${of}${tail}` };
}

/** `9 of 17 reviewed` over the rows of the current filter. */
export function reviewedCount(ids, rowsState) {
  const done = (ids ?? []).filter(
    (id) => rowsState?.[id]?.viewed || rowsState?.[id]?.verdicts,
  );
  return { done: done.length, total: (ids ?? []).length };
}

/** Next/previous row id in `ids` from `current` (wraps off: null at the ends). */
export function stepRow(ids, current, dir, skip) {
  const list = ids ?? [];
  let i = list.indexOf(current);
  for (let n = 0; n < list.length; n += 1) {
    i += dir;
    if (i < 0 || i >= list.length) return null;
    if (!skip || !skip(list[i])) return list[i];
  }
  return null;
}

// ------------------------------------------------------------ metrics

const isToolStep = (s) =>
  s?.type === "tool_call" ||
  (s?.type === "node" && ["tool", "retriever"].includes(s.node_type));

/**
 * Numbers of one trial: steps, tools, cost, time, tokens. L2 G2 R4
 * (consistency #8): `N steps` counts every numbered step the lane shows,
 * the terminal `Ended with …` step included (9 rows read `9 steps`).
 */
export function trialMetrics(t) {
  const steps = t?.steps ?? [];
  return {
    steps: steps.length,
    toolCalls: t?.tool_calls ?? steps.filter(isToolStep).length,
    toolErrors: t?.tool_errors ?? 0,
    cost: Number.isFinite(t?.cost_usd) ? t.cost_usd : null,
    wall: Number.isFinite(t?.wall_ms) ? t.wall_ms : null,
    tokens: Number.isFinite(t?.tokens) ? t.tokens : null,
    sandbox: Number.isFinite(t?.sandbox_ms) ? t.sandbox_ms : null,
  };
}

const absText = {
  steps: (v) => `${v} ${v === 1 ? "step" : "steps"}`,
  cost: (v) => fmtCost(v),
  wall: (v) => fmtMs(v),
  tokens: (v) => fmtTokens(v),
  sandbox: (v) => `${fmtMs(v)} sandbox`,
};
const deltaText = {
  steps: (d) =>
    d === 0
      ? "±0 steps"
      : `${d > 0 ? "+" : MINUS}${Math.abs(d)} ${Math.abs(d) === 1 ? "step" : "steps"}`,
  cost: (d) => fmtCostDelta(d),
  wall: (d) => fmtDeltaMs(d),
  tokens: (d) => `${d >= 0 ? "+" : MINUS}${fmtTokens(Math.abs(d))}`,
  sandbox: (d) => fmtDeltaMs(d),
};

/**
 * One lane number as `abs` / `delta` / `ratio` vs the baseline value, with
 * the cycle tooltip `Click to switch between 9.8 s, +3.7 s and 1.6×`.
 */
export function metricDisplay(key, value, base, fmt = "abs") {
  if (value == null)
    return { text: "—", tooltip: "Not recorded for this trial" };
  const abs = absText[key](value);
  const hasBase = base != null && base !== undefined;
  const delta = hasBase ? deltaText[key](value - base) : null;
  const ratio = hasBase && base > 0 ? fmtMultiple(value, base) : null;
  const options = [abs, delta, ratio].filter(Boolean);
  const tooltip =
    options.length > 1
      ? `Click to switch between ${options.slice(0, -1).join(", ")} and ${options[options.length - 1]}`
      : abs;
  let text = abs;
  if (fmt === "delta" && delta) text = delta;
  if (fmt === "ratio" && ratio) text = ratio;
  return { text, tooltip, abs, delta, ratio };
}

export const nextFmt = (fmt) =>
  ({ abs: "delta", delta: "ratio", ratio: "abs" })[fmt] ?? "abs";

// ------------------------------------------------------------ blind

function hash32(s) {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i += 1) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/**
 * Blind names, randomised per user per task (seeded): `{runId: "Run A"}` and
 * the shuffled lane order.
 */
export function blindOrder(laneIds, seed) {
  const ids = [...(laneIds ?? [])];
  let x = hash32(String(seed)) || 1;
  const rand = () => {
    x ^= x << 13;
    x ^= x >>> 17;
    x ^= x << 5;
    return (x >>> 0) / 4294967296;
  };
  for (let i = ids.length - 1; i > 0; i -= 1) {
    const j = Math.floor(rand() * (i + 1));
    [ids[i], ids[j]] = [ids[j], ids[i]];
  }
  const names = Object.fromEntries(
    ids.map((id, i) => [id, `Run ${String.fromCharCode(65 + i)}`]),
  );
  return { order: ids, names };
}

// ------------------------------------------------------------ copy

export const VERDICT_CHOICES = Object.freeze({
  verdictBaseline: "left",
  verdictTie: "tie",
  verdictOther: "right",
  verdictBothBad: "both_bad",
});

/** `Saved: #2D better on T26` · `Saved: tie on T26` · `Saved: both bad on T26`. */
export function verdictToast(choice, { leftLabel, rightLabel, rowLabel }) {
  if (choice === "tie") return `Saved: tie on ${rowLabel}`;
  if (choice === "both_bad") return `Saved: both bad on ${rowLabel}`;
  return `Saved: ${choice === "left" ? leftLabel : rightLabel} better on ${rowLabel}`;
}

/** `Link copied · T26 · 3 lanes`. */
export function linkToast({ rowLabel, lanes }) {
  return `Link copied · ${rowLabel} · ${lanes} ${lanes === 1 ? "lane" : "lanes"}`;
}

/** Lane kind chip: `Agent` · `Prompt + tools` · `Your code` · `Agent harness` (S1: no status words). */
export function kindChip(kind) {
  return (
    {
      agent: "Agent",
      prompt: "Prompt + tools",
      code: "Your code",
      harness: "Agent harness",
      model: "Model",
      production: "Production",
    }[kind] ?? "Run"
  );
}

/**
 * A model id as text (IT-R3 G4 P1, rule 19): run configs carry the model as
 * a string (`gpt-4o`), an object (`{id, params}` for code and harness runs)
 * or `{name}`; JSX only ever gets the string (an object crashed the page).
 * @param {unknown} m
 * @returns {string|null}
 */
export function modelText(m) {
  if (m == null) return null;
  if (typeof m === "string" || typeof m === "number") return String(m) || null;
  const v = m.id ?? m.name ?? m.model ?? null;
  return typeof v === "string" && v ? v : null;
}

/**
 * IT-R4 G4 P4 · which tries the lanes show and why, in one line:
 * `Trial 1 of 3 vs trial 2 of 3: Support Agent v3's first failed try and
 * Claude Code's typical passing try (median cost).` Null with one trial each.
 * @param {{lanes: object[], names?: Map, picked?: object}} args
 */
export function trialCaption({ lanes = [], names, picked = {} } = {}) {
  const shown = lanes.filter((l) => (l.trials?.length ?? 0) > 0);
  if (shown.length < 2 || shown.every((l) => l.trials.length < 2)) return null;
  const nameOf = (l) => names?.get(l.id) ?? l.label ?? "a run";
  const why = (l) => {
    const n = l.trial?.trial ?? trialOfLane(l, picked)?.trial;
    if (picked?.[l.run_id ?? l.id] != null)
      return `${nameOf(l)}'s try you picked`;
    const v = laneVerdict(l);
    if (["fail", "flaky", "error"].includes(v.key) && n === defaultTrialOf(l))
      return `${nameOf(l)}'s first failed try`;
    if (v.key === "pass" && n === defaultTrialOf(l))
      return `${nameOf(l)}'s typical passing try (median cost)`;
    return `${nameOf(l)}'s try ${n}`;
  };
  const head = shown
    .slice(0, 3)
    .map(
      (l, i) =>
        `${i ? "trial" : "Trial"} ${l.trial?.trial ?? 1} of ${l.trials.length}`,
    )
    .join(" vs ");
  return `${head}: ${shown.slice(0, 2).map(why).join(" and ")}.`;
}

/**
 * L2 G2 R4 (consistency #8): the footer's match count, from the same rows
 * the banner's `N differences` counts (milestone rows whose change is not
 * `same`): `12 of 18 steps match · 3 rows differ`. A step in a differing row
 * never counts as a match. Folded one-run steps are named apart. Pure.
 * @param {{rows?: object[]}|null} align  alignLanes result
 * @returns {string}
 */
export function stepsMatchText(align) {
  let match = 0;
  let total = 0;
  let folded = 0;
  let differ = 0;
  (align?.rows ?? []).forEach((row) => {
    if (row.kind === "milestone" && row.change !== "same") differ += 1;
    (row.cells ?? []).forEach((c) => {
      if (!c) return;
      if (c.type === "gap") {
        folded += c.count ?? 0;
        total += c.count ?? 0;
        return;
      }
      if (row.kind !== "milestone") return;
      const n = 1 + (c.subSteps?.length ?? 0);
      total += n;
      if (row.change === "same") match += n;
    });
  });
  const unit = total === 1 ? "step" : "steps";
  const parts = [`${match} of ${total} ${unit} match`];
  if (differ)
    parts.push(`${differ} ${differ === 1 ? "row differs" : "rows differ"}`);
  if (folded) parts.push(`${folded} only in one run (folded)`);
  return parts.join(" · ");
}
