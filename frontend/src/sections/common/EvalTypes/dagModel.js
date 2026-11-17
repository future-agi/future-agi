// W7 A2 (W7-3, EV-15): the decision-tree metric's pure model, shared by the
// editor and the mock world (definition schema `fi-dag-v1`): validation with
// its verbatim messages, the deterministic checks, the readable outline the
// template's instructions carry, numbering, path keys and labels.
//
// {schema: "fi-dag-v1", root, output_type: "score"|"pass_fail", nodes: [
//   {id, kind: "judge", question, judge_model?, answers: [{label, next}×2]}
//   {id, kind: "judge", question, judge_model?, options: [{label, next}×2–10]}
//   {id, kind: "check", check: {type, on, value?, pattern?, path?, op?, n?,
//        case_sensitive?}, answers: [{label: "Pass", next}, {label: "Fail", next}]}
//   {id, kind: "eval", template_id, answers: [{label: "Pass"…}, {label: "Fail"…}]}
//   {id, kind: "leaf", label, score}]}

export const DAG_SCHEMA = "fi-dag-v1";
// A choice question has 2 to 10 options (fi-dag-v1).
export const CHOICE_OPTIONS = { min: 2, max: 10 };
export const CHECK_TYPES = [
  { value: "contains", label: "Contains" },
  { value: "not_contains", label: "Does not contain" },
  { value: "regex", label: "Matches pattern" },
  { value: "equals_expected", label: "Equals expected" },
  { value: "json_path", label: "JSON path" },
  { value: "tool_called", label: "Tool called" },
  { value: "length", label: "Length" },
];
export const NODE_KINDS = [
  { value: "judge", label: "Question" },
  { value: "check", label: "Check" },
  { value: "eval", label: "Eval" },
  { value: "leaf", label: "Score" },
];
const VAR_RE = /{{\s*([^{}]+?)\s*}}/g;

const isLeaf = (n) => n?.kind === "leaf";
/** A node's outgoing answers (yes/no, pass/fail) or a choice's options. */
export const edgesOf = (n) =>
  n?.kind === "judge" && Array.isArray(n.options) && n.options.length
    ? n.options
    : n?.answers ?? [];
export const isChoice = (n) =>
  n?.kind === "judge" && Array.isArray(n.options) && n.options.length > 0;

/** Question numbers: every non-score node in outline order, from 1. */
export function numbersOf(dag) {
  const out = {};
  let i = 0;
  (dag?.nodes ?? []).forEach((n) => {
    if (!isLeaf(n)) {
      i += 1;
      out[n.id] = i;
    }
  });
  return out;
}

/** How a node is named in messages and paths: its number, or its score's label. */
export function nodeName(dag, id, nums = numbersOf(dag)) {
  if (nums[id] != null) return String(nums[id]);
  const n = (dag?.nodes ?? []).find((x) => x.id === id);
  return n?.label ?? id;
}

/**
 * The {{variables}} a definition reads: its questions' and options', each
 * check's `on`, and `expected` for an Equals expected check.
 */
export function variablesOf(dag) {
  const vars = new Set();
  const read = (text) =>
    [...String(text ?? "").matchAll(VAR_RE)].forEach((m) => vars.add(m[1]));
  (dag?.nodes ?? []).forEach((n) => {
    if (n.kind === "judge") {
      read(n.question);
      (n.options ?? []).forEach((o) => read(o?.label));
    }
    if (n.kind === "eval")
      (n.required_keys ?? []).forEach((key) => vars.add(key));
    if (n.kind === "check") {
      const on =
        n.check?.on ||
        (n.check?.type === "tool_called" ? "tool_calls" : "output");
      if (on !== "output") vars.add(on);
    }
    if (n.kind === "check" && n.check?.type === "equals_expected")
      vars.add("expected");
  });
  return [...vars];
}

