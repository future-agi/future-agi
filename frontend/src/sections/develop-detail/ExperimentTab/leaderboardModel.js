// Pure rules of the Experiments tab leaderboard (PRODUCT-SPEC §3.7,
// area-experiments §A2). The API computes every aggregate, delta and
// movement count (R1); this module only picks, orders, groups and links.
import { getRunStatus } from "src/sections/common/Runs/runStatus";
import { erroredRowsOf } from "src/sections/common/Runs/runLedger";
import { runLabel } from "src/sections/common/Runs/runLabel";
import {
  defaultRunsTab,
  kindLabel,
  outputModalityOf,
  runKindOf,
} from "src/sections/common/Runs/runKind";
import { provenanceOf } from "src/sections/common/Runs/provenanceModel";
import { promptLinkOf } from "src/sections/common/RunSettings/targetLinks";
import {
  movementPill,
  serializePills,
} from "src/sections/common/Results/filterPills";

export const MAX_COMPARE_RUNS = 8;
export const STRIP_RUNS = 20;

// EL#34: every option says what it groups by, in one line.
export const GROUP_OPTIONS = [
  {
    value: "experiment",
    label: "Experiment",
    help: "Runs launched together, with the best run on the group row",
  },
  { value: "none", label: "None", help: "One flat list of runs" },
  { value: "model", label: "Model", help: "The model each run called" },
  {
    value: "prompt_version",
    label: "Prompt or agent version",
    help: "Which prompt or agent version ran",
  },
  {
    value: "target_type",
    label: "Run type",
    help: "Prompt, agent or HTTP endpoint",
  },
  {
    value: "temperature",
    label: "Temperature",
    help: "The sampling temperature (prompt runs)",
  },
  { value: "status", label: "Status", help: "Running, waiting, completed…" },
  { value: "created_by", label: "Created by", help: "Who launched the run" },
  {
    value: "dataset_version",
    label: "Dataset version",
    help: "The dataset version each run used",
  },
  {
    value: "iteration",
    label: "Iteration",
    help: "A run and the reruns that came from it",
  },
  // W5 C-H1: the experiment's tags (a run with several sits under its first).
  { value: "tag", label: "Tag", help: "The tags on each run's experiment" },
];
export const GROUP_VALUES = GROUP_OPTIONS.map((o) => o.value);
export const normalizeGroup = (g) =>
  GROUP_VALUES.includes(g) ? g : "experiment";

export const TARGET_LABEL = {
  prompt: "Prompt",
  agent: "Agent",
  external: "External",
  http: "HTTP",
  code: "Code",
  harness: "Harness",
  traces: "Traces",
};
// Group-by `Run type` names (the New experiment cards' words).
const RUN_TYPE_LABEL = {
  prompt: "Prompt",
  agent: "Agent",
  external: "From code",
  http: "HTTP endpoint",
  code: "Code",
  harness: "Harness",
  traces: "Traces",
};

export const TARGET_ICON = {
  prompt: "hugeicons:message-programming",
  agent: "hugeicons:ai-network",
  external: "mdi:github",
  http: "mdi:api",
  // The New experiment cards' icons.
  code: "mdi:code-braces",
  harness: "mdi:console",
  traces: "mdi:transit-connection-variant",
};

const isNum = (v) => typeof v === "number" && Number.isFinite(v);

const hostOf = (url) => {
  try {
    const u = new URL(url);
    return `${u.host}${u.pathname}`;
  } catch {
    return url ?? "";
  }
};

/**
 * One line that says what a run ran:
 * `Support Reply V5 · gpt-4o-mini · t=0.2`, `Support Agent v3`,
 * `CI · main@a1f3c9e`, `POST api.acme.ai/v1/support/reply`.
 */
export function runConfigLine(run) {
  const t = run?.target ?? {};
  switch (run?.target_type) {
    case "prompt": {
      const temp = t.model_params?.temperature;
      return [
        [t.prompt_name, t.prompt_version_name].filter(Boolean).join(" "),
        t.model,
        isNum(temp) ? `t=${temp}` : null,
      ]
        .filter(Boolean)
        .join(" · ");
    }
    case "agent":
      return [t.graph_name, t.version_name].filter(Boolean).join(" ");
    case "external": {
      const ref = [t.branch, t.commit].filter(Boolean).join("@");
      return ref ? `CI · ${ref}` : "External run";
    }
    case "http": {
      const url = t.url ?? run?.http?.url;
      return url ? `${t.method ?? "POST"} ${hostOf(url)}` : "HTTP endpoint";
    }
    default:
      return run?.name ?? "";
  }
}

