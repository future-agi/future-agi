// W7 A1 (W7-5): the composite editor's and display's pure model — the new
// aggregations' copy, jury seats, levels, checks, the create / PATCH body
// fields (child_judge_models, child_levels, child_kinds keyed
// `<template_id>#<seat>` or `check:<key>`), validation, and the compact line
// the grid tooltip shows. The mock world stores and scores the same keys
// (evalTypes/composite.js).

/** The aggregations the editor offers on top of today's four. */
export const VOTE_AGGREGATIONS = [
  { value: "majority", label: "Majority" },
  { value: "all_pass", label: "All pass" },
];

export const AGGREGATION_COPY = {
  avg: "Plain mean of the child scores. Weights are ignored.",
  majority: "More than half the weight must pass. A tie fails.",
  all_pass: "Every child must pass.",
};

/** The line that replaces the composite's pass bar under a vote rule. */
export const VOTE_PASS_LINE = {
  majority: "Passes when more than half the weight passes",
  all_pass: "Passes only when every child passes",
};

export const isVoteAggregation = (agg) =>
  agg === "majority" || agg === "all_pass";

/** Aggregations whose children carry a weight the author sets. */
export const usesWeights = (agg) =>
  agg === "weighted_avg" || isVoteAggregation(agg);

export const LEVEL_OPTIONS = [
  { value: "row", label: "Whole row" },
  { value: "step", label: "Each step" },
];
export const STEP_KIND_OPTIONS = [
  { value: "llm", label: "LLM steps" },
  { value: "tool", label: "Tool steps" },
];
export const REDUCE_OPTIONS = [
  { value: "all_pass", label: "All pass" },
  { value: "mean", label: "Mean" },
  { value: "min", label: "Lowest" },
];

const AGG_WORD = {
  weighted_average: "weighted average",
  weighted_avg: "weighted average",
  average: "average",
  avg: "average",
  min: "min",
  max: "max",
  pass_rate: "pass rate",
  majority: "majority",
  all_pass: "all pass",
};
export const aggregationWord = (agg) => AGG_WORD[agg] ?? agg ?? "";

/** A child row's key: `<template_id>#<seat>`, or `check:<key>`. */
export const rowKeyOf = (c) =>
  c?.kind === "check"
    ? `check:${c.check_key}`
    : `${c?.child_id}#${c?.seat ?? 1}`;

/** The rows that are eval templates (a check is not one). */
export const templateRowsOf = (children = []) =>
  children.filter((c) => c.kind !== "check");

/** Number every row's seat by its template's order (1, 2, …). */
export function withSeats(children = []) {
  const seen = {};
  return children.map((c, order) => {
    if (c.kind === "check") return { ...c, order };
    seen[c.child_id] = (seen[c.child_id] ?? 0) + 1;
    const seat = seen[c.child_id];
    return {
      ...c,
      order,
      seat,
      kind: seat > 1 ? "seat" : c.kind === "seat" ? "eval" : c.kind ?? "eval",
    };
  });
}

/** Add a jury seat: the same template right after its last seat. */
export function addSeat(children, child) {
  const rows = withSeats(children);
  const last = rows.reduce(
    (at, c, i) =>
      c.child_id === child.child_id && c.kind !== "check" ? i : at,
    -1,
  );
  const seat = {
    ...child,
    kind: "seat",
    judge_model: null,
    level: child.level ?? "row",
  };
  return withSeats([...rows.slice(0, last + 1), seat, ...rows.slice(last + 1)]);
}

/** Add a task-suite check as a child (free; read from the try's checks). */
export function addCheck(children, { key, name }) {
  if (children.some((c) => c.kind === "check" && c.check_key === key))
    return children;
  return withSeats([
    ...children,
    {
      child_id: `check:${key}`,
      child_name: name ?? key,
      kind: "check",
      check_key: key,
      eval_type: "check",
      weight: 1,
    },
  ]);
}

/**
 * The check keys a task suite's rows carry (the `checks` column, a JSON list
 * per row), each once: [{key, name, suite, rows}] in first-seen order.
 * `table` and `columns` are get-dataset-table's `table` / `column_config`.
 */
export function suiteCheckKeysOf(table = [], columns = [], suite = null) {
  const col = columns.find((c) => c.name === "checks");
  if (!col) return [];
  const seen = new Map();
  table.forEach((r) => {
    const raw = r?.[col.id]?.cell_value ?? r?.[col.id];
    let list = raw;
    if (typeof raw === "string") {
      try {
        list = JSON.parse(raw);
      } catch {
        list = [];
      }
    }
    (Array.isArray(list) ? list : []).forEach((c) => {
      if (!c?.key) return;
      const at = seen.get(c.key);
      if (at) at.rows += 1;
      else
        seen.set(c.key, { key: c.key, name: c.name ?? c.key, suite, rows: 1 });
    });
  });
  return [...seen.values()];
}

/** Change one row (by rowKeyOf). */
export const patchRow = (children, key, patch) =>
  withSeats(
    children.map((c) => (rowKeyOf(c) === key ? { ...c, ...patch } : c)),
  );

/** Remove one row; the seats behind it renumber. */
export const removeRow = (children, key) =>
  withSeats(children.filter((c) => rowKeyOf(c) !== key));

const hasExtras = (rows) =>
  rows.some(
    (c) =>
      c.kind === "check" ||
      c.kind === "seat" ||
      c.judge_model ||
      (c.level ?? "row") === "step",
  );

/**
 * The jury, level and check fields of a create / PATCH / execute body, or {}
 * for a composite without them (today's body is then sent unchanged).
 * `weights` is the editor's child_id → weight map.
 */
