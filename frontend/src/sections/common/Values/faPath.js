// FA Path: one path syntax everywhere (R2-SPEC P6). A JSONPath-compatible
// subset, case-sensitive:
//
//   col · col.key · col["key.with.dots"] · col['k'] · col[0] · col[-1]
//   col[*] · col[1:3] · col[*].name · col.items[*].tool_calls[*].name
//   col[?(@.role=="user")] · col[?(@.amount==129)]   (equality only)
//
// An optional leading `$` / `$.` is accepted and ignored (Langfuse-style
// pastes and the experiment mapping format `<colId>::$.<path>`), as are the
// `{{ … }}` braces of prompt variables. Whitespace around tokens is ignored.
// Pure; never throws.
//
// Frozen API (R2 manifest row 38; L6 may only add exports): parsePath,
// resolvePath, formatPath, listPaths, toLegacyMapping, fromLegacyMapping,
// LEGACY_SEPARATOR.

export const LEGACY_SEPARATOR = "::";

const IDENT_START = /[A-Za-z0-9_]/;
const IDENT_CHAR = /[A-Za-z0-9_-]/;
const IDENTIFIER = /^[A-Za-z_][A-Za-z0-9_]*$/;

const fail = (ch, pos) => ({
  ok: false,
  column: null,
  steps: [],
  error:
    ch === undefined || ch === ""
      ? `Unexpected end at ${pos}`
      : `Unexpected ${JSON.stringify(ch)} at ${pos}`,
  pos,
});

/**
 * Parse an FA Path.
 * @param {string} str
 * @param {{columns?: string[]}} [opts]  column names; when the first token
 *   names one, it becomes `column` and the rest are `steps`
 * @returns {{ok: boolean, column: string|null, steps: Array<
 *   {type:"key", key:string} | {type:"index", index:number} |
 *   {type:"wildcard"} | {type:"slice", start:number|null, end:number|null} |
 *   {type:"filter", path:string[], value:string|number}>,
 *   error?: string, pos?: number}}
 */