/**
 * The part of a run's config line that tells it apart from its siblings in
 * one experiment: `t=0.2` vs `t=0.9`, `gpt-4o-mini` vs `claude-sonnet-4-5`.
 * Falls back to the full line when nothing differs.
 */
export function distinctConfigLine(run, siblings) {
  const full = runConfigLine(run);
  const others = (siblings ?? []).filter((r) => r.run_id !== run.run_id);
  if (!others.length) return full;
  const otherParts = others.map((r) => new Set(runConfigLine(r).split(" · ")));
  const parts = full
    .split(" · ")
    .filter((p) => otherParts.some((set) => !set.has(p)));
  return parts.length ? parts.join(" · ") : full;
}

/** `gpt-4o-mini`, or the agent / external / HTTP stand-in. */
export function modelOf(run) {
  const t = run?.target ?? {};
  if (t.model) return t.model;
  if (run?.target_type === "agent") return `${t.graph_name ?? "Agent"} (agent)`;
  if (run?.target_type === "external") return "External (CI)";
  if (run?.target_type === "http") return "HTTP endpoint";
  return "Unknown";
}

/** `Support Reply V5`, `Support Agent v3`, `External (CI)`. */
export function targetVersionOf(run) {
  const t = run?.target ?? {};
  if (run?.target_type === "prompt")
    return [t.prompt_name, t.prompt_version_name].filter(Boolean).join(" ");
  if (run?.target_type === "agent")
    return [t.graph_name, t.version_name].filter(Boolean).join(" ");
  return modelOf(run);
}

/** Group key + label of a run for the `Group by` choice. */
export function groupOf(run, group) {
  switch (group) {
    case "experiment":
      return {
        key: run.experiment_id,
        label: run.experiment_name ?? `#${run.experiment_ordinal}`,
      };
    case "model": {
      const m = modelOf(run);
      return { key: `model:${m}`, label: m };
    }
    case "prompt_version": {
      const v = targetVersionOf(run);
      return { key: `target:${v}`, label: v };
    }
    case "dataset_version": {
      const n = run.dataset_version?.number;
      const tag = run.dataset_version?.tag;
      return {
        key: `version:${n}`,
        label: [`v${n}`, tag].filter(Boolean).join(" · "),
      };
    }
    case "target_type": {
      const t = run.target_type ?? "other";
      return { key: `type:${t}`, label: RUN_TYPE_LABEL[t] ?? "Other" };
    }
    case "temperature": {
      const temp = run.target?.model_params?.temperature;
      return isNum(temp)
        ? { key: `temp:${temp}`, label: `Temperature ${temp}` }
        : { key: "temp:none", label: "No temperature (not a prompt run)" };
    }
    case "status": {
      const st = getRunStatus(run);
      return { key: `status:${st.label}`, label: st.label };
    }
    case "created_by": {
      const who = run.created_by?.name;
      return who
        ? { key: `by:${who}`, label: who }
        : { key: "by:none", label: "Unknown creator" };
    }
    case "tag": {
      const t = run.tags?.[0];
      return t
        ? { key: `tag:${t}`, label: t }
        : { key: "tag:none", label: "No tag" };
    }
    case "iteration": {
      // The grid joins lineage (`__root`) and labels by root run.
      const root = run.__root ?? run.run_id;
      return { key: `root:${root}`, label: runLabel(run) };
    }
    default:
      return { key: run.run_id, label: runLabel(run) };
  }
}

// ---------------------------------------------------------------- values

/** The aggregate an eval cell shows: pass rate for pass/fail, else avg / median. */
export function aggValueOf(run, def, agg = "avg") {
  const a = run?.metrics?.aggregates?.[def?.key];
  if (!a) return null;
  if (def.output_type === "pass_fail" || def.output_type === "thumbs")
    return isNum(a.avg) ? a.avg : null;
  const v = agg === "median" ? a.median : a.avg;
  return isNum(v) ? v : null;
}

/** The run with the best aggregate by the metric's direction, or null. */
export function bestRunFor(runs, def, agg = "avg") {
  const hib = def?.higher_is_better;
  if (hib == null) return null;
  let best = null;
  let bestValue = null;
  (runs ?? []).forEach((r) => {
    const v = aggValueOf(r, def, agg);
    if (!isNum(v)) return;
    if (bestValue == null || (hib ? v > bestValue : v < bestValue)) {
      best = r;
      bestValue = v;
    }
  });
  return best;
}

