// L7c g7 (test:editor, mapping:variable-mapping, evalcreate:save-add): an
// experiment host's virtual "Output" column in the eval editor's mapping.
//
// In an experiment the eval scores each run's own reply: the one scorer
// never reads a dataset column for it. So a variable that reads the reply
// (output, response, ...) is fixed to the virtual Output column (no remap
// to a dataset column the run would not score), and the editor's Test
// stands in the dataset column the host names (`sampleFrom`: the column
// the wizard card's Test judges, e.g. "Draft reply") on the row on screen.

/** Eval variables that read the run's reply (one rule with the wizard). */
export const OUTPUT_VARIABLE =
  /^(?:output|response|actual(?:_output)?|completion|generated)$/i;

const nameOf = (col) =>
  typeof col === "string"
    ? col
    : col?.headerName || col?.field || col?.name || col?.label || "";

/**
 * The host's virtual run-output column (`field: "output"`), or null.
 * @param {Array<object|string>} extraColumns
 * @returns {{name: string, sampleFrom: string|null, sampleName: string|null}|null}
 */
export function virtualOutputOf(extraColumns = []) {
  const col = (extraColumns ?? []).find(
    (c) => c && typeof c === "object" && c.field === "output",
  );
  if (!col) return null;
  return {
    name: nameOf(col),
    sampleFrom: col.sampleFrom ?? null,
    sampleName: col.sampleName ?? null,
  };
}

/** The variables fixed to the run output in this host. */
export const lockedOutputVariables = (variables = [], virtualOutput = null) =>
  virtualOutput ? variables.filter((v) => OUTPUT_VARIABLE.test(v)) : [];

/**
 * The mapping with every locked variable on the virtual Output column;
 * returns `mapping` itself when nothing changes.
 */
export function withLockedOutput(mapping = {}, locked = [], virtualOutput) {
  if (!virtualOutput || !locked.length) return mapping;
  if (locked.every((v) => mapping[v] === virtualOutput.name)) return mapping;
  const next = { ...mapping };
  locked.forEach((v) => {
    next[v] = virtualOutput.name;
  });
  return next;
}

/**
 * What a Test sends for a variable mapped to the virtual Output: the
 * host's sample column on the row on screen; undefined when the host names
 * none (the scorer then says the output is empty, as it is).
 * @param {object|null} virtualOutput
 * @param {object|null} row     the row on screen (cells by column id)
 * @param {(cell: any) => any} unwrap
 */
export function virtualOutputValueOf(virtualOutput, row, unwrap = (x) => x) {
  if (!virtualOutput?.sampleFrom || !row) return undefined;
  const v = unwrap(row[virtualOutput.sampleFrom]);
  if (v == null) return undefined;
  return typeof v === "object" ? JSON.stringify(v) : String(v);
}
