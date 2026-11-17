// W7 A0: the typed evaluator types the Evals module creates on their own tab
// (a decision tree, a remote scorer) and the judge calls a typed eval makes
// a row. Pure; the mock world (evalTypes/index.js) and the browser share it
// (the estimate's "up to N judge calls a row", integration S13).
import { treeJudgeOf } from "./dagModel";

export const TYPED_EVAL_TYPES = [
  { value: "dag", label: "Decision tree" },
  { value: "remote", label: "Remote scorer" },
];

const TYPED = new Set(TYPED_EVAL_TYPES.map((t) => t.value));

/** Whether an eval type is one of the typed types (dag, remote). */
export const isTypedEvalType = (type) => TYPED.has(type);

/** The name of a typed type, or null for any other type. */
export const typedEvalLabel = (type) =>
  TYPED_EVAL_TYPES.find((t) => t.value === type)?.label ?? null;

/** The type of a stored template: its custom template's, else its own. */
export const evalTypeOf = (metric) =>
  metric?.custom_template?.eval_type ??
  metric?.eval_type ??
  (metric?.composite || metric?.template_type === "composite"
    ? "composite"
    : null);

const dagOf = (evalDef, metric) =>
  evalDef?.config?.dag ??
  metric?.custom_template?.config?.dag ??
  metric?.config?.dag ??
  metric?.dag ??
  null;

/**
 * The judge calls one node of a decision tree makes, by judge model (a
 * choice asks once per option): a question its own judge, else the tree's
 * (dagModel.treeJudgeOf); an eval node its template's own (null here).
 */
const nodeModels = (node, tree) => {
  if (node?.kind === "judge")
    return Array(
      Array.isArray(node.options) && node.options.length
        ? node.options.length
        : 1,
    ).fill(node.judge_model || tree || null);
  if (node?.kind === "eval") return [null];
  return [];
};

/**
 * The fewest and the most judge calls on any path from the root to a score,
 * and the judges of the most (`models`).
 */
function dagCalls(dag, metric) {
  const tree = treeJudgeOf(dag, metric);
  const byId = new Map((dag?.nodes ?? []).map((n) => [n.id, n]));
  const memo = new Map();
  const walk = (id, seen) => {
    const node = byId.get(id);
    if (!node || seen.has(id)) return null;
    if (memo.has(id)) return memo.get(id);
    if (node.kind === "leaf") return { min: 0, max: 0, models: [] };
    const nexts = [...(node.answers ?? []), ...(node.options ?? [])]
      .map((a) => a?.next)
      .filter(Boolean);
    const sub = nexts
      .map((n) => walk(n, new Set([...seen, id])))
      .filter(Boolean);
    if (!sub.length) return null;
    const own = nodeModels(node, tree);
    const most = sub.reduce((a, s) => (s.max > a.max ? s : a));
    const res = {
      min: own.length + Math.min(...sub.map((s) => s.min)),
      max: own.length + most.max,
      models: [...own, ...most.models],
    };
    memo.set(id, res);
    return res;
  };
  return walk(dag?.root, new Set());
}

/** The children of a composite (the store's, or the template detail's). */
const compositeChildrenOf = (evalDef, metric) =>
  evalDef?.composite?.children ??
  metric?.composite?.children ??
  (metric?.template_type === "composite" ? metric?.children : null) ??
  null;

/**
 * Judge calls a typed eval makes on one row: {min, max} (and `per_step`,
 * the children judged once per step of the try), or null for an untyped
 * eval (one call, the estimate's default).
 */
export function typedJudgeCallsOf(evalDef = {}, metric = null) {
  const type = evalTypeOf(metric) ?? evalTypeOf(evalDef);
  if (type === "dag") {
    const calls = dagCalls(dagOf(evalDef, metric), metric);
    return calls ? { min: calls.min, max: calls.max } : { min: 0, max: 0 };
  }
  if (type === "remote") return { min: 0, max: 0 };
  const kids = compositeChildrenOf(evalDef, metric);
  if (type === "composite" || kids) {
    const judged = (kids ?? []).filter(
      (c) => (c.kind ?? c.child_kind ?? "eval") !== "check",
    );
    const perStep = judged.filter((c) => (c.level ?? "row") === "step").length;
    const rowCalls = judged.length - perStep;
    return {
      min: rowCalls,
      max: rowCalls,
      ...(perStep ? { per_step: perStep } : {}),
    };
  }
  return null;
}

/**
 * Each judge call a typed eval makes a row, by its judge model (null: the
 * eval's own judge): a composite's judged children (a jury seat with its own
 * model; a step-level child counted once, its try's steps are not known
 * before the run), a decision tree's longest path (each question at its
 * judge), none for a remote scorer. Null for an untyped eval.
 */
export function typedJudgeModelsOf(evalDef = {}, metric = null) {
  const calls = typedJudgeCallsOf(evalDef, metric);
  if (!calls) return null;
  const kids = compositeChildrenOf(evalDef, metric);
  if (kids)
    return kids
      .filter((c) => (c.kind ?? c.child_kind ?? "eval") !== "check")
      .map((c) => c.judge_model ?? null);
  if (evalTypeOf(metric) === "dag" || evalTypeOf(evalDef) === "dag")
    return dagCalls(dagOf(evalDef, metric), metric)?.models ?? [];
  return Array(calls.max).fill(null);
}

/**
 * The judge calls one picked eval adds to each row of the run estimate
 * (integration S13), by judge model; null for an untyped eval (the estimate's one
 * call). Reads what the wizard entry carries from the list item (S11c,
 * S13b `judgeModels`) or the template detail (S11b `judge_models`), else
 * the entry's own definition.
 */
export function estimateJudgeModelsOf(entry) {
  const models =
    entry?.judgeModels ??
    entry?.templateDetails?.judge_models ??
    entry?.evalTemplate?.judge_models ??
    entry?.judge_models ??
    typedJudgeModelsOf(entry ?? {}, entry ?? null);
  return Array.isArray(models) ? models : null;
}

/**
 * The one judge a typed eval's own questions ask, from its template detail's
 * `judge_models` (S11b; null entries are eval nodes asking their own
 * template's judge), else null: a tree of checks asks none, and questions
 * at several judges name no single one. The wizard card's judge (S10c).
 */
export function typedOwnJudgeOf(detail) {
  const models = [...new Set((detail?.judge_models ?? []).filter(Boolean))];
  return models.length === 1 ? models[0] : null;
}