export function compositeExtrasBody(children = [], weights = {}) {
  const rows = withSeats(children);
  if (!hasExtras(rows)) return {};
  const body = {
    child_template_ids: rows
      .filter((c) => c.kind !== "check")
      .map((c) => c.child_id),
    child_judge_models: {},
    child_levels: {},
    child_kinds: {},
    child_weights: {},
  };
  rows.forEach((c) => {
    const key = rowKeyOf(c);
    body.child_kinds[key] = c.kind;
    if (c.judge_model) body.child_judge_models[key] = c.judge_model;
    body.child_levels[key] =
      c.level === "step"
        ? {
            level: "step",
            step_kind: c.step_kind ?? "llm",
            reduce: c.reduce ?? "all_pass",
          }
        : { level: "row" };
    const w =
      c.seat > 1 || c.kind === "check"
        ? c.weight ?? 1
        : weights[c.child_id] ?? c.weight ?? 1;
    body.child_weights[key] = Number(w);
    if (c.kind !== "check" && c.seat === 1)
      body.child_weights[c.child_id] = Number(w);
  });
  return body;
}

/** The detail's children with their stored seat, judge and level. */
export function rowsFromDetail(detail) {
  // Eval children, then the task-suite checks (kept beside them).
  const kids = [
    ...(detail?.children ?? []).filter((c) => c.kind !== "check"),
    ...(detail?.check_children ?? []),
  ];
  const models = detail?.child_judge_models ?? {};
  const levels = detail?.child_levels ?? {};
  return withSeats(kids).map((c) => {
    const key = rowKeyOf(c);
    const lvl = levels[key];
    return {
      ...c,
      ...(models[key] ? { judge_model: models[key] } : {}),
      ...(lvl?.level === "step"
        ? { level: "step", step_kind: lvl.step_kind, reduce: lvl.reduce }
        : {}),
    };
  });
}

/**
 * Why the composite cannot be saved or tested yet ([] when it can).
 * @param {{aggregation: string, children: object[], weights?: object}} p
 */
export function validateComposite({
  aggregation,
  children = [],
  weights = {},
}) {
  const errors = [];
  const rows = withSeats(children);
  if (aggregation === "majority" && rows.length < 2)
    errors.push("Majority needs at least 2 children");
  if (usesWeights(aggregation)) {
    const bad = rows.find((c) => {
      const w = Number(
        c.seat > 1 || c.kind === "check"
          ? c.weight ?? 1
          : weights[c.child_id] ?? c.weight ?? 1,
      );
      return !(w > 0);
    });
    if (bad) errors.push(`Give ${bad.child_name} a weight above 0`);
  }
  rows
    .filter((c) => c.kind === "seat" && !c.judge_model)
    .forEach((c) =>
      errors.push(`Pick a judge for seat ${c.seat} of ${c.child_name}`),
    );
  const seen = new Set();
  rows
    .filter((c) => c.kind !== "check" && c.judge_model)
    .forEach((c) => {
      const k = `${c.child_id}|${c.judge_model}|${c.level ?? "row"}`;
      if (seen.has(k))
        errors.push(`${c.child_name} has two seats with ${c.judge_model}`);
      seen.add(k);
    });
  return errors;
}

const fmt = (v) => (v == null ? "–" : Number(v).toFixed(2));

/** `Correctness ×2 Pass 0.82 · Tone ×1 Fail 0.31 · majority → Pass`. */
export function compactLine(typed, passed) {
  if (typed?.kind !== "composite") return "";
  const parts = typed.children.map((k) => {
    const who = k.kind === "seat" || k.seat > 1 ? ` (${k.judge_model})` : "";
    const verdict = k.skipped ? "Skipped" : k.passed ? "Pass" : "Fail";
    return `${k.name}${who} ×${k.weight} ${verdict}${k.skipped ? "" : ` ${fmt(k.value)}`}`;
  });
  const p = typed.passed ?? passed;
  const verdict = p == null ? "" : ` → ${p ? "Pass" : "Fail"}`;
  return `${parts.join(" · ")} · ${aggregationWord(typed.aggregation)}${verdict}`;
}

/** `Judges disagree (2–1)` when the scored children split, else null. */
/**
 * Whether a composite's judged children are one template's seats on the
 * same reading (a jury: the same level and step kind): its split reads
 * "Judges disagree"; different evals, or one eval on different steps,
 * "Children split".
 */
export const isJuryOf = (typed) => {
  const keys = (typed?.children ?? [])
    .filter((k) => k.kind !== "check")
    .map((k) => `${k.template_id}|${k.level ?? "row"}|${k.step_kind ?? ""}`);
  return keys.length > 1 && new Set(keys).size === 1;
};

export const splitLine = (typed) =>
  typed?.disagreement
    ? `${isJuryOf(typed) ? "Judges disagree" : "Children split"} (${typed.agreement?.split})`
    : null;

/** A vote's passing weight: `passing weight 2 of 3`, else null. */
export const voteShareLine = (typed) =>
  typed?.agreement?.total_weight != null
    ? `passing weight ${typed.agreement.pass_weight} of ${typed.agreement.total_weight}`
    : null;

/** A child's level in words: `Whole row`, `Each tool step · mean`. */
export function levelText(k) {
  if (k?.kind === "check") return "Check";
  if (k?.level !== "step") return "Whole row";
  const kind = k.step_kind === "tool" ? "tool" : "LLM";
  const red =
    REDUCE_OPTIONS.find((o) => o.value === k.reduce)?.label ?? "All pass";
  return `Each ${kind} step · ${red.toLowerCase()}`;
}
