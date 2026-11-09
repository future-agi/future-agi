// Check evals of a task row (R2-SPEC §5.3.8 c): four types, the sentence
// each one reads as, live validation and evaluation of `End state equals`
// against a state (the row's start state in the editor preview), and the
// start-state schema check. Pure; never throws.
//
// A check record (the `checks` column, JSON list) is
//   {key, name, type:"check"|"judge", kind?, assert:{…}, description, draft}
// World asserts: `{path, op, value}` (end state; legacy `refunds[order=A-1]`
// filters), `{tool, op, value}` (tool calls), `{judge, model}` (rubric);
// the editor also writes `{code}` (Python) and its own end-state / tool
// fields (`aggregate`, `expected`, `mode`, `times`, `arg`).
import {
  formatPath,
  parsePath,
  resolvePath,
} from "src/sections/common/Values/faPath";
import { parseJson } from "./itemModel";

export const CHECK_TYPES = Object.freeze([
  {
    id: "end_state",
    chip: "End state",
    label: "End state equals",
    icon: "mdi:database-check-outline",
  },
  {
    id: "tool_call",
    chip: "Tool call",
    label: "Tool called / not called",
    icon: "mdi:wrench-check-outline",
  },
  {
    id: "rubric",
    chip: "Rubric",
    label: "Rubric (LLM judge)",
    icon: "mdi:scale-balance",
  },
  {
    id: "python",
    chip: "Python",
    label: "Python function",
    icon: "mdi:language-python",
  },
  // W6 C1: one plain-language sentence, judged on its own by the one
  // scorer (sources/checksJudge.js: scoreEval with the sentence as criteria).
  {
    id: "assertion",
    chip: "Assertion",
    label: "Plain-language assertion (judged)",
    icon: "mdi:format-quote-close-outline",
  },
]);
export const checkTypeMeta = (id) =>
  CHECK_TYPES.find((t) => t.id === id) ?? CHECK_TYPES[0];

export const AGGREGATES = Object.freeze(["value", "count", "exists"]);
export const PYTHON_SIGNATURE = "def check(end_state, transcript, row) -> bool";
export const DEFAULT_PYTHON = `${PYTHON_SIGNATURE}:
    refunds = [r for r in end_state.get("refunds", []) if r.get("order_id") == row["order_id"]]
    return len(refunds) == 1
`;

/** The `checks` cell value → an array of check records. */
export function parseChecks(value) {
  const v = parseJson(value);
  return Array.isArray(v) ? v.filter((c) => c && typeof c === "object") : [];
}

/** `end_state` / `tool_call` / `rubric` / `python` / `assertion`. */
export function checkTypeOf(check) {
  const a = check?.assert ?? {};
  if (CHECK_TYPES.some((t) => t.id === check?.kind)) return check.kind;
  if (typeof a.sentence === "string") return "assertion";
  if (check?.type === "judge" || a.judge) return "rubric";
  if (check?.type === "python" || typeof a.code === "string") return "python";
  if (a.tool) return "tool_call";
  return "end_state";
}

/** True while any check of the list is a draft. */
export const hasDraft = (checks) => (checks ?? []).some((c) => c?.draft);

