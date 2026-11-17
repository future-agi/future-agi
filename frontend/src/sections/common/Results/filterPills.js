// Results filter pills (PRODUCT-SPEC §3.10.5, C2). Pills travel as-is in
// the `filters=` URL param and the R3/R5 `filters` query (a JSON array);
// they AND together. The mock and the backend apply them with
// results-math `applyPills`; this module only builds, parses and names them.
import { expressionPillLabel } from "src/sections/common/filters/expressionPill";

/**
 * Pill types and their fields. `run` is a run id, "any_candidate" (default)
 * or "any_run".
 */
export const PILL_TYPES = {
  movement: { fields: ["metric", "dir", "run"], runScoped: true },
  fails: { fields: ["metric", "run"], runScoped: true },
  score: { fields: ["metric", "op", "value", "run"], runScoped: true },
  // W1-7: `cause` (errorCause) keeps the rows that errored with it.
  error: { fields: ["run", "cause"], runScoped: true },
  flaky: { fields: ["run"], runScoped: true },
  tool_error: { fields: ["run", "tool"], runScoped: true },
  split: { fields: ["values"], runScoped: false },
  meta: { fields: ["key", "op", "value"], runScoped: false },
  human: { fields: ["label", "op", "value", "run"], runScoped: true },
  pref: { fields: ["run", "value", "source"], runScoped: true },
  pref_disagree: { fields: ["run"], runScoped: true },
  latency: { fields: ["op", "value", "run"], runScoped: true },
  cost: { fields: ["op", "value", "run"], runScoped: true },
  // R2: exactly these rows (`Rows (6)` / `Failure mode: … (13)`); the
  // dataset grid's `system:row_ids` filter carries the same ids.
  rows: { fields: ["ids", "label"], runScoped: false },
  // integration S1: an expression (common/filters/expression.js), evaluated by the
  // one evaluator (applyPills case "expression").
  expression: { fields: ["expr", "label"], runScoped: false },
};

export const ANY_CANDIDATE = "any_candidate";
export const ANY_RUN = "any_run";
export const NO_SPLIT = "__none__";
export const MOVEMENT_DIRS = ["improved", "regressed", "changed"];

const OP_TEXT = {
  lt: "<",
  "<": "<",
  lte: "≤",
  "<=": "≤",
  gt: ">",
  ">": ">",
  gte: "≥",
  ">=": "≥",
  eq: "=",
  "=": "=",
  ne: "≠",
  "!=": "≠",
  in: "in",
};

const isValidPill = (p) =>
  !!p &&
  typeof p === "object" &&
  Object.prototype.hasOwnProperty.call(PILL_TYPES, p.type);

/** Keep only a pill's known fields, in a stable order. */
function normalizePill(p) {
  const out = { type: p.type };
  PILL_TYPES[p.type].fields.forEach((f) => {
    if (p[f] !== undefined && p[f] !== null && p[f] !== "") out[f] = p[f];
  });
  return out;
}

/**
 * Parse the `filters` param. Accepts a JSON string (URI-encoded or not), an
 * array, or empty. Unknown types and malformed input are dropped, never thrown.
 * @returns {object[]}
 */
export function parsePills(str) {
  if (!str) return [];
  let raw = str;
  if (typeof raw === "string") {
    let text = raw.trim();
    if (!text.startsWith("[")) {
      try {
        text = decodeURIComponent(text);
      } catch {
        return [];
      }
    }
    try {
      raw = JSON.parse(text);
    } catch {
      return [];
    }
  }
  if (!Array.isArray(raw)) return [];
  return raw.filter(isValidPill).map(normalizePill);
}

/** JSON for the URL / API; "" for no pills (so the param can be dropped). */
export function serializePills(arr) {
  const pills = (arr ?? []).filter(isValidPill).map(normalizePill);
  return pills.length ? JSON.stringify(pills) : "";
}

/** Stable identity of a pill (React keys, toggling, de-duplication). */
export const pillKey = (pill) => JSON.stringify(normalizePill(pill));

export const samePill = (a, b) =>
  isValidPill(a) && isValidPill(b) && pillKey(a) === pillKey(b);

/** Add the pill, or remove it when an equal one is already there. */
export function togglePill(pills, pill) {
  const list = pills ?? [];
  return list.some((p) => samePill(p, pill))
    ? list.filter((p) => !samePill(p, pill))
    : [...list, normalizePill(pill)];
}

/** Replace any movement pill on the same metric + run (one direction at a time). */
export function setMovementPill(pills, pill) {
  const rest = (pills ?? []).filter(
    (p) =>
      !(
        p.type === "movement" &&
        p.metric === pill.metric &&
        (p.run ?? ANY_CANDIDATE) === (pill.run ?? ANY_CANDIDATE)
      ),
  );
  return [...rest, normalizePill(pill)];
}

/**
 * R2 `rows` pill: exactly these row (example) ids; `label` names the set
 * (`Failure mode: wrong order id`).
 */
