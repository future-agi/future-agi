// W5-1 · ONE expression language (W5-SCOPE §5 A0): the public API. Grid,
// results, custom columns, run metrics, run filters and find all call these;
// nothing else parses or evaluates an expression. Pure.
//
//   parseExpression(text)            → {ast} | {error: {message, at, length}}
//   validateExpression(ast, catalog) → {ok: true, refs} | {error}
//   evaluateExpression(ast, scope)   → value
//   matchesExpression(ast, scope)    → boolean
//   printExpression(ast)             → canonical text
//   compileExpression(text, catalog) → {ast, refs} | {error}  (parse + validate)
import { EXPRESSION_LIMITS, TOO_DEEP, TOO_LONG } from "./expressionLexer";
import { FUNCTIONS, parseExpression } from "./expressionParser";
import {
  bindWords,
  evaluateExpression,
  knownValuesOf,
  matchesExpression,
  quantifierRuns,
  resolveNode,
} from "./expressionEval";
import { printExpression } from "./expressionPrint";
import { catalogNames, lookupField } from "./fieldCatalog";
import { didYouMean, orList } from "./didYouMean";
import { VERDICT_WORDS } from "./expressionLexer";

export {
  EXPRESSION_LIMITS,
  TOO_DEEP,
  TOO_LONG,
  parseExpression,
  evaluateExpression,
  matchesExpression,
  printExpression,
  quantifierRuns,
};

export const ONE_QUANTIFIER =
  "Use one of any / all per comparison (or name the runs).";

class ValidationError extends Error {
  constructor(message, node, at, length) {
    super(message);
    this.at = at ?? node?.at ?? 0;
    this.length = Math.max(1, length ?? node?.length ?? 1);
  }
}

const fieldText = (node) => node.path.join(".");

function unknownField(node, catalog, { valuePos = false } = {}) {
  const name = fieldText(node);
  const hints = didYouMean(name, catalogNames(catalog));
  const quoteHint =
    valuePos && !node.run && node.path.length === 1
      ? ` Text goes in quotes: "${name}".`
      : "";
  return new ValidationError(
    hints.length
      ? `Unknown field “${name}”. Did you mean ${orList(hints)}?`
      : `Unknown field “${name}”.${quoteHint}`,
    node,
  );
}

const isQuantified = (res, catalog) =>
  res.field?.scope === "run" &&
  (res.run?.quant === "any" ||
    res.run?.quant === "all" ||
    (res.run?.quant === "default" && (catalog.runs?.length ?? 0) >= 2));

const ORDER_OPS = ["<", "<=", ">", ">="];

/**
 * Check every name, run and operator against the catalogue.
 * @returns {{ok: true, refs: Array<{name, scope, run: string|null, quant: string|null}>}
 *   | {error: {message, at, length}}}
 */