/** The judge of the first question that names one, or null. */
export const firstJudgeOf = (dag) =>
  (dag?.nodes ?? []).find((n) => n.kind === "judge" && n.judge_model)
    ?.judge_model ?? null;

/**
 * The judge a question without its own asks (Test, a run, Re-score and the
 * estimate alike): the template's model, else the first question's judge;
 * null leaves it to the scorer's default judge.
 */
export const treeJudgeOf = (dag, metric = null) =>
  metric?.custom_template?.model || metric?.model || firstJudgeOf(dag);

const validPattern = (p) => {
  try {
    // eslint-disable-next-line no-new
    new RegExp(p);
    return true;
  } catch {
    return false;
  }
};

/**
 * Every reason a definition cannot be saved or tested (verbatim messages);
 * [] when it can. `mapping` (optional): the variables a binding maps.
 */
export function validateDag(dag, { mapping = null } = {}) {
  const errors = [];
  const add = (m) => {
    if (!errors.includes(m)) errors.push(m);
  };
  const nodes = dag?.nodes ?? [];
  const ids = nodes.map((n) => n.id);
  if (new Set(ids).size !== ids.length) add("Node ids must be unique");
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const root = byId.get(dag?.root);
  if (!dag?.root || !root || isLeaf(root)) add("Start from one question");
  const nums = numbersOf(dag);
  const name = (id) => nodeName(dag, id, nums);
  nodes.forEach((n) => {
    if (isLeaf(n)) {
      const s = Number(n.score);
      if (n.score === "" || n.score == null || !(s >= 0 && s <= 1))
        add("Scores are between 0 and 1");
      return;
    }
    if (n.kind === "judge" && !String(n.question ?? "").trim())
      add("Write the question");
    if (isChoice(n)) {
      if (
        n.options.length < CHOICE_OPTIONS.min ||
        n.options.length > CHOICE_OPTIONS.max
      )
        add("A choice question has 2 to 10 options");
    } else if (edgesOf(n).length !== 2)
      add(
        edgesOf(n).length === 0
          ? "Every path must end in a score"
          : "A yes/no question has exactly two answers",
      );
    if (edgesOf(n).some((a) => !a?.next || !byId.has(a.next)))
      add(`${name(n.id)} has an answer that leads nowhere`);
    if (n.kind === "check") {
      const c = n.check ?? {};
      if (c.type === "regex" && !validPattern(c.pattern ?? ""))
        add(`${c.pattern ?? ""} is not a valid pattern`);
    }
  });
  // Cycles and reach, from the first question.
  const state = new Map();
  const reached = new Set();
  const walk = (id) => {
    reached.add(id);
    state.set(id, "open");
    edgesOf(byId.get(id)).forEach((a) => {
      if (!byId.has(a?.next)) return;
      if (state.get(a.next) === "open")
        add(`${name(id)} loops back to ${name(a.next)}`);
      else if (!state.has(a.next)) walk(a.next);
    });
    state.set(id, "done");
  };
  if (root) walk(root.id);
  if (root)
    nodes
      .filter((n) => !reached.has(n.id))
      .forEach((n) =>
        add(`${name(n.id)} can't be reached from the first question`),
      );
  if (!nodes.some(isLeaf)) add("Every path must end in a score");
  if (mapping)
    variablesOf(dag).forEach((v) => {
      if (v !== "output" && !mapping[v]) add(`{{${v}}} is not mapped`);
    });
  return errors;
}

/** Render `{{var}}` placeholders from a values map (missing ones stay). */
export const renderVars = (text, values = {}) =>
  String(text ?? "").replace(VAR_RE, (m, k) =>
    values[k] == null ? m : String(values[k]),
  );

const textOf = (v) =>
  v == null ? "" : typeof v === "string" ? v : JSON.stringify(v);

