// W5-1 expression language · the evaluator (W5-SCOPE §5 A0). A tree walk
// over a fixed operator table; no eval. Pure.
//
// scope = { catalog, runIds, baselineId, readRow(field), readRun(runId, field) }
//   `field` is the catalogue entry (fieldCatalog.lookupField) the node names.
//
// Semantics (ExpressionHelp.jsx prints them word for word):
// - A comparison with a missing value is false. `is empty` is true for
//   missing, "", [] and {}.
// - = and != on text are exact; contains and startswith ignore case.
// - A pass/fail eval compared with a verdict word compares its verdict.
// - split and tags are lists.
// - An unprefixed run field = any candidate when 2+ runs are in view, else
//   the only run. any. = at least one run; all. = every run; baseline. =
//   the baseline.
import { lookupField, resolveRunRef } from "./fieldCatalog";
import { parseExpression } from "./expressionParser";
import {
  FUNCS,
  WORD,
  arith,
  asNumber,
  asText,
  containsValue,
  equal,
  isEmptyValue,
  isMissing,
  isWord,
  order,
  word,
} from "./expressionValues";

export { isEmptyValue, isMissing } from "./expressionValues";

// ---------------------------------------------------------------- binding
const RESOLVED = new WeakMap();

/** The catalogue entry and run ref a field node names, cached per catalogue. */
export function resolveNode(node, catalog) {
  let byCat = RESOLVED.get(catalog);
  if (!byCat) {
    byCat = new WeakMap();
    RESOLVED.set(catalog, byCat);
  }
  if (byCat.has(node)) return byCat.get(node);
  const field = lookupField(catalog, node.path, { wantRun: !!node.run });
  const run =
    field && field.scope === "run" ? resolveRunRef(catalog, node.run) : null;
  const res = { field, run };
  byCat.set(node, res);
  return res;
}

/**
 * Verdict words outside a value position name a field when the catalogue
 * has one (`error is not empty`); on the right of =, in, contains and
 * startswith they stay words (`status = error`). Cached per catalogue.
 */
const BOUND = new WeakMap();
const valuesOf = (node, catalog) => {
  if (node?.t !== "field") return null;
  const f = lookupField(catalog, node.path, { wantRun: !!node.run });
  return f?.values ? f.values.map((v) => String(v).toLowerCase()) : null;
};
export const knownValuesOf = valuesOf;
export function bindWords(ast, catalog) {
  if (!ast) return ast;
  let byCat = BOUND.get(catalog);
  if (!byCat) {
    byCat = new WeakMap();
    BOUND.set(catalog, byCat);
  }
  if (byCat.has(ast)) return byCat.get(ast);
  const walk = (node, valuePos) => {
    if (!node || typeof node !== "object") return node;
    if (node.t === "word")
      return !valuePos && lookupField(catalog, [node.v])
        ? {
            t: "field",
            run: null,
            path: [node.v],
            at: node.at,
            length: node.length,
          }
        : node;
    const out = { ...node };
    ["l", "e", "list"].forEach((k) => {
      if (node[k]) out[k] = walk(node[k], false);
    });
    if (node.r)
      out.r = walk(node.r, ["cmp", "contains", "startswith"].includes(node.t));
    if (node.items) out.items = node.items.map((x) => walk(x, node.t === "in"));
    // `movement = regressed`: a bare name that is one of the left field's
    // known values (and names no field) is that value.
    const known = valuesOf(out.l, catalog);
    if (
      known &&
      node.t === "cmp" &&
      out.r?.t === "field" &&
      !out.r.run &&
      out.r.path.length === 1 &&
      !lookupField(catalog, out.r.path)
    ) {
      const v = String(out.r.path[0]).toLowerCase();
      if (known.includes(v))
        out.r = { t: "word", v, at: out.r.at, length: out.r.length };
    }
    if (node.args) out.args = node.args.map((x) => walk(x, false));
    return out;
  };
  const bound = walk(ast, false);
  byCat.set(ast, bound);
  return bound;
}

/** Runs an unprefixed / any / all field ranges over. */
export function quantifierRuns(quant, scope) {
  const ids = scope.runIds ?? [];
  if (quant === "default") {
    const cands = ids.filter((id) => id !== scope.baselineId);
    return ids.length >= 2 ? cands : ids;
  }
  return ids;
}

/** The first quantified run field in a predicate (validated: at most one). */
function findQuantified(node, catalog) {
  if (!node || typeof node !== "object") return null;
  if (node.t === "field") {
    const { field, run } = resolveNode(node, catalog);
    if (field?.scope === "run" && run?.quant) return run.quant;
    return null;
  }
  for (const k of ["l", "r", "e", "list"]) {
    const q = findQuantified(node[k], catalog);
    if (q) return q;
  }
  for (const arr of [node.args, node.items]) {
    if (!arr) continue;
    for (const x of arr) {
      const q = findQuantified(x, catalog);
      if (q) return q;
    }
  }
  return null;
}

// ---------------------------------------------------------------- reads
function readCustom(def, field, runId, scope, env) {
  const stack = env.stack ?? [];
  if (stack.includes(def.name)) return null;
  const inner = {
    ...env,
    stack: [...stack, def.name],
    bound: runId ?? env.bound,
  };
  if (def.kind === "path") {
    const parsed = parseExpression(
      String(def.path ?? "").replace(/\[(\d+)\]/g, ".$1"),
    );
    if (parsed.error || parsed.ast.t !== "field") return null;
    return evalValue(parsed.ast, scope, inner);
  }
  const ast = def.ast ?? parseExpression(def.expr ?? "").ast;
  return ast ? evalValue(bindWords(ast, scope.catalog), scope, inner) : null;
}