// ------------------------------------------------- ranking finished runs
// A running run's numbers move between polls (78.6% → 75.0% → 76.9%), so a
// group row only crowns FINISHED runs, like the answer line does.

/** Every task done and Completed / Finished with errors. */
export function isRunFinal(run) {
  const st = getRunStatus(run);
  if (st.key !== "Completed" && st.key !== "PartialCompleted") return false;
  const p = run?.progress;
  // L7c g5 (kept:so-far-headline): a run whose rows are in but not all
  // scored yet is not final (it is not ranked while it scores).
  if (p && isNum(p.scored) && p.scored < (p.done ?? 0)) return false;
  return !(p && isNum(p.total) && p.total > 0 && (p.done ?? 0) < p.total);
}

/** Still going: running, scoring, queued or waiting for uploaded results. */
export function isRunPending(run) {
  const st = getRunStatus(run);
  return Boolean(st.active || st.waiting);
}

/**
 * `{done, total}` while a run's numbers cover only part of its tasks
 * (running, or stopped part-way), else null.
 */
export function partialProgress(run) {
  if (isRunFinal(run)) return null;
  // W1-6 (review M7): the run's one ledger, when R1 sends it: rows still
  // running or queued, and rows a limit stopped, are not in its numbers
  // yet (a Failed-rows child scoring its 56 re-played rows reads `Based on
  // 182 of 238`, never 238 of 238 from the rows it carried).
  const L = run?.ledger?.rows;
  if (L && isNum(L.total) && L.total > 0) {
    // L7c g5: rows still scoring are not in its numbers either.
    const done = Math.max(
      0,
      L.total -
        (L.running ?? 0) -
        (L.scoring ?? 0) -
        (L.queued ?? 0) -
        (L.stopped ?? 0),
    );
    return done < L.total ? { done, total: L.total } : null;
  }
  const p = run?.progress;
  if (!p || !isNum(p.total) || p.total <= 0) return null;
  const done = p.done ?? 0;
  return done < p.total ? { done, total: p.total } : null;
}

/** `Based on 14 of 48 tasks, may change`, or null for a finished run. */
export function partialNote(run, word = "tasks") {
  const p = partialProgress(run);
  return p ? `Based on ${p.done} of ${p.total} ${word}, may change` : null;
}

/**
 * IT-R5 G2 P6: a voice run with no audio (`Text only`) is not ranked
 * against voice runs: no best, no medal, not the dataset's Best.
 */
export function isUnrankedRun(run) {
  return runKindOf(run) === "voice" && outputModalityOf(run) === "text";
}

/** The runs a group row may rank (finished) and the ones still going. */
export function rankPool(runs) {
  const list = runs ?? [];
  return {
    finished: list.filter((r) => isRunFinal(r) && !isUnrankedRun(r)),
    pending: list.filter(isRunPending),
  };
}

/**
 * Group row with nothing finished yet: `Best pending` and why. Null when
 * nothing is still going (every run stopped or failed part-way).
 */
export function bestPending(pending) {
  const n = pending?.length ?? 0;
  if (!n) return null;
  const who = pending
    .map((r) => {
      const p = r.progress;
      const label = r.label ?? runLabel(r);
      return p && isNum(p.total) && p.total > 0
        ? `${label} ${p.done ?? 0}/${p.total}`
        : label;
    })
    .join(", ");
  return {
    label: "Best pending",
    tip: `${n} ${n === 1 ? "run is" : "runs are"} still running (${who}). The best run is named when ${n === 1 ? "it finishes" : "they finish"}; until then the numbers move.`,
  };
}

/** `2 runs still running are not ranked yet.` for a group row tooltip. */
export function unrankedNote(pending) {
  const n = pending?.length ?? 0;
  if (!n) return null;
  return `${n} ${n === 1 ? "run" : "runs"} still running ${n === 1 ? "is" : "are"} not ranked yet.`;
}

/** Mean of the runs' aggregates (non-experiment group rows). */
export function meanValue(runs, getter) {
  const xs = (runs ?? []).map(getter).filter(isNum);
  return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;
}

/** Sum over runs, null when no run has a value. */
export function sumValue(runs, getter) {
  const xs = (runs ?? []).map(getter).filter(isNum);
  return xs.length ? xs.reduce((a, b) => a + b, 0) : null;
}