export function validateExpression(ast, catalog) {
  if (!ast)
    return { error: { message: "Type an expression.", at: 0, length: 1 } };
  const node0 = bindWords(ast, catalog);
  const refs = [];
  const visitCustom = new Set();

  const checkCustom = (def, node, stack) => {
    if (stack.includes(def.name))
      throw new ValidationError(
        `Custom column “${def.name}” refers to itself.`,
        node,
      );
    if (def.kind !== "expression" || visitCustom.has(def.name)) return;
    const parsed = parseExpression(def.expr ?? "");
    if (parsed.error)
      throw new ValidationError(
        `Custom column “${def.name}”: ${parsed.error.message}`,
        node,
      );
    walk(bindWords(parsed.ast, catalog), [...stack, def.name], () => {}, true);
    visitCustom.add(def.name);
  };

  // `count(res)` tallies quantified run fields of the current predicate.
  function walk(node, stack, count, inCustom = false) {
    if (!node || typeof node !== "object") return;
    switch (node.t) {
      case "field": {
        const res = resolveNode(node, catalog);
        if (!res.field) throw unknownField(node, catalog);
        if (node.run && res.field.scope === "row")
          throw new ValidationError(
            `“${res.field.name}” is a row field; remove the run before it.`,
            node,
          );
        if (res.run?.error)
          throw new ValidationError(
            res.run.error,
            node,
            node.runAt ?? node.at,
            node.runLength ?? node.length,
          );
        if (res.field.custom) checkCustom(res.field.custom, node, stack);
        if (isQuantified(res, catalog)) count(node);
        if (!inCustom)
          refs.push({
            name: res.field.name,
            scope: res.field.scope,
            run: res.run?.id ?? null,
            quant: res.run?.quant ?? null,
          });
        return;
      }
      case "call": {
        const spec = FUNCTIONS[node.fn];
        const n = node.args.length;
        if (n < spec.min || n > spec.max) {
          const want =
            spec.max === Infinity
              ? `at least ${spec.min} value${spec.min > 1 ? "s" : ""}`
              : spec.min === spec.max
                ? `${spec.min} value${spec.min > 1 ? "s" : ""}`
                : `${spec.min} or ${spec.max} values`;
          throw new ValidationError(`${node.fn}() takes ${want}.`, node);
        }
        node.args.forEach((a) => walk(a, stack, count, inCustom));
        return;
      }
      case "cmp":
        if (
          node.r.t === "field" &&
          !node.r.run &&
          node.r.path.length === 1 &&
          !resolveNode(node.r, catalog).field
        )
          throw unknownField(node.r, catalog, { valuePos: true });
        if (ORDER_OPS.includes(node.op)) checkOrdering(node);
        break;
      case "in": {
        const known = knownValuesOf(node.l, catalog) ?? [];
        (node.items ?? []).forEach((x) => {
          if (
            x.t === "word" &&
            !VERDICT_WORDS.includes(x.v) &&
            !known.includes(x.v)
          )
            throw new ValidationError(`Text goes in quotes: "${x.v}".`, x);
        });
        break;
      }
      default:
        break;
    }
    ["l", "r", "e", "list"].forEach((k) =>
      walk(node[k], stack, count, inCustom),
    );
    (node.items ?? []).forEach((x) => walk(x, stack, count, inCustom));
  }

  function checkOrdering(node) {
    const pair = [
      [node.l, node.r],
      [node.r, node.l],
    ];
    for (const [a, b] of pair) {
      if (b.t !== "word") continue;
      const res = a.t === "field" ? resolveNode(a, catalog) : null;
      const name =
        res?.field?.eval?.name ?? (a.t === "field" ? fieldText(a) : null);
      if (res?.field?.type === "eval" || res?.field?.eval)
        throw new ValidationError(
          `“${node.op}” needs a number; ${name} is pass / fail. Try ${name} = ${b.v}.`,
          node,
          node.opAt,
          node.opLength,
        );
      throw new ValidationError(
        `“${node.op}” needs a number; “${b.v}” is a word.`,
        node,
        node.opAt,
        node.opLength,
      );
    }
    for (const [a, b] of pair) {
      if (a.t !== "field" || b.t !== "lit" || typeof b.v !== "number") continue;
      const res = resolveNode(a, catalog);
      if (res.field?.type === "text" && !res.field.eval)
        throw new ValidationError(
          `“${node.op}” needs a number; ${res.field.name} is text.`,
          node,
          node.opAt,
          node.opLength,
        );
    }
  }

  // Predicates: every node that is not and / or / not.
  function predicates(node) {
    if (node.t === "or" || node.t === "and")
      return node.items.forEach(predicates);
    if (node.t === "not") return predicates(node.e);
    let seen = 0;
    walk(node, [], (fieldNode) => {
      seen += 1;
      if (seen > 1) throw new ValidationError(ONE_QUANTIFIER, fieldNode);
    });
    return undefined;
  }

  try {
    predicates(node0);
    return { ok: true, refs };
  } catch (e) {
    if (e instanceof ValidationError)
      return { error: { message: e.message, at: e.at, length: e.length } };
    throw e;
  }
}

/** Parse + validate in one call. */
export function compileExpression(text, catalog) {
  const parsed = parseExpression(text);
  if (parsed.error) return parsed;
  const valid = validateExpression(parsed.ast, catalog);
  if (valid.error) return valid;
  return { ast: parsed.ast, refs: valid.refs };
}

/** The field a name resolves to in a catalogue (autocomplete, help). */
export const fieldOf = (catalog, name, opts) =>
  lookupField(catalog, String(name).split("."), opts);