function jsonAt(obj, path) {
  const parts = String(path ?? "")
    .replace(/^\$\.?/, "")
    .split(/\.|\[(\d+)\]/)
    .filter((x) => x != null && x !== "");
  let cur = obj;
  for (const k of parts) {
    if (cur == null || typeof cur !== "object" || !(k in cur))
      return { found: false };
    cur = cur[k];
  }
  return { found: true, value: cur };
}

/**
 * One deterministic check (free, no judge call).
 * @param {object} check  {type, on, value, pattern, path, op, n, case_sensitive}
 * @param {{output: any, values?: object, attempt?: object}} ctx
 * @returns {{passed: boolean|null, skipped?: string, reason: string, match?: string}}
 */
export function runCheck(
  check = {},
  { output, values = {}, attempt = null } = {},
) {
  const on = check.on && check.on !== "output" ? check.on : "output";
  const text = on === "output" ? textOf(output) : textOf(values[on]);
  const fold = (s) =>
    check.case_sensitive ? String(s) : String(s).toLowerCase();
  const t = check.type;
  if (t === "contains" || t === "not_contains") {
    const has = fold(text).includes(fold(check.value ?? ""));
    const passed = t === "contains" ? has : !has;
    return {
      passed,
      reason: `${on} ${has ? "contains" : "does not contain"} "${check.value ?? ""}"`,
    };
  }
  if (t === "regex") {
    const m = text.match(
      new RegExp(check.pattern ?? "", check.case_sensitive ? "" : "i"),
    );
    return {
      passed: Boolean(m),
      reason: m
        ? `${on} matches ${check.pattern} ("${m[0]}")`
        : `${on} does not match ${check.pattern}`,
      ...(m ? { match: m[0] } : {}),
    };
  }
  if (t === "equals_expected") {
    const exp = values.expected;
    if (exp == null || !String(exp).trim())
      return {
        passed: null,
        skipped: "no expected value",
        reason: "no expected value",
      };
    const passed = fold(text.trim()) === fold(String(exp).trim());
    return {
      passed,
      reason: passed ? `${on} equals expected` : `${on} differs from expected`,
    };
  }
  if (t === "json_path") {
    let obj = output;
    if (typeof obj === "string" || on !== "output") {
      try {
        obj = JSON.parse(text);
      } catch {
        return { passed: false, reason: `${on} is not JSON` };
      }
    }
    const at = jsonAt(obj, check.path);
    if (check.op === "equals") {
      const passed = at.found && textOf(at.value) === String(check.value ?? "");
      return {
        passed,
        reason: at.found
          ? `${check.path} is ${textOf(at.value)}`
          : `${check.path} is missing`,
      };
    }
    return {
      passed: at.found,
      reason: at.found ? `${check.path} exists` : `${check.path} is missing`,
    };
  }
  if (t === "tool_called") {
    const calls = attempt?.tool_calls;
    if (!Array.isArray(calls) || !calls.length)
      return {
        passed: null,
        skipped: "no tool calls on this try",
        reason: "no tool calls on this try",
      };
    const passed = calls.some((c) => (c?.name ?? c?.tool) === check.value);
    return {
      passed,
      reason: `${check.value} ${passed ? "was" : "was not"} called`,
    };
  }
  if (t === "length") {
    const n = Number(check.n);
    const len = text.length;
    const passed = check.op === "gte" ? len >= n : len <= n;
    return {
      passed,
      reason: `${on} is ${len} characters (${check.op === "gte" ? "≥" : "≤"} ${n})`,
    };
  }
  return {
    passed: null,
    skipped: `unknown check ${t}`,
    reason: `unknown check ${t}`,
  };
}