export const latencyP50 = (run) => run?.metrics?.latency_p50_s ?? null;
export const latencyP99 = (run) => run?.metrics?.latency_p99_s ?? null;
export const costOf = (run) => run?.metrics?.cost_usd ?? null;
export const tokensOf = (run) => {
  const t = run?.metrics?.tokens;
  if (!t) return null;
  const total = (t.prompt ?? 0) + (t.completion ?? 0);
  return total || (run?.progress?.done ? 0 : null);
};
// L7c g5: errored rows from the one ledger (the Summary's count).
export const errorsOf = (run) =>
  erroredRowsOf(run, run?.metrics?.error_count ?? null);

/** The run with the lowest p50 latency. */
export function fastestRun(runs) {
  let best = null;
  (runs ?? []).forEach((r) => {
    const v = latencyP50(r);
    if (isNum(v) && (!best || v < latencyP50(best))) best = r;
  });
  return best;
}

// Most active first: the state a group row shows.
const STATUS_RANK = [
  "Running",
  "ExperimentEvaluation",
  "Queued",
  "NotStarted",
  "WaitingForResults",
  "Editing",
  "Failed",
  "Cancelled",
  "PartialCompleted",
  "Completed",
];

/** The run whose status a group row shows (most active state). */
export function mostActiveRun(runs) {
  const rank = (r) => {
    const i = STATUS_RANK.indexOf(getRunStatus(r).key);
    return i === -1 ? STATUS_RANK.length : i;
  };
  return [...(runs ?? [])].sort((a, b) => rank(a) - rank(b))[0] ?? null;
}

/** `Prompt ×2 · Agent ×1` for a group of runs. */
export function targetMix(runs) {
  const counts = new Map();
  (runs ?? []).forEach((r) =>
    counts.set(r.target_type, (counts.get(r.target_type) ?? 0) + 1),
  );
  return [...counts.entries()]
    .map(
      ([t, n]) => `${TARGET_LABEL[t] ?? t}${runs.length > 1 ? ` ×${n}` : ""}`,
    )
    .join(" · ");
}

/** Examples a run covers: `248`, or `48 of 248` for a split. */
export function examplesText(run) {
  const n = run?.examples ?? run?.progress?.total;
  if (!isNum(n)) return null;
  const all = run?.version_rows;
  return run?.splits?.length && isNum(all) && all !== n
    ? `${n} of ${all}`
    : String(n);
}

// ---------------------------------------------------------------- order

const byCreatedAsc = (a, b) =>
  String(a.created_at).localeCompare(String(b.created_at)) ||
  (a.experiment_ordinal ?? 0) - (b.experiment_ordinal ?? 0) ||
  String(a.letter).localeCompare(String(b.letter));

/** The last `limit` runs, oldest to newest (the strip's x axis). */
export function stripRuns(runs, limit = STRIP_RUNS) {
  return [...(runs ?? [])].sort(byCreatedAsc).slice(-limit);
}

/** Latest run (newest created, last letter) in a list. */
export const latestRun = (runs) => stripRuns(runs, 1)[0] ?? null;

// ---------------------------------------------------------------- filters

/** R4 (lane Rb): `Your code`, `Agent harness`… for the Kind filter. */
export const kindOfRun = (r) => {
  const k = runKindOf(r);
  return k ? kindLabel(k) : null;
};
/** R4 (lane Rb): the framework / harness / provider name (catalog). */
export const frameworkOf = (r) => {
  const src = provenanceOf(r)?.source;
  return src?.entryId ? src.name ?? null : null;
};

const FILTER_FIELDS = {
  kind: (r) => kindOfRun(r) ?? "",
  framework: (r) => frameworkOf(r) ?? "",
  status: (r) => getRunStatus(r).key,
  target: (r) => r.target_type,
  model: (r) => modelOf(r),
  target_version: (r) => targetVersionOf(r),
  dataset_version: (r) => String(r.dataset_version?.number ?? ""),
  split: (r) => (r.splits?.length ? r.splits.map((s) => s.name) : ["__all__"]),
  created_by: (r) => r.created_by?.name ?? "",
  has_errors: (r) => ((r.metrics?.error_count ?? 0) > 0 ? "yes" : "no"),
  trials: (r) => r.trials ?? 1,
  tag: (r) => r.tags ?? [], // W5 C-H1
  run_id: (r) => r.run_id, // W5 C-H1 `Filter to selected`
};

const filterValueList = (v) =>
  (Array.isArray(v) ? v : String(v ?? "").split(","))
    .map((x) => String(x).trim())
    .filter(Boolean);

