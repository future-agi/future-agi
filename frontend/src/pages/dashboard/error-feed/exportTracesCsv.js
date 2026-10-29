// Reel text is a plain string or `[{t, hl}]` segments (see RichText in OverviewTab).
const reelText = (text) =>
  Array.isArray(text) ? text.map((seg) => seg.t ?? "").join("") : text ?? "";

const failureEvidence = (reel = []) =>
  reel
    .map((step) => {
      const text = reelText(step.text);
      return step.label ? `${step.label}: ${text}` : text;
    })
    .filter(Boolean)
    .join("\n");

const COLUMNS = [
  ["trace_id", (trace) => trace.id],
  ["timestamp", (trace) => trace.timestamp],
  ["model", (trace) => trace.summary?.model],
  ["score", (trace) => trace.evidence?.score],
  ["latency_ms", (trace) => trace.summary?.latency_ms],
  ["turns", (trace) => trace.summary?.turns],
  ["input_tokens", (trace) => trace.summary?.input_tokens],
  ["output_tokens", (trace) => trace.summary?.output_tokens],
  ["input", (trace) => trace.evidence?.input],
  ["output", (trace) => trace.evidence?.output],
  ["judge_reason", (trace) => trace.evidence?.judge_reason],
  ["failure_evidence", (trace) => failureEvidence(trace.evidence?.fail_reel)],
];

const formatCell = (value) => {
  if (value === null || value === undefined) return "";
  const text = String(value);
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
};

/**
 * Serialize Overview representative traces to CSV that the dataset upload
 * accepts as-is: one header row, the same column count on every row, no BOM,
 * no comment rows. Cells are not formula-escaped, so values survive re-import.
 */
export const tracesToCsv = (traces) =>
  [
    COLUMNS.map(([name]) => name),
    ...traces.map((trace) => COLUMNS.map(([, read]) => read(trace))),
  ]
    .map((cells) => cells.map(formatCell).join(","))
    .join("\r\n");

export const downloadCsv = (csv, filename) => {
  const url = URL.createObjectURL(
    new Blob([csv], { type: "text/csv;charset=utf-8" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