export function parsePath(str, { columns } = {}) {
  const src = typeof str === "string" ? str : "";
  let i = 0;
  const peek = () => src[i];
  const skipWs = () => {
    while (i < src.length && /\s/.test(src[i])) i += 1;
  };

  skipWs();
  // `{{ path }}` (prompt variables)
  let closeBraces = false;
  if (src.startsWith("{{", i)) {
    i += 2;
    closeBraces = true;
    skipWs();
  }
  // optional `$` / `$.`
  if (peek() === "$") {
    i += 1;
    skipWs();
    if (peek() === ".") i += 1;
    skipWs();
  }

  const steps = [];
  const readIdent = () => {
    const start = i;
    if (!IDENT_START.test(peek() ?? "")) return null;
    while (i < src.length && IDENT_CHAR.test(src[i])) i += 1;
    return src.slice(start, i);
  };
  const readQuoted = () => {
    const quote = peek();
    if (quote !== '"' && quote !== "'") return null;
    i += 1;
    let out = "";
    while (i < src.length && src[i] !== quote) {
      if (src[i] === "\\" && i + 1 < src.length) {
        out += src[i + 1];
        i += 2;
      } else {
        out += src[i];
        i += 1;
      }
    }
    if (src[i] !== quote) return { error: fail(src[i], i) };
    i += 1;
    return { value: out };
  };
  const readInt = () => {
    const start = i;
    if (peek() === "-") i += 1;
    while (i < src.length && /[0-9]/.test(src[i])) i += 1;
    const text = src.slice(start, i);
    if (!text || text === "-") {
      i = start;
      return null;
    }
    return Number(text);
  };
  const readNumberLiteral = () => {
    const m = /^-?\d+(\.\d+)?/.exec(src.slice(i));
    if (!m) return null;
    i += m[0].length;
    return Number(m[0]);
  };

  // [ … ] after the `[`
  const readBracket = () => {
    skipWs();
    const ch = peek();
    if (ch === "*") {
      i += 1;
      skipWs();
      if (peek() !== "]") return fail(peek(), i);
      i += 1;
      return { type: "wildcard" };
    }
    if (ch === '"' || ch === "'") {
      const q = readQuoted();
      if (q.error) return q.error;
      skipWs();
      if (peek() !== "]") return fail(peek(), i);
      i += 1;
      return { type: "key", key: q.value };
    }
    if (ch === "?") {
      i += 1;
      skipWs();
      if (peek() !== "(") return fail(peek(), i);
      i += 1;
      skipWs();
      if (peek() !== "@") return fail(peek(), i);
      i += 1;
      const path = [];
      for (;;) {
        skipWs();
        if (peek() === ".") {
          i += 1;
          skipWs();
          const id = readIdent();
          if (id === null) return fail(peek(), i);
          path.push(id);
        } else if (peek() === "[") {
          i += 1;
          skipWs();
          const q = readQuoted();
          if (!q) return fail(peek(), i);
          if (q.error) return q.error;
          skipWs();
          if (peek() !== "]") return fail(peek(), i);
          i += 1;
          path.push(q.value);
        } else break;
      }
      if (!path.length) return fail(peek(), i);
      skipWs();
      if (!src.startsWith("==", i)) return fail(peek(), i);
      i += 2;
      skipWs();
      let value;
      if (peek() === '"' || peek() === "'") {
        const q = readQuoted();
        if (q.error) return q.error;
        value = q.value;
      } else {
        value = readNumberLiteral();
        if (value === null) return fail(peek(), i);
      }
      skipWs();
      if (peek() !== ")") return fail(peek(), i);
      i += 1;
      skipWs();
      if (peek() !== "]") return fail(peek(), i);
      i += 1;
      return { type: "filter", path, value };
    }
    // index or slice
    const a = readInt();
    skipWs();
    if (peek() === ":") {
      i += 1;
      skipWs();
      const b = readInt();
      skipWs();
      if (peek() !== "]") return fail(peek(), i);
      i += 1;
      return { type: "slice", start: a, end: b };
    }
    if (a === null) return fail(peek(), i);
    if (peek() !== "]") return fail(peek(), i);
    i += 1;
    return { type: "index", index: a };
  };

  // first token: identifier or bracket
  skipWs();
  if (i >= src.length || (closeBraces && src.startsWith("}}", i)))
    return { ...fail(undefined, i), error: "Empty path" };
  let first = true;
  while (i < src.length) {
    skipWs();
    if (i >= src.length) break;
    if (closeBraces && src.startsWith("}}", i)) {
      i += 2;
      skipWs();
      if (i < src.length) return fail(peek(), i);
      closeBraces = false;
      break;
    }
    const ch = peek();
    if (ch === "[") {
      i += 1;
      const step = readBracket();
      if (step.ok === false) return step;
      steps.push(step);
    } else if (ch === "." && !first) {
      i += 1;
      skipWs();
      const id = readIdent();
      if (id === null) return fail(peek(), i);
      steps.push({ type: "key", key: id });
    } else if (first) {
      const id = readIdent();
      if (id === null) return fail(ch, i);
      steps.push({ type: "key", key: id });
    } else {
      return fail(ch, i);
    }
    first = false;
  }
  if (closeBraces) return fail(undefined, i);
  if (!steps.length) return { ...fail(undefined, i), error: "Empty path" };

  let column = null;
  let rest = steps;
  if (
    Array.isArray(columns) &&
    steps[0].type === "key" &&
    columns.includes(steps[0].key)
  ) {
    column = steps[0].key;
    rest = steps.slice(1);
  }
  return { ok: true, column, steps: rest };
}

// ---------------------------------------------------------------- resolve

const parseJsonText = (v) => {
  if (typeof v !== "string") return v;
  const t = v.trim();
  if (!(t.startsWith("{") || t.startsWith("["))) return v;
  try {
    return JSON.parse(t);
  } catch {
    return v;
  }
};