function matchesNumber(value, op, [a, b] = []) {
  if (!isNum(value)) return false;
  const x = Number(a);
  const y = Number(b);
  switch (op) {
    case "greater_than":
      return value > x;
    case "less_than":
      return value < x;
    case "greater_than_or_equal":
      return value >= x;
    case "less_than_or_equal":
      return value <= x;
    case "not_equals":
      return value !== x;
    case "between":
      return value >= Math.min(x, y) && value <= Math.max(x, y);
    case "not_between":
      return value < Math.min(x, y) || value > Math.max(x, y);
    default:
      return Math.abs(value - x) < 1e-9;
  }
}

/** True when a ComplexFilter row has everything it needs to apply. */
export function isCompleteFilter(f) {
  const cfg = f?.filter_config;
  if (!f?.column_id || !cfg) return false;
  const v = cfg.filter_value;
  if (Array.isArray(v)) return v.some((x) => x !== "" && x != null);
  return v !== "" && v != null;
}

/**
 * Apply ComplexFilter rows (`{column_id, filter_config:{filter_type,
 * filter_op, filter_value}}`) to runs. `metric:<key>` columns compare the
 * eval aggregate in display units (pass/fail in %, scores 0–1).
 */
export function filterRuns(runs, filters, defsByKey = {}) {
  const active = (filters ?? []).filter(isCompleteFilter);
  if (!active.length) return runs ?? [];
  return (runs ?? []).filter((run) =>
    active.every((f) => {
      const { filter_op: op, filter_value: value } = f.filter_config;
      if (f.column_id.startsWith("metric:")) {
        const def = defsByKey[f.column_id.slice(7)];
        let v = aggValueOf(run, def);
        if (isNum(v) && def?.output_type === "pass_fail") v *= 100;
        const vals = Array.isArray(value) ? value : [value];
        return matchesNumber(v, op, vals);
      }
      const get = FILTER_FIELDS[f.column_id];
      if (!get) return true;
      const actual = get(run);
      if (f.column_id === "trials")
        return matchesNumber(
          actual,
          op,
          Array.isArray(value) ? value : [value],
        );
      const wanted = filterValueList(value);
      const have = Array.isArray(actual) ? actual : [String(actual)];
      const hit = have.some((h) => wanted.includes(h));
      return op === "not_equals" || op === "not_in" ? !hit : hit;
    }),
  );
}

// ---------------------------------------------------------------- selection

/**
 * `runs` + `baseline` of the compare page for a selection (§3.7):
 * the view baseline when selected, else the oldest selected run; the
 * baseline goes first. One run compares against the view baseline.
 * `runs` (the selected run objects) lets compareUrl pick the default tab.
 *
 * IT-R4 G2 P4 (S2, rule 27): `opts.pinnedId` (the dataset's pinned
 * baseline) wins whenever it is selected; `candId` is `opts.candId` (the
 * headline candidate) when selected, else the newest selected non-baseline
 * run. Spread the result into compareUrl so every link carries `runs`,
 * `baseline` and `cand`.
 * @returns {{runIds: string[], baselineId: string|null, candId: string|null,
 *   error: string|null, runs: object[]}}
 */
export function compareSelection(selected, viewBaselineId, opts = {}) {
  const runs = (selected ?? []).filter(Boolean);
  const ids = runs.map((r) => r.run_id);
  const base =
    opts.pinnedId && ids.includes(opts.pinnedId)
      ? opts.pinnedId
      : viewBaselineId;
  const sel = selectionOf(runs, base);
  return {
    ...sel,
    candId: candidateOf(runs, sel.baselineId, opts.candId),
    runs,
  };
}

function candidateOf(runs, baselineId, wanted) {
  const others = runs.filter((r) => r.run_id !== baselineId);
  if (!others.length) return null;
  if (wanted && others.some((r) => r.run_id === wanted)) return wanted;
  return [...others].sort(byCreatedAsc)[others.length - 1].run_id;
}