// Legacy world filters `refunds[order=A-8830]` → FA `refunds[?(@.order=="A-8830")]`.
const LEGACY_FILTER = /\[([A-Za-z_][\w.]*)=([^\]"'?@]+)\]/g;
export function normalizePath(path) {
  return String(path ?? "").replace(LEGACY_FILTER, (_, key, raw) => {
    const value = raw.trim();
    const lit = /^-?\d+(\.\d+)?$/.test(value) ? value : JSON.stringify(value);
    return `[?(@.${key}==${lit})]`;
  });
}

const lit = (v) => {
  if (v === undefined || v === "") return "…";
  if (typeof v === "string") return JSON.stringify(v);
  return JSON.stringify(v);
};

/** The end-state fields of a check: `{path, aggregate, expected}`. */
export function endStateFields(check) {
  const a = check?.assert ?? {};
  const path = normalizePath(a.path ?? "");
  if (a.aggregate)
    return { path, aggregate: a.aggregate, expected: a.expected ?? a.value };
  if (a.op === "count_eq")
    return { path, aggregate: "count", expected: a.value };
  if (a.op === "exists")
    return { path, aggregate: "exists", expected: a.value ?? true };
  if (a.op === "eq") return { path, aggregate: "value", expected: a.value };
  return { path, aggregate: null, op: a.op ?? null, expected: a.value };
}

/** `count(refunds[?(@.order_id=="A-8830")]) == 1`. */
export function endStateExpression({ path, aggregate, op, expected }) {
  const p = path || "…";
  if (aggregate === "count") return `count(${p}) == ${lit(expected)}`;
  if (aggregate === "exists")
    return expected === false ? `not exists(${p})` : `exists(${p})`;
  if (aggregate === "value") return `${p} == ${lit(expected)}`;
  if (op === "mentions") return `${p} mentions ${lit(expected)}`;
  if (op === "covers")
    return `${p} covers ${Array.isArray(expected) ? expected.length : lit(expected)} requests`;
  if (op === "unchanged_except") return `${p} unchanged except what was asked`;
  return `${p} ${op ?? "=="} ${lit(expected)}`;
}

const TIMES = { exactly: "exactly", at_least: "at least", at_most: "at most" };

/** The tool fields of a check: `{tool, mode, times:{op, n}|null, arg:{name, value}|null}`. */
export function toolFields(check) {
  const a = check?.assert ?? {};
  if (a.mode)
    return {
      tool: a.tool ?? "",
      mode: a.mode,
      times: a.times ?? null,
      arg: a.arg ?? null,
    };
  if (a.op === "not_called")
    return { tool: a.tool ?? "", mode: "not_called", times: null, arg: null };
  if (a.op === "returned")
    return {
      tool: a.tool ?? "",
      mode: "returned",
      times: null,
      arg: null,
      value: a.value,
    };
  return {
    tool: a.tool ?? "",
    mode: "called",
    times: null,
    arg: a.value != null ? { name: null, value: a.value } : null,
  };
}

/** `issue_refund called exactly 1 time with order_id = "A-8830"`; `cancel_item not called`. */
export function toolSentence({ tool, mode, times, arg, value }) {
  const t = tool || "…";
  if (mode === "not_called") return `${t} not called`;
  if (mode === "returned") return `${t} returned ${lit(value)}`;
  let s = `${t} called`;
  if (times?.op && Number.isFinite(Number(times.n)) && times.n !== "") {
    const n = Number(times.n);
    s += ` ${TIMES[times.op] ?? times.op} ${n} time${n === 1 ? "" : "s"}`;
  }
  if (arg && (arg.name || arg.value != null)) {
    s += arg.name
      ? ` with ${arg.name} = ${lit(arg.value)}`
      : ` with ${lit(arg.value)}`;
  }
  return s;
}

/** The sentence a check reads as (lists, the Matrix hover, the editor preview). */
export function checkSentence(check) {
  const type = checkTypeOf(check);
  const a = check?.assert ?? {};
  if (type === "rubric")
    return `${a.judge ?? check?.name ?? "Judge"} on ${a.scope === "transcript" ? "the whole transcript" : "the final reply"}`;
  if (type === "python") return "check(end_state, transcript, row) is True";
  if (type === "assertion") return `“${a.sentence ?? ""}” (judged)`;
  if (type === "tool_call") return toolSentence(toolFields(check));
  return endStateExpression(endStateFields(check));
}

// ------------------------------------------------------------ evaluation

/**
 * Evaluate an end-state expression against a state object.
 * @returns {{ok: boolean, actual?: *, pass?: boolean, error?: string}}
 */
export function evaluateEndState({ path, aggregate, expected }, state) {
  const parsed = parsePath(path ?? "");
  if (!path || !parsed.ok) return { ok: false, error: "Invalid path" };
  const s = parseJson(state) ?? state;
  if (!s || typeof s !== "object")
    return { ok: false, error: "No state to check" };
  const steps = parsed.column
    ? [{ type: "key", key: parsed.column }, ...parsed.steps]
    : parsed.steps;
  const res = resolvePath(s, steps, { all: true });
  const values = res.found ? res.values ?? [res.value] : [];
  if (aggregate === "count") {
    const actual = res.multi
      ? values.length
      : Array.isArray(res.value)
        ? res.value.length
        : res.found
          ? 1
          : 0;
    return { ok: true, actual, pass: actual === Number(expected) };
  }
  if (aggregate === "exists") {
    const actual = res.multi ? values.length > 0 : res.found;
    return { ok: true, actual, pass: actual === (expected !== false) };
  }
  // W6 C1: a filter that picks one record compares that record's value
  // (`refunds[?(@.order=="A-1")].amount == 89`), not a one-item list.
  const actual = res.found
    ? res.multi
      ? values.length === 1
        ? values[0]
        : values
      : res.value
    : undefined;
  const pass =
    actual !== undefined && JSON.stringify(actual) === JSON.stringify(expected);
  return { ok: true, actual, pass };
}

/** `0` / `true` / `"A-8830"` / `not found` for the preview `(on the start state: 0)`. */
export function actualText(result) {
  if (!result?.ok) return result?.error ?? "—";
  if (result.actual === undefined) return "not found";
  if (typeof result.actual === "string") return JSON.stringify(result.actual);
  return JSON.stringify(result.actual);
}

// ------------------------------------------------------------- editor

const KEY = /^[a-z][a-z0-9_]{0,47}$/;

/** An editor draft from a check (Edit) or blank (`+ Add check`). */
export function draftFromCheck(check, { defaults = {} } = {}) {
  const type = check ? checkTypeOf(check) : defaults.type ?? "end_state";
  const a = check?.assert ?? {};
  const es = endStateFields(check);
  const tf = toolFields(check);
  return {
    type,
    key: check?.key ?? "",
    name: check?.name ?? "",
    description: check?.description ?? "",
    draft: check ? Boolean(check.draft) : true,
    path: es.path || defaults.path || "",
    aggregate: es.aggregate ?? "count",
    expected:
      es.expected === undefined || es.expected === null
        ? ""
        : typeof es.expected === "string"
          ? es.expected
          : JSON.stringify(es.expected),
    tool: tf.tool || defaults.tool || "",
    toolMode: tf.mode === "not_called" ? "not_called" : "called",
    timesOp: tf.times?.op ?? "exactly",
    timesN: tf.times?.n != null ? String(tf.times.n) : "1",
    argName: tf.arg?.name ?? defaults.argName ?? "",
    argValue:
      tf.arg?.value != null ? String(tf.arg.value) : defaults.argValue ?? "",
    judge: a.judge ?? "",
    judgeId: a.judge_id ?? null,
    scope: a.scope === "transcript" ? "transcript" : "final_reply",
    code: typeof a.code === "string" ? a.code : DEFAULT_PYTHON,
    sentence: typeof a.sentence === "string" ? a.sentence : "",
  };
}

/** Typed expected value: numbers, booleans, null and JSON literals; text otherwise. */
export function parseExpected(text) {
  const s = String(text ?? "").trim();
  if (s === "") return undefined;
  try {
    return JSON.parse(s);
  } catch {
    return s;
  }
}

/**
 * Live validation: `{valid, reason, field}` (`reason` is the Save button's
 * tooltip; `field` the input that shows it).
 */
export function validateDraft(d, { existingKeys = [] } = {}) {
  if (!KEY.test(d?.key ?? ""))
    return {
      valid: false,
      field: "key",
      reason: d?.key
        ? "Name: lowercase letters, digits and _ (e.g. refund_once)"
        : "Name the check",
    };
  if (existingKeys.includes(d.key))
    return {
      valid: false,
      field: "key",
      reason: `A check named ${d.key} already exists`,
    };
  if (d.type === "end_state") {
    const p = parsePath(d.path ?? "");
    if (!d.path)
      return {
        valid: false,
        field: "path",
        reason: "Enter a path into the end state",
      };
    if (!p.ok) return { valid: false, field: "path", reason: "Invalid path" };
    if (d.aggregate !== "exists" && parseExpected(d.expected) === undefined)
      return {
        valid: false,
        field: "expected",
        reason: "Enter the expected value",
      };
    if (d.aggregate === "count" && !Number.isInteger(parseExpected(d.expected)))
      return {
        valid: false,
        field: "expected",
        reason: "A count is a whole number",
      };
  }
  if (d.type === "tool_call") {
    if (!d.tool) return { valid: false, field: "tool", reason: "Pick a tool" };
    if (d.toolMode === "called") {
      const n = Number(d.timesN);
      if (d.timesN !== "" && (!Number.isInteger(n) || n < 0))
        return {
          valid: false,
          field: "timesN",
          reason: "Times is a whole number",
        };
      if (d.argValue !== "" && !d.argName)
        return { valid: false, field: "argName", reason: "Name the argument" };
    }
  }
  if (d.type === "rubric" && !d.judge)
    return { valid: false, field: "judge", reason: "Pick an eval" };
  if (d.type === "assertion" && !String(d.sentence ?? "").trim())
    return {
      valid: false,
      field: "sentence",
      reason: "Write the sentence the reply must hold to",
    };
  if (d.type === "python") {
    if (!/def\s+check\s*\(/.test(d.code ?? ""))
      return {
        valid: false,
        field: "code",
        reason: "Define def check(end_state, transcript, row)",
      };
  }
  return { valid: true, reason: null, field: null };
}

/** A check record from an editor draft (always `draft: true` until reviewed). */
export function buildCheck(d) {
  const base = {
    key: d.key,
    name: d.name?.trim() || d.key,
    description: d.description?.trim() || "",
    draft: d.draft !== false,
  };
  if (d.type === "rubric")
    return {
      ...base,
      type: "judge",
      kind: "rubric",
      assert: {
        judge: d.judge,
        ...(d.judgeId ? { judge_id: d.judgeId } : {}),
        scope: d.scope === "transcript" ? "transcript" : "final_reply",
      },
    };
  if (d.type === "python")
    return { ...base, type: "check", kind: "python", assert: { code: d.code } };
  if (d.type === "assertion")
    return {
      ...base,
      type: "judge",
      kind: "assertion",
      assert: { sentence: String(d.sentence).trim() },
    };
  if (d.type === "tool_call") {
    const called = d.toolMode !== "not_called";
    return {
      ...base,
      type: "check",
      kind: "tool_call",
      assert: {
        tool: d.tool,
        mode: called ? "called" : "not_called",
        ...(called && d.timesN !== ""
          ? { times: { op: d.timesOp ?? "exactly", n: Number(d.timesN) } }
          : {}),
        ...(called && d.argName
          ? { arg: { name: d.argName, value: parseExpected(d.argValue) ?? "" } }
          : {}),
      },
    };
  }
  return {
    ...base,
    type: "check",
    kind: "end_state",
    assert: {
      path: formatCanonical(d.path),
      aggregate: d.aggregate,
      ...(d.aggregate === "exists"
        ? {}
        : { expected: parseExpected(d.expected) }),
    },
  };
}

const formatCanonical = (path) => {
  const p = parsePath(path ?? "");
  return p.ok ? formatPath(p.column, p.steps) : path;
};

/** Replace the check with the same key, or append it. */
export function upsertCheck(checks, check) {
  const list = [...(checks ?? [])];
  const i = list.findIndex((c) => c.key === check.key);
  if (i >= 0) list[i] = check;
  else list.push(check);
  return list;
}

/** Every check reviewed (`draft: false`). */
export const markReviewed = (checks) =>
  (checks ?? []).map((c) => ({ ...c, draft: false }));

/**
 * `Apply to all rows in category refund`: `[{rowId, before, value}]` for
 * every row of the category (the open row included), each row's checks
 * with this one upserted. Rows: `{row_id, category, checks}` (cell text).
 */
export function applyToCategory(rows, check, category) {
  return (rows ?? [])
    .filter((r) => r.category === category)
    .map((r) => ({
      rowId: r.row_id,
      before: r.checks ?? "",
      value: JSON.stringify(upsertCheck(parseChecks(r.checks), check)),
    }));
}

// --------------------------------------------------------- start state

const typeOfJson = (v) =>
  v === null ? "null" : Array.isArray(v) ? "array" : typeof v;

/**
 * Keys of a start state that do not match the environment's schema:
 * `{missing: ["refunds"], wrongType: [{key, expected, actual}]}` or null
 * when it matches (or there is nothing to check).
 */
export function startStateIssues(startState, schema) {
  const s = parseJson(startState);
  if (!schema || typeof schema !== "object") return null;
  if (!s || typeof s !== "object" || Array.isArray(s))
    return { missing: [...(schema.required ?? [])], wrongType: [] };
  const missing = (schema.required ?? []).filter(
    (k) => !Object.prototype.hasOwnProperty.call(s, k),
  );
  const wrongType = Object.entries(schema.properties ?? {})
    .filter(([k, p]) => p?.type && Object.prototype.hasOwnProperty.call(s, k))
    .filter(([k, p]) => {
      const t = typeOfJson(s[k]);
      return p.type === "integer" ? !Number.isInteger(s[k]) : t !== p.type;
    })
    .map(([k, p]) => ({ key: k, expected: p.type, actual: typeOfJson(s[k]) }));
  return missing.length || wrongType.length ? { missing, wrongType } : null;
}

/** `Start state doesn't match the environment schema: missing refunds`. */
export function startStateWarning(issues) {
  if (!issues) return null;
  const parts = [];
  if (issues.missing.length) parts.push(`missing ${issues.missing.join(", ")}`);
  issues.wrongType.forEach((w) =>
    parts.push(`${w.key} is ${w.actual}, not ${w.expected}`),
  );
  return `Start state doesn't match the environment schema: ${parts.join("; ")}`;
}
