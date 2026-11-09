// W1-5 (DS-04): what an eval read on a row, from the cell's `inputs_used`
// ({<input>: {column, value}}, set by the one scorer): one line per input,
// "Read expected ← intent: 'refund_request'". The reference comes first and
// the run's output last.

const QUOTE_CHARS = 80;
const quote = (v) => {
  if (v == null || v === "") return "(empty)";
  const t = typeof v === "object" ? JSON.stringify(v) : String(v);
  return `'${t.length > QUOTE_CHARS ? `${t.slice(0, QUOTE_CHARS)}…` : t}'`;
};

const REFERENCE_ROLE = /^(?:expected|reference|ground_?truth|answer)/i;
const rankOf = (input) =>
  REFERENCE_ROLE.test(input) ? 0 : input === "output" ? 2 : 1;

/** The lines of a cell's inputs_used, or [] when it has none. */
export function inputsUsedLines(inputsUsed) {
  if (!inputsUsed || typeof inputsUsed !== "object") return [];
  return Object.entries(inputsUsed)
    .sort(([a], [b]) => rankOf(a) - rankOf(b))
    .map(([input, used]) =>
      used?.column == null
        ? `Read ${input} ← no column (not mapped)`
        : // L7c g6: the run's own output is shown above the card; a line
          // without its text never claims the output was empty.
          input === "output" && used.value == null
          ? `Read ${input} ← ${used.column}`
          : `Read ${input} ← ${used.column}: ${quote(used.value)}`,
    );
}
