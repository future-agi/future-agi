export const EVAL_METRICS_GROUP = "Evaluation Metrics";

export const EVAL_TASK_ROW_TYPE = {
  TRACES: "traces",
  SPANS: "spans",
};

const GLYPH_BY_ROW_TYPE = {
  [EVAL_TASK_ROW_TYPE.TRACES]: {
    code: "T",
    label: "Trace-level eval — one result per trace",
  },
  [EVAL_TASK_ROW_TYPE.SPANS]: {
    code: "S",
    label: "Span-level eval — rolled up across this trace's spans",
  },
};

export const isEvalMetricColumn = (col) => col?.groupBy === EVAL_METRICS_GROUP;

// Groups newest-first by task creation date; evals without task fields are
// returned in `ungrouped` and render flat as before.
export const groupEvalColumnsByTask = (evalCols = []) => {
  const groups = new Map();
  const ungrouped = [];
  for (const col of evalCols) {
    if (!col?.evalTaskName) {
      ungrouped.push(col);
      continue;
    }
    let group = groups.get(col.evalTaskName);
    if (!group) {
      group = {
        taskName: col.evalTaskName,
        createdAt: col.evalTaskCreatedAt || null,
        rowType: col.rowType || null,
        evals: [],
      };
      groups.set(col.evalTaskName, group);
    }
    group.evals.push(col);
  }
  const sorted = Array.from(groups.values()).sort(
    (a, b) => new Date(b.createdAt || 0) - new Date(a.createdAt || 0),
  );
  return { groups: sorted, ungrouped };
};

export const getGlyphMeta = (rowType) =>
  GLYPH_BY_ROW_TYPE[String(rowType || "").toLowerCase()] || null;
