import {
  resolveEvalKind,
  EVAL_KIND,
  choiceTone,
} from "src/sections/projects/LLMTracing/evalCellModel";
import { EVAL_TASK_ROW_TYPE } from "src/sections/projects/LLMTracing/evalTaskGrouping";

// Shared name-column width so task rows, verdict rows, and the column header line up.
export const NAME_W = "42%";

// `col` shim so the evalCellModel helpers (adaptEvalCell/buildChips/choiceTone) work outside the grid.
export const colFromTemplate = (tpl, sampleRow) => ({
  id: tpl.id,
  name: tpl.name,
  outputType: tpl.outputType,
  choicesMap: sampleRow?.choices_map || sampleRow?.choicesMap || {},
});

// One non-rolled result → one chip. Used in span scope + the breakdown rows.
export const singleResultChip = (e) => {
  if (e.status === "errored" || e.error)
    return { label: "Errored", tone: "errored" };
  const kind = resolveEvalKind({ outputType: e.output_type });
  if (kind === EVAL_KIND.CHOICE) {
    const label = String(e.result ?? "—");
    return { label, tone: choiceTone(label, { choicesMap: e.choices_map }) };
  }
  if (kind === EVAL_KIND.PASS_FAIL)
    return e.result
      ? { label: "Pass", tone: "pass" }
      : { label: "Fail", tone: "fail" };
  return { label: e.score != null ? `${e.score}%` : "—", tone: "plain" };
};

// Flat eval list → [{ id, name, rowType, templates: [{ id, name, outputType, rows }] }].
export const groupByTaskTemplate = (evals) => {
  const tasks = new Map();
  for (const e of evals) {
    const taskId = e.eval_task_id || e.eval_task_name || "__ungrouped__";
    if (!tasks.has(taskId))
      tasks.set(taskId, {
        id: taskId,
        name: e.eval_task_name || "Ungrouped",
        // One task = one level, so the task's glyph comes from its rows' row_type.
        rowType:
          e.row_type === EVAL_TASK_ROW_TYPE.TRACES
            ? EVAL_TASK_ROW_TYPE.TRACES
            : EVAL_TASK_ROW_TYPE.SPANS,
        templates: new Map(),
      });
    const tmap = tasks.get(taskId).templates;
    const tplId = e.eval_template_id || e.eval_config_id;
    if (!tmap.has(tplId))
      tmap.set(tplId, {
        id: tplId,
        name: e.eval_template_name || e.eval_name,
        outputType: e.output_type,
        rows: [],
      });
    tmap.get(tplId).rows.push(e);
  }
  return [...tasks.values()].map((t) => ({
    ...t,
    templates: [...t.templates.values()],
  }));
};

export const isPassed = (e) => {
  const kind = resolveEvalKind({ outputType: e.output_type });
  if (e.status === "errored" || e.error) return false;
  if (kind === EVAL_KIND.PASS_FAIL) return !!e.result;
  if (kind === EVAL_KIND.NUMERIC) return e.score != null && e.score >= 50;
  return true; // choices have nothing to "fix"
};

// Whether a row has anything to show in the expansion (explanation or error localization).
export const hasDetail = (e) => {
  const observationSpanId =
    e.observation_span_id || e.observationSpanId || e.spanId;
  const customEvalConfigId =
    e.custom_eval_config_id || e.eval_config_id || e.evalConfigId;
  return !!(
    e.explanation ||
    e.eval_explanation ||
    (observationSpanId && customEvalConfigId)
  );
};
