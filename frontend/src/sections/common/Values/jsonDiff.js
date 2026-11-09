// JSON-aware diff (R2-SPEC P6 §3.6): added / removed / changed paths with
// old → new, ignore paths (globs with `*`), key order ignored, arrays matched
// by index or by key (`id` / `name`), chat arrays aligned per message and
// tool-call arguments diffed as JSON. Pure; never throws.
import { formatPath } from "./faPath";
import { normalizeChat } from "./sniff";

const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

/** Parsed JSON for strings that hold an object / array, else the value. */
export function asJson(v) {
  if (typeof v !== "string") return v;
  const t = v.trim();
  if (!(t.startsWith("{") || t.startsWith("["))) return v;
  try {
    return JSON.parse(t);
  } catch {
    return v;
  }
}

/** True when both sides are JSON objects / arrays (or JSON text of one). */
export function isJsonPair(a, b) {
  const x = asJson(a);
  const y = asJson(b);
  const ok = (v) => v !== null && typeof v === "object";
  return ok(x) && ok(y);
}

const globToRegExp = (glob) =>
  new RegExp(
    `^${String(glob)
      .trim()
      .replace(/[.+?^${}()|[\]\\]/g, "\\$&")
      .replace(/\*/g, ".*")}$`,
  );

/** Parse `usage.*, *.id` into a list of globs. */
export const parseIgnore = (text) =>
  String(text ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

/** True when a path matches one of the ignore globs (`usage.*` also ignores `usage`). */
export function isIgnored(path, globs = []) {
  return globs.some((g) => {
    const re = globToRegExp(g);
    if (re.test(path)) return true;
    // `usage.*` also covers `usage` itself and `a.usage.x` style prefixes
    if (g.endsWith(".*") && path === g.slice(0, -2)) return true;
    return false;
  });
}

const keyOf = (item) =>
  isObj(item) ? item.id ?? item.name ?? item.function?.name ?? null : null;

const normString = (s, { whitespace, caseInsensitive }) => {
  let out = s;
  if (whitespace) out = out.replace(/\s+/g, " ").trim();
  if (caseInsensitive) out = out.toLowerCase();
  return out;
};

/**
 * Diff two JSON values.
 * @param {*} from
 * @param {*} to
 * @param {{ignorePaths?: string[], arrayMatch?: "index"|"key",
 *   ignoreWhitespace?: boolean, ignoreCase?: boolean}} [opts]
 * @returns {{changes: Array<{path: string, op: "added"|"removed"|"changed",
 *   from?: *, to?: *, delta?: number}>, ignored: number,
 *   counts: {added: number, removed: number, changed: number}}}
 */
export function jsonDiff(from, to, opts = {}) {
  const {
    ignorePaths = [],
    arrayMatch = "index",
    ignoreWhitespace = false,
    ignoreCase = false,
  } = opts;
  const changes = [];
  const ignoredPaths = new Set();
  const str = { whitespace: ignoreWhitespace, caseInsensitive: ignoreCase };

  const push = (steps, op, a, b) => {
    const path = formatPath(null, steps) || "$";
    if (isIgnored(path, ignorePaths)) {
      ignoredPaths.add(path);
      return;
    }
    const c = { path, op };
    if (op !== "added") c.from = a;
    if (op !== "removed") c.to = b;
    if (op === "changed" && typeof a === "number" && typeof b === "number")
      c.delta = Math.round((b - a) * 1e6) / 1e6;
    changes.push(c);
  };

  const walk = (a0, b0, steps) => {
    const a = asJson(a0);
    const b = asJson(b0);
    const path = formatPath(null, steps);
    if (steps.length && isIgnored(path, ignorePaths)) {
      if (JSON.stringify(a) !== JSON.stringify(b)) ignoredPaths.add(path);
      return;
    }
    if (Array.isArray(a) && Array.isArray(b)) {
      // chat arrays: normalise (Anthropic / LangChain) and align by index
      const ca = normalizeChat(a);
      const cb = normalizeChat(b);
      const left = ca && cb ? ca.messages : a;
      const right = ca && cb ? cb.messages : b;
      if (arrayMatch === "key" && left.every(keyOf) && right.every(keyOf)) {
        const ka = new Map(left.map((x) => [keyOf(x), x]));
        const kb = new Map(right.map((x) => [keyOf(x), x]));
        ka.forEach((x, k) => {
          const s = [
            ...steps,
            { type: "filter", path: [x.id != null ? "id" : "name"], value: k },
          ];
          if (kb.has(k)) walk(x, kb.get(k), s);
          else push(s, "removed", x);
        });
        kb.forEach((y, k) => {
          if (!ka.has(k))
            push(
              [
                ...steps,
                {
                  type: "filter",
                  path: [y.id != null ? "id" : "name"],
                  value: k,
                },
              ],
              "added",
              undefined,
              y,
            );
        });
        return;
      }
      const n = Math.max(left.length, right.length);
      for (let i = 0; i < n; i += 1) {
        const s = [...steps, { type: "index", index: i }];
        if (i >= left.length) push(s, "added", undefined, right[i]);
        else if (i >= right.length) push(s, "removed", left[i]);
        else walk(left[i], right[i], s);
      }
      return;
    }
    if (isObj(a) && isObj(b)) {
      const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
      keys.forEach((k) => {
        const s = [...steps, { type: "key", key: k }];
        if (!(k in a)) push(s, "added", undefined, b[k]);
        else if (!(k in b)) push(s, "removed", a[k]);
        else walk(a[k], b[k], s);
      });
      return;
    }
    if (typeof a === "string" && typeof b === "string") {
      if (normString(a, str) !== normString(b, str))
        push(steps, "changed", a, b);
      return;
    }
    if (JSON.stringify(a) !== JSON.stringify(b)) push(steps, "changed", a, b);
  };

  walk(from, to, []);
  const counts = { added: 0, removed: 0, changed: 0 };
  changes.forEach((c) => {
    counts[c.op] += 1;
  });
  return { changes, ignored: ignoredPaths.size, counts };
}

/** `2 changed · 1 added · 0 removed`. */
export const diffCountsText = ({ changed = 0, added = 0, removed = 0 } = {}) =>
  `${changed} changed · ${added} added · ${removed} removed`;
