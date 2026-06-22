const TRACE_GLYPH = { code: "T", label: "Trace-level eval — one result per trace" };
const SPAN_GLYPH = {
  code: "S",
  label: "Span-level eval — rolled up across this trace's spans",
};

const GLYPH_BY_ROW_TYPE = {
  traces: TRACE_GLYPH,
  trace: TRACE_GLYPH,
  spans: SPAN_GLYPH,
  span: SPAN_GLYPH,
};

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
        rowType: col.rowType || col.targetType || null,
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
