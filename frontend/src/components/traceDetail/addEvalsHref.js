// options-truth L3 G10 (copy:status-words): the trace drawer's `Add Evals`
// opens the task creator (the same flow the tracing list's `Add Evals`
// uses) prefilled with this span's project and a filter on this span, and
// `returnTo` brings the user back to the trace they came from.
import { buildAddEvalsDraft } from "src/sections/projects/LLMTracing/buildAddEvalsDraft";

/**
 * @param {{projectId?: string|null, span?: {id?: string, trace?: string}|null,
 *   returnTo?: string|null}} a
 * @returns {string|null} `/dashboard/tasks/create?project=…&draft=…&returnTo=…`,
 *   or null when the span has no project (nothing to attach evals to)
 */
export function addEvalsHrefFor({ projectId, span, returnTo } = {}) {
  if (!projectId || !span?.id) return null;
  return buildAddEvalsDraft({
    observeId: projectId,
    rowType: "spans",
    mainFilters: [
      {
        column_id: "span_id",
        filter_config: {
          col_type: "SYSTEM_METRIC",
          filter_type: "text",
          filter_op: "equals",
          filter_value: span.id,
        },
      },
    ],
    returnTo: returnTo ?? null,
  });
}