const isObject = (v) => v !== null && typeof v === "object";

const filterMatch = (item, path, value) => {
  let cur = parseJsonText(item);
  for (const key of path) {
    if (!isObject(cur)) return false;
    cur = parseJsonText(cur[key]);
  }
  if (cur === value) return true;
  if (typeof value === "number" && typeof cur === "string" && cur.trim() !== "")
    return Number(cur) === value;
  return false;
};

const hasKey = (obj, key) =>
  isObject(obj) && Object.prototype.hasOwnProperty.call(obj, key);

/**
 * Resolve a path against a value. JSON text is parsed first (at every
 * level), so a column stored as JSON text resolves like an object. A
 * wildcard, slice or filter anywhere → `multi:true` and `values` (one
 * flattening level per wildcard). Values are returned typed.
 * @param {*} value
 * @param {string|Array|{steps: Array}} pathOrSteps  a path string (resolved
 *   from its first token), parsed steps, or a parsePath result
 * @param {{all?: boolean}} [opts]  `all`: include `values` for one value too
 * @returns {{found: boolean, value?: *, values?: Array, multi: boolean}}
 */
export function resolvePath(value, pathOrSteps, { all = false } = {}) {
  let steps;
  if (typeof pathOrSteps === "string") {
    const parsed = parsePath(pathOrSteps);
    if (!parsed.ok) return { found: false, multi: false };
    steps = parsed.steps;
  } else if (Array.isArray(pathOrSteps)) {
    steps = pathOrSteps;
  } else if (pathOrSteps && Array.isArray(pathOrSteps.steps)) {
    steps = pathOrSteps.steps;
  } else {
    return { found: false, multi: false };
  }

  let multi = false;
  let current = [parseJsonText(value)];
  for (const step of steps) {
    const next = [];
    current.forEach((raw) => {
      const v = parseJsonText(raw);
      switch (step.type) {
        case "key":
          if (Array.isArray(v) && /^-?\d+$/.test(step.key)) {
            const idx = Number(step.key);
            const at = idx < 0 ? v.length + idx : idx;
            if (at >= 0 && at < v.length) next.push(v[at]);
          } else if (hasKey(v, step.key)) next.push(v[step.key]);
          break;
        case "index":
          if (Array.isArray(v)) {
            const at = step.index < 0 ? v.length + step.index : step.index;
            if (at >= 0 && at < v.length) next.push(v[at]);
          }
          break;
        case "wildcard":
          if (Array.isArray(v)) next.push(...v);
          else if (isObject(v)) next.push(...Object.values(v));
          break;
        case "slice":
          if (Array.isArray(v)) {
            const n = v.length;
            const norm = (x, dflt) =>
              x === null || x === undefined
                ? dflt
                : x < 0
                  ? Math.max(0, n + x)
                  : Math.min(n, x);
            next.push(...v.slice(norm(step.start, 0), norm(step.end, n)));
          }
          break;
        case "filter":
          if (Array.isArray(v))
            next.push(
              ...v.filter((item) => filterMatch(item, step.path, step.value)),
            );
          break;
        default:
          break;
      }
    });
    if (["wildcard", "slice", "filter"].includes(step.type)) multi = true;
    current = next;
    if (!multi && !current.length) return { found: false, multi: false };
  }
  const values = current.map(parseJsonText);
  if (multi)
    return { found: values.length > 0, value: values, values, multi: true };
  if (!values.length) return { found: false, multi: false };
  // `all`: callers that always want a list get `values` for one value too.
  if (all) return { found: true, value: values[0], values, multi: false };
  return { found: true, value: values[0], multi: false };
}

// ----------------------------------------------------------------- format

const quoteKey = (key) => `[${JSON.stringify(key)}]`;