function readField(node, scope, env, { verdict = false } = {}) {
  const { field, run } = resolveNode(node, scope.catalog);
  if (!field) return null;
  if (field.custom) {
    const runId = field.scope === "run" ? run?.id ?? env.bound ?? null : null;
    return readCustom(field.custom, field, runId, scope, env);
  }
  let target = field;
  if (verdict && field.type === "eval" && field.eval) {
    const v = lookupField(scope.catalog, [...node.path, "verdict"], {
      wantRun: field.scope === "run",
    });
    if (v) target = v;
  }
  if (field.scope === "row") return scope.readRow(target);
  if (run?.error) return null;
  const runId =
    run?.id ??
    env.bound ??
    (quantifierRuns("default", scope).length === 1
      ? quantifierRuns("default", scope)[0]
      : null);
  if (!runId) return null;
  return scope.readRun(runId, target);
}

function evalValue(node, scope, env) {
  switch (node.t) {
    case "lit":
      return node.v;
    case "word":
      return word(node.v);
    case "field":
      return readField(node, scope, env);
    case "call":
      return FUNCS[node.fn](node.args.map((a) => evalValue(a, scope, env)));
    case "arith":
      return arith(
        node.op,
        evalValue(node.l, scope, env),
        evalValue(node.r, scope, env),
      );
    case "neg": {
      const n = asNumber(evalValue(node.e, scope, env));
      return n === null ? null : -n;
    }
    default:
      return evalBool(node, scope, env);
  }
}

// One side of a comparison against a verdict word reads the eval's verdict.
function side(node, other, scope, env) {
  if (node.t === "field" && other?.t === "word")
    return readField(node, scope, env, { verdict: true });
  return evalValue(node, scope, env);
}

function evalPredicate(node, scope, env) {
  switch (node.t) {
    case "cmp": {
      const a = side(node.l, node.r, scope, env);
      const b = side(node.r, node.l, scope, env);
      if (isMissing(a) || isMissing(b)) return false;
      if (node.op === "=") return equal(a, b);
      if (node.op === "!=") return !equal(a, b);
      return order(node.op, a, b);
    }
    case "in": {
      const a = node.items
        ? side(node.l, node.items[0], scope, env)
        : evalValue(node.l, scope, env);
      if (isEmptyValue(a) && !Array.isArray(a)) return false;
      let hit;
      if (node.items) {
        const vals = node.items.map((x) => evalValue(x, scope, env));
        hit = vals.some((v) => !isMissing(v) && equal(a, v));
      } else {
        const list = evalValue(node.list, scope, env);
        if (isMissing(list)) return false;
        hit = Array.isArray(list)
          ? list.some((v) => !isMissing(v) && equal(v, a))
          : containsValue(list, a);
      }
      return node.neg ? !hit : hit;
    }
    case "contains": {
      const a = evalValue(node.l, scope, env);
      const b = evalValue(node.r, scope, env);
      if (isMissing(a) || isMissing(b)) return false;
      const hit = containsValue(a, b);
      return node.neg ? !hit : hit;
    }
    case "startswith": {
      const a = evalValue(node.l, scope, env);
      const b = evalValue(node.r, scope, env);
      if (isMissing(a) || isMissing(b) || typeof a === "object") return false;
      const hit = asText(a)
        .toLowerCase()
        .startsWith(isWord(b) ? b[WORD] : asText(b).toLowerCase());
      return node.neg ? !hit : hit;
    }
    case "empty": {
      const v = evalValue(node.l, scope, env);
      return node.neg ? !isEmptyValue(v) : isEmptyValue(v);
    }
    default: {
      return truthy(evalValue(node, scope, env));
    }
  }
}

const truthy = (v) =>
  isWord(v)
    ? false
    : typeof v === "number"
      ? v !== 0 && Number.isFinite(v)
      : Boolean(v) && !isEmptyValue(v);

function evalBool(node, scope, env) {
  switch (node.t) {
    case "or":
      return node.items.some((x) => evalBool(x, scope, env));
    case "and":
      return node.items.every((x) => evalBool(x, scope, env));
    case "not":
      return !evalBool(node.e, scope, env);
    default: {
      if (env.bound) return evalPredicate(node, scope, env);
      const quant = findQuantified(node, scope.catalog);
      if (!quant) return evalPredicate(node, scope, env);
      const ids = quantifierRuns(quant, scope);
      if (!ids.length) return false;
      const test = (id) => evalPredicate(node, scope, { ...env, bound: id });
      return quant === "all" ? ids.every(test) : ids.some(test);
    }
  }
}

/** The value of an expression (a boolean for conditions). */
export function evaluateExpression(ast, scope, env = {}) {
  if (!ast) return null;
  const node = bindWords(ast, scope.catalog);
  if (["lit", "word", "field", "call", "arith", "neg"].includes(node.t)) {
    const v = evalValue(node, scope, env);
    return isWord(v) ? v[WORD] : v;
  }
  return evalBool(node, scope, env);
}

/** True when the row matches. */
export const matchesExpression = (ast, scope) =>
  !!ast && evalBool(bindWords(ast, scope.catalog), scope, {}) === true;