/** A check in words: `output matches A-\d{4}`. */
export function checkText(c = {}) {
  const on = c.on && c.on !== "output" ? c.on : "output";
  const label = CHECK_TYPES.find((x) => x.value === c.type)?.label ?? c.type;
  if (c.type === "regex") return `${on} matches ${c.pattern ?? ""}`;
  if (c.type === "contains" || c.type === "not_contains")
    return `${on} ${c.type === "contains" ? "contains" : "does not contain"} "${c.value ?? ""}"`;
  if (c.type === "json_path")
    return `${on} ${c.path ?? ""} ${c.op === "equals" ? `equals ${c.value ?? ""}` : "exists"}`;
  if (c.type === "tool_called") return `tool ${c.value ?? ""} was called`;
  if (c.type === "length")
    return `${on} length ${c.op === "gte" ? "≥" : "≤"} ${c.n ?? ""}`;
  if (c.type === "equals_expected") return `${on} equals {{expected}}`;
  return `${label}`;
}

/**
 * The definition as a numbered outline (the template's instructions), with
 * every {{variable}} the nodes read, so required keys and the variable
 * mapping work as for any eval.
 */
export function dagInstructionsOf(dag, { evalNames = {} } = {}) {
  const nums = numbersOf(dag);
  const name = (id) => {
    const n = (dag?.nodes ?? []).find((x) => x.id === id);
    if (!n) return "?";
    if (n.kind === "leaf") return `${n.label} (${Number(n.score).toFixed(2)})`;
    return `question ${nums[id]}`;
  };
  const lines = [
    `Decision tree (${dag?.output_type === "pass_fail" ? "Pass/Fail" : "score"}) on {{output}}:`,
  ];
  (dag?.nodes ?? []).forEach((n) => {
    if (n.kind === "leaf") return;
    const head =
      n.kind === "judge"
        ? n.question
        : n.kind === "check"
          ? `Check: ${checkText(n.check)}`
          : `Eval: ${evalNames[n.template_id] ?? n.template_id}`;
    const edges = edgesOf(n)
      .map((a) => `${a.label} → ${name(a.next)}`)
      .join(" · ");
    lines.push(`${nums[n.id]}. ${head} — ${edges}`);
  });
  const vars = variablesOf(dag).filter((v) => v !== "output");
  if (vars.length)
    lines.push(`Reads: ${vars.map((v) => `{{${v}}}`).join(" ")}`);
  return lines.join("\n");
}

/** `n1:Yes>n2:No>leaf_wrong` — the key a path is grouped by. */
export const pathKeyOf = (path, leaf) =>
  [...path.map((s) => `${s.node_id}:${s.answer}`), leaf?.id ?? "skipped"].join(
    ">",
  );

/** `1 Yes → 2 No → Wrong amount` — a path as people read it. */
export const pathLabelOf = (path, leaf) =>
  [
    ...path.map((s) => `${s.n} ${s.answer ?? "skipped"}`),
    ...(leaf ? [leaf.label] : []),
  ].join(" → ");

/** `Path: 1 Yes → 2 No → Wrong amount (0.00)` — the grid tooltip's line. */
export const dagCompactLine = (t) =>
  t?.leaf
    ? `Path: ${t.path_label} (${Number(t.leaf.score).toFixed(2)})`
    : `Path: ${t?.path_label || "–"} · skipped`;

/**
 * A tree in one line: `Decision tree · 3 questions (1 judged, 1 check, 1
 * eval) · 4 scores` — counts read from the definition.
 */
export function dagSummaryOf(dag) {
  const nodes = dag?.nodes ?? [];
  const count = (k) => nodes.filter((n) => n.kind === k).length;
  const questions = nodes.filter((n) => n.kind !== "leaf").length;
  const parts = [
    count("judge") ? `${count("judge")} judged` : null,
    count("check")
      ? `${count("check")} check${count("check") === 1 ? "" : "s"}`
      : null,
    count("eval")
      ? `${count("eval")} eval${count("eval") === 1 ? "" : "s"}`
      : null,
  ].filter(Boolean);
  const leaves = count("leaf");
  return `Decision tree · ${questions} question${questions === 1 ? "" : "s"}${parts.length ? ` (${parts.join(", ")})` : ""} · ${leaves} score${leaves === 1 ? "" : "s"}`;
}