const stepText = (step, isFirst) => {
  switch (step.type) {
    case "key":
      if (IDENTIFIER.test(step.key)) return isFirst ? step.key : `.${step.key}`;
      return quoteKey(step.key);
    case "index":
      return `[${step.index}]`;
    case "wildcard":
      return "[*]";
    case "slice":
      return `[${step.start ?? ""}:${step.end ?? ""}]`;
    case "filter": {
      const at = step.path
        .map((k) => (IDENTIFIER.test(k) ? `.${k}` : quoteKey(k)))
        .join("");
      const v =
        typeof step.value === "number"
          ? String(step.value)
          : JSON.stringify(step.value);
      return `[?(@${at}==${v})]`;
    }
    default:
      return "";
  }
};

/**
 * Canonical text of a path: `expected.citations[0].page`; keys are quoted
 * only when they are not identifiers (`metadata["customer.tier"]`).
 * @param {string|null|Array} column  (or the steps, with no column)
 * @param {Array} steps
 */
export function formatPath(column, steps = []) {
  // `formatPath(steps)` (no column) is accepted too.
  if (Array.isArray(column)) return formatPath(null, column);
  const head = column
    ? IDENTIFIER.test(column)
      ? column
      : quoteKey(column)
    : "";
  return (steps ?? []).reduce(
    (acc, step, idx) => acc + stepText(step, !head && idx === 0),
    head,
  );
}

// ------------------------------------------------------------------- list

const kindOf = (v) => {
  if (v === null || v === undefined) return "null";
  if (Array.isArray(v)) return "array";
  return typeof v === "object" ? "object" : typeof v;
};

/**
 * Every path in sampled values, with the kinds seen at each and one example.
 * Arrays contribute `[*]` paths. Pass an array of sampled values (to list a
 * single array value, wrap it: `[value]`).
 * @param {*|Array} sample  one value, or an array of sampled values
 * @param {{maxDepth?: number, maxPaths?: number}} [opts]
 * @returns {{path: string, kinds: Object<string, number>, example: *}[]}
 */
export function listPaths(sample, { maxDepth = 6, maxPaths = 200 } = {}) {
  const samples = Array.isArray(sample) ? sample : [sample];
  const byPath = new Map();
  const add = (steps, v) => {
    if (!steps.length) return;
    const path = formatPath(null, steps);
    let entry = byPath.get(path);
    if (!entry) {
      if (byPath.size >= maxPaths) return;
      entry = { path, kinds: {}, example: null };
      byPath.set(path, entry);
    }
    const kind = kindOf(v);
    entry.kinds[kind] = (entry.kinds[kind] ?? 0) + 1;
    if (entry.example === null && kind !== "null" && !isObject(v))
      entry.example = v;
  };
  const walk = (raw, steps, depth) => {
    const v = parseJsonText(raw);
    add(steps, v);
    if (depth >= maxDepth || !isObject(v)) return;
    if (Array.isArray(v)) {
      v.forEach((item) =>
        walk(item, [...steps, { type: "wildcard" }], depth + 1),
      );
    } else {
      Object.entries(v).forEach(([k, item]) =>
        walk(item, [...steps, { type: "key", key: k }], depth + 1),
      );
    }
  };
  samples.forEach((s) => walk(s, [], 0));
  return [...byPath.values()];
}

// ----------------------------------------------------------------- legacy

/**
 * The experiment mapping format (variableMapping.js): `<colId>::$.<path>`,
 * or the bare column id when there is no path.
 */
export function toLegacyMapping(colId, path) {
  const p = String(path ?? "")
    .trim()
    .replace(/^\$\.?/, "");
  if (!p) return colId;
  return `${colId}${LEGACY_SEPARATOR}$${p.startsWith("[") ? "" : "."}${p}`;
}

/** `{colId, path}` from `<colId>::$.<path>` (path without the `$.`). */
export function fromLegacyMapping(str) {
  const s = String(str ?? "");
  const at = s.indexOf(LEGACY_SEPARATOR);
  if (at < 0) return { colId: s || null, path: "" };
  return {
    colId: s.slice(0, at) || null,
    path: s.slice(at + LEGACY_SEPARATOR.length).replace(/^\$\.?/, ""),
  };
}