export function rowsPill(ids, { label } = {}) {
  const pill = { type: "rows", ids: [...new Set((ids ?? []).filter(Boolean))] };
  if (label) pill.label = label;
  return pill;
}

/** Row ids of the first `rows` pill, or null. */
export function rowIdsFromPills(pills) {
  const pill = (pills ?? []).find((p) => p?.type === "rows");
  return pill && Array.isArray(pill.ids) ? pill.ids : null;
}

/** `{type:"movement", metric, dir, run}` (run defaults to any candidate). */
export function movementPill(metric, dir, run = ANY_CANDIDATE) {
  return { type: "movement", metric, dir, run };
}

const asMap = (listOrMap, idKey) => {
  if (!listOrMap) return {};
  if (Array.isArray(listOrMap))
    return Object.fromEntries(listOrMap.map((x) => [x?.[idKey], x]));
  return listOrMap;
};

/**
 * Plain-text label: `Regressed on Task completion · #4A vs #3A`,
 * `Split: hard`, `Errored · any candidate`.
 * @param {object} pill
 * @param {{runs?: object[]|Object<string,object>, metrics?: object[]|Object<string,object>,
 *   baselineId?: string, labels?: Object<string,string>}} [ctx]
 *   runs by `run_id` (canonical Run with `label`), metrics by `key`,
 *   labels: annotation label id -> name
 */
export function pillLabel(pill, { runs, metrics, baselineId, labels } = {}) {
  if (!isValidPill(pill)) return "";
  const runMap = asMap(runs, "run_id");
  const metricMap = asMap(metrics, "key");
  const runText = (id) => {
    if (!id || id === ANY_CANDIDATE) return "any candidate";
    if (id === ANY_RUN) return "any run";
    return runMap[id]?.label ?? id;
  };
  const metricText = (k) => metricMap[k]?.name ?? k ?? "";
  const vs = (id) => {
    const base = baselineId ? runMap[baselineId]?.label ?? baselineId : null;
    return base && id !== baselineId
      ? `${runText(id)} vs ${base}`
      : runText(id);
  };
  const op = (o) => OP_TEXT[o] ?? o ?? "=";
  switch (pill.type) {
    case "movement": {
      const dir =
        pill.dir === "changed"
          ? "Changed"
          : pill.dir === "improved"
            ? "Improved"
            : "Regressed";
      return `${dir} on ${metricText(pill.metric)} · ${vs(pill.run)}`;
    }
    case "fails":
      return `Fails ${metricText(pill.metric)} · ${runText(pill.run)}`;
    case "score":
      return `${metricText(pill.metric)} ${op(pill.op)} ${pill.value} · ${runText(pill.run)}`;
    case "error":
      return pill.cause
        ? `Errored: ${pill.cause} · ${runText(pill.run)}`
        : `Errored · ${runText(pill.run)}`;
    case "flaky":
      return `Flaky · ${runText(pill.run)}`;
    case "tool_error":
      return pill.tool
        ? `Tool errors: ${pill.tool} · ${runText(pill.run)}`
        : `Tool errors · ${runText(pill.run)}`;
    case "split": {
      const values = (pill.values ?? []).map((v) =>
        v === NO_SPLIT ? "no split" : v,
      );
      return values.length ? `Split: ${values.join(", ")}` : "Split";
    }
    case "meta": {
      const value = Array.isArray(pill.value)
        ? pill.value.join(", ")
        : pill.value;
      return `${pill.key} ${op(pill.op)} ${value}`;
    }
    case "human": {
      const name = labels?.[pill.label] ?? metricText(pill.label);
      return `${name} ${op(pill.op)} ${pill.value} · ${runText(pill.run)}`;
    }
    case "pref": {
      const who =
        pill.value === "tie"
          ? "Tie"
          : pill.value === "none"
            ? "No preference"
            : `${runText(pill.value === "A" || pill.value === "B" ? pill.run : pill.value ?? pill.run)} preferred`;
      const src =
        pill.source === "judge"
          ? " · LLM judge"
          : pill.source === "human"
            ? " · human"
            : "";
      return `${who}${src}`;
    }
    case "pref_disagree":
      return "LLM judge disagrees with reviewers";
    case "latency":
      return `Latency ${op(pill.op)} ${formatSecondsValue(pill.value)} · ${runText(pill.run)}`;
    case "cost":
      return `Cost ${op(pill.op)} $${pill.value} · ${runText(pill.run)}`;
    case "rows": {
      const n = (pill.ids ?? []).length;
      return `${pill.label ?? "Rows"} (${n.toLocaleString("en-US")})`;
    }
    // integration S2: `Expression: <first 60 characters>…`.
    case "expression":
      return expressionPillLabel(pill);
    default:
      return pill.type;
  }
}

/** Latency pill values are milliseconds; show them like the grid does. */
function formatSecondsValue(ms) {
  const n = Number(ms);
  if (!Number.isFinite(n)) return String(ms ?? "");
  return n >= 1000 ? `${Number((n / 1000).toFixed(2))} s` : `${n} ms`;
}
