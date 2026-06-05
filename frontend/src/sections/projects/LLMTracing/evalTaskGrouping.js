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

// Each task is emitted as one block at its first eval's array position so
// user reorder sticks.
export const buildColumnBlocks = (cols = []) => {
  const byTask = new Map();
  for (const col of cols) {
    if (!col?.evalTaskName) continue;
    let group = byTask.get(col.evalTaskName);
    if (!group) {
      group = {
        taskName: col.evalTaskName,
        createdAt: col.evalTaskCreatedAt || null,
        rowType: col.rowType || null,
        evals: [],
      };
      byTask.set(col.evalTaskName, group);
    }
    group.evals.push(col);
  }
  const blocks = [];
  const emitted = new Set();
  for (const col of cols) {
    if (!col) continue;
    if (col.evalTaskName) {
      if (!emitted.has(col.evalTaskName)) {
        emitted.add(col.evalTaskName);
        blocks.push({ type: "task", group: byTask.get(col.evalTaskName) });
      }
    } else {
      blocks.push({ type: "col", col });
    }
  }
  return blocks;
};

export const getGlyphMeta = (rowType) =>
  GLYPH_BY_ROW_TYPE[String(rowType || "").toLowerCase()] || null;
