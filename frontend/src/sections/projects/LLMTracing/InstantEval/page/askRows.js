import { answerState } from "./viz";

// "Did the agent resolve it?" → the rows that got `answer` for it.
export function rowsWithAnswer(records, configId, answer) {
  return records.filter(
    (record) => answerState(record, configId).answer === answer,
  );
}

/**
 * The rows the table shows: a selection from a chart wins, then the answer
 * in focus on the legend, then every row.
 */
export function visibleRows(records, { selection, focus }) {
  if (selection?.ids?.length) {
    const ids = new Set(selection.ids);
    return records.filter((record) => ids.has(record.id));
  }
  if (focus?.configId && focus.answer)
    return rowsWithAnswer(records, focus.configId, focus.answer);
  return records;
}
