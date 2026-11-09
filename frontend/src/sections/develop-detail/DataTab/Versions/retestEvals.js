// IT-R2 G5 P1 (S2): the checks a re-test of rows imported from production
// failures needs. Pure.
//
// Every row imported from a failing production session carries
// `production: {outcome, criterion, reason, failing_turn, reply,
// template_id}` (pending door ops: `op.production`; saved rows:
// `row_meta.production` in the table). A re-test scores the same criterion
// (Resolution, a judge that needs no expected answer) and does not
// preselect a default eval that reads a reference column empty on every
// selected row (it would skip all of them).
import { emptyReferenceWarnings } from "./evalCoverage";

const productionOfOp = (op) => op?.production ?? op?.source?.production ?? null;

/**
 * `{count, criterion, template_id}` when every selected row carries the same
 * production failure criterion, else null.
 * @param {{rowIds?: string[], ops?: object[], rows?: object[]}} s
 */
export function productionOfSelection({ rowIds, ops, rows } = {}) {
  const ids = (rowIds ?? []).filter(Boolean);
  if (!ids.length) return null;
  const adds = new Map(
    (ops ?? [])
      .filter((o) => o?.op === "add_row" && o.temp_id)
      .map((o) => [o.temp_id, o]),
  );
  const table = new Map((rows ?? []).map((r) => [r.row_id, r]));
  const list = ids.map(
    (id) =>
      productionOfOp(adds.get(id)) ??
      table.get(id)?.row_meta?.production ??
      table.get(id)?.production ??
      null,
  );
  if (list.some((p) => !p?.criterion)) return null;
  const { criterion, template_id: templateId } = list[0];
  if (!list.every((p) => p.criterion === criterion)) return null;
  return { count: list.length, criterion, template_id: templateId ?? null };
}

/** The card line under a production check. */
export const PRODUCTION_EVAL_HINT =
  "The check that failed in production (judge, no expected answer needed)";

/** The form entry (evalFromDatasetDefault shape) for the production check. */
export function productionEval(production, columns = []) {
  const conv = (columns ?? []).find((c) => c.data_type === "conversation");
  const mapping = {
    output: "output",
    ...(conv ? { conversation: conv.id } : {}),
  };
  return {
    evalId: production.template_id,
    templateId: production.template_id,
    name: production.criterion,
    evalTemplateName: String(production.criterion).toLowerCase(),
    mapping,
    model: "gpt-4o",
    config: { mapping },
    outputType: "pass_fail",
    higherIsBetter: true,
    passThreshold: null,
    version: "v1",
    isDefault: false,
    fromProduction: true,
    hint: PRODUCTION_EVAL_HINT,
  };
}

const templateOf = (e) => e?.templateId ?? e?.template_id ?? e?.evalId ?? null;

/**
 * Evals for a re-test of production failures: the production check first;
 * a DEFAULT eval that reads the reference column is dropped when that
 * column is empty on every selected row. Evals the user added stay.
 * Returns `evals` unchanged when there is nothing to do.
 */
export function retestEvals(
  evals = [],
  { production, columns, referenceColumnId, selected } = {},
) {
  if (!production?.template_id) return evals;
  const warnings = emptyReferenceWarnings({
    evals,
    columns,
    referenceColumnId,
    selected,
  });
  const drop = new Set(
    warnings
      .filter((w) => w.empty === w.total && evals[w.evalIndex]?.isDefault)
      .map((w) => w.evalIndex),
  );
  const kept = evals.filter((_, i) => !drop.has(i));
  const has = kept.some((e) => templateOf(e) === production.template_id);
  if (has && !drop.size) return evals;
  return has ? kept : [productionEval(production, columns), ...kept];
}

/** `Added 6 failed production sessions` / `Added 6 rows`. */
export function doorNote(doorOps = []) {
  const n = doorOps.length;
  const word = (w) => `${n} ${w}${n === 1 ? "" : "s"}`;
  return doorOps.length && doorOps.every((o) => productionOfOp(o))
    ? `Added ${word("failed production session")}`
    : `Added ${word("row")}`;
}
