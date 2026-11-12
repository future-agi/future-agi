// W5-2 · custom columns (AG-16; W5-SCOPE §5 A4): the definition, its checks,
// type inference and the path suggestions. Pure. Values are computed only in
// MOCK/fixtures/world/w5CustomColumns.customColumnValues; the expression
// language reads a column as `col.<name>` (or its bare name when free).
//
// A definition: {id, name, kind: "path" | "expression", path?, expr?,
//   applies_to: "run" | "row", type: "number" | "text" | "boolean",
//   created_by, created_at}
import { listPaths, parsePath } from "src/sections/common/Values/faPath";
import { compileExpression } from "src/sections/common/filters/expression";

export const COLUMN_KINDS = ["path", "expression"];
export const APPLIES_TO = ["run", "row"];
export const NAME_RULE = /^[A-Za-z_][A-Za-z0-9_]*$/;
export const NAME_MAX = 64;
// The outputs path suggestions read (W5-SCOPE §5 A4: "the first 20 outputs").
export const PATH_SAMPLE = 20;

export const NAME_ERROR =
  "Name it with letters, digits and _ (starting with a letter or _).";

/** The steps of a path, or null when it does not parse. */
export function pathSteps(path) {
  const parsed = parsePath(String(path ?? "").trim());
  return parsed.ok && parsed.steps.length ? parsed.steps : null;
}

/**
 * Where a path starts: `output…` reads each run's result, every other head
 * (`input.<col>`, `expected.<col>`, `metadata.<col>`, a column name) the row.
 */
export const pathAppliesTo = (path) =>
  pathSteps(path)?.[0]?.key === "output" ? "run" : "row";

/** One definition from a form or a PUT body (trimmed, defaults filled). */
export function normalizeColumn(input = {}, { now, user } = {}) {
  const kind = COLUMN_KINDS.includes(input.kind) ? input.kind : "expression";
  const base = {
    id: input.id || null,
    name: String(input.name ?? "").trim(),
    kind,
    applies_to: APPLIES_TO.includes(input.applies_to)
      ? input.applies_to
      : kind === "path"
        ? pathAppliesTo(input.path)
        : "run",
    type: ["number", "text", "boolean"].includes(input.type)
      ? input.type
      : "text",
    created_by: input.created_by ?? user ?? null,
    created_at: input.created_at ?? now ?? null,
  };
  return kind === "path"
    ? { ...base, path: String(input.path ?? "").trim() }
    : { ...base, expr: String(input.expr ?? "").trim() };
}

/**
 * The first problem in a list of definitions, or null.
 * @param {Array} defs  normalized definitions
 * @param {(defs) => object} catalogFor  the results catalogue with these defs
 * @returns {{id, name, message} | null}
 */
export function columnsError(defs, catalogFor) {
  const seen = new Set();
  for (const d of defs) {
    const fail = (message) => ({ id: d.id, name: d.name, message });
    if (!d.name || !NAME_RULE.test(d.name) || d.name.length > NAME_MAX)
      return fail(NAME_ERROR);
    const key = d.name.toLowerCase();
    if (seen.has(key)) return fail(`Two columns are named “${d.name}”.`);
    seen.add(key);
    if (d.kind === "path") {
      if (!d.path) return fail(`Column “${d.name}”: pick a path.`);
      if (!pathSteps(d.path))
        return fail(`Column “${d.name}”: “${d.path}” is not a path.`);
    } else if (!d.expr) {
      return fail(`Column “${d.name}”: type an expression.`);
    }
  }
  const catalog = catalogFor ? catalogFor(defs) : null;
  if (!catalog) return null;
  for (const d of defs) {
    if (d.kind !== "expression") continue;
    // `col.<self>` inside its own expression is the cycle error.
    const compiled = compileExpression(d.expr, catalog);
    if (compiled.error)
      return { id: d.id, name: d.name, message: compiled.error.message };
  }
  return null;
}

/** The type of a column from its computed values (nulls ignored). */
export function inferType(values) {
  const xs = (values ?? []).filter((v) => v !== null && v !== undefined);
  if (!xs.length) return "text";
  if (xs.every((v) => typeof v === "boolean")) return "boolean";
  if (xs.every((v) => typeof v === "number" && Number.isFinite(v)))
    return "number";
  return "text";
}

/**
 * Path suggestions from outputs: `output` plus `output.<key path>` for the
 * JSON keys of the first PATH_SAMPLE outputs (JSON text is parsed), with
 * how many outputs carry each path.
 */
export function outputPathOptions(outputs = []) {
  const counts = new Map();
  outputs.slice(0, PATH_SAMPLE).forEach((o) => {
    const paths = new Set(
      listPaths([o])
        .map((p) => p.path)
        .filter(Boolean),
    );
    paths.forEach((p) => counts.set(p, (counts.get(p) ?? 0) + 1));
  });
  return [...counts]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([p, n]) => ({
      path: p.startsWith("[") ? `output${p}` : `output.${p}`,
      count: n,
    }));
}

/** `Column “intent_out” saved for everyone on this dataset`. */
export const savedToast = (name) =>
  `Column “${name}” saved for everyone on this dataset`;

/** `Column “intent_out” removed for everyone on this dataset` (HO R1). */
export const removedToast = (name) =>
  `Column “${name}” removed for everyone on this dataset`;

/** Replace or add one definition (by id, else by name). */
export function upsertColumn(defs, next) {
  const i = defs.findIndex(
    (d) =>
      (next.id && d.id === next.id) ||
      d.name.toLowerCase() === next.name.toLowerCase(),
  );
  if (i === -1) return [...defs, next];
  const out = [...defs];
  out[i] = { ...defs[i], ...next, id: defs[i].id };
  return out;
}