function selectionOf(runs, viewBaselineId) {
  if (!runs.length)
    return { runIds: [], baselineId: null, error: "Select runs to compare" };
  if (runs.length === 1) {
    const only = runs[0];
    if (!viewBaselineId)
      return {
        runIds: [only.run_id],
        baselineId: null,
        error: "Pick a baseline run to compare one run against",
      };
    if (only.run_id === viewBaselineId)
      return {
        runIds: [only.run_id],
        baselineId: only.run_id,
        error: "This is the baseline run. Select a run to compare with it",
      };
    return {
      runIds: [viewBaselineId, only.run_id],
      baselineId: viewBaselineId,
      error: null,
    };
  }
  if (runs.length > MAX_COMPARE_RUNS)
    return {
      runIds: runs.map((r) => r.run_id),
      baselineId: null,
      // IT-R4 G2 P3: the reason names the range and the count.
      error: `Compare takes 2-${MAX_COMPARE_RUNS} runs · you selected ${runs.length}`,
    };
  const ids = runs.map((r) => r.run_id);
  const baselineId = ids.includes(viewBaselineId)
    ? viewBaselineId
    : [...runs].sort(byCreatedAsc)[0].run_id;
  return {
    runIds: [baselineId, ...ids.filter((id) => id !== baselineId)],
    baselineId,
    error: null,
  };
}

/** Experiment ids whose every run is selected (Delete deletes experiments). */
export function wholeExperiments(selected, allRuns) {
  const selectedIds = new Set((selected ?? []).map((r) => r.run_id));
  const byExp = new Map();
  (allRuns ?? []).forEach((r) => {
    if (!byExp.has(r.experiment_id)) byExp.set(r.experiment_id, []);
    byExp.get(r.experiment_id).push(r.run_id);
  });
  const touched = new Set((selected ?? []).map((r) => r.experiment_id));
  const whole = [];
  let partial = false;
  touched.forEach((expId) => {
    const ids = byExp.get(expId) ?? [];
    if (ids.length && ids.every((id) => selectedIds.has(id))) whole.push(expId);
    else partial = true;
  });
  return { experimentIds: whole, partial };
}

// ---------------------------------------------------------------- links

const enc = encodeURIComponent;

/**
 * Compare runs page: `…/compare/<tab>?runs=a,b&baseline=a[&filters][&review]`.
 * The tab is `data` when pills or a review are named, else
 * defaultRunsTab(runs) (`matrix` for runs of different kinds; R2-SPEC §0.3).
 */
export function compareUrl(
  datasetId,
  { runIds, baselineId, candId, pills, review, from, runs, tab: forceTab } = {},
) {
  const parts = [`runs=${(runIds ?? []).join(",")}`];
  if (baselineId) parts.push(`baseline=${baselineId}`);
  // IT-R3 G2 P1 / S6: the headline's pair names its candidate.
  if (candId && candId !== baselineId) parts.push(`cand=${candId}`);
  const filters = serializePills(pills);
  if (filters) parts.push(`filters=${enc(filters)}`);
  if (review) parts.push(`review=${review}`);
  if (from) parts.push(`from=${enc(from)}`);
  // IT-R2 G2 P1: `tab` forces one (the headline's `Compare these two`
  // always opens the Matrix, which is what it promises).
  const tab = forceTab ?? (filters || review ? "data" : defaultRunsTab(runs));
  return `/dashboard/develop/${datasetId}/compare/${tab}?${parts.join("&")}`;
}

/** A movement count opens compare filtered to exactly those rows (C26). */
export function movementUrl(
  datasetId,
  { baselineId, runId, metricKey, dir, from },
) {
  return compareUrl(datasetId, {
    runIds: [baselineId, runId],
    baselineId,
    pills: [movementPill(metricKey, dir, runId)],
    from,
  });
}

/**
 * Experiment page, optionally focused on a run and filtered by pills. With
 * neither, the tab is defaultRunsTab(runs) (the experiment's run objects).
 */
export function experimentUrl(
  datasetId,
  experimentId,
  { runId, pills, runs } = {},
) {
  const parts = [`datasetId=${datasetId}`];
  if (runId) parts.push(`run=${runId}`);
  const filters = serializePills(pills);
  if (filters) parts.push(`filters=${enc(filters)}`);
  const tab = runId || filters ? "data" : defaultRunsTab(runs);
  return `/dashboard/develop/experiment/${experimentId}/${tab}?${parts.join("&")}`;
}

/** Every listed run of one experiment (IT-R2 G2 P1: its Open tab). */
export const experimentRunsOf = (allRuns, experimentId) =>
  (allRuns ?? []).filter(
    (r) => r?.experiment_id === experimentId && r.listed !== false,
  );

/** `Open in Prompts` for a prompt run: the workbench on the run's version. */
export const promptUrl = (run) =>
  run?.target?.prompt_id
    ? promptLinkOf(run.target, { config: run.config })
    : null;
