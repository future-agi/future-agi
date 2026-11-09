// W6-1 (A2, `try:r2-try-on-one-row`): the Try's stop line from the try
// itself: a step error, else the try's own error ("Stopped at 600 tokens",
// "Answer cut at 101 tokens", "No recording for lookup_order on this
// row"), else the final answer. Pure.

/** The line under the Try's output, or null before a try has a trace. */
export function tryStopLine(attempt) {
  if (!attempt) return null;
  const s = attempt.trace_summary ?? {};
  if (s.status === "error")
    return `Stopped at step ${s.error?.step ?? "?"}: ${s.error?.text ?? "error"}`;
  if (attempt.error) return attempt.error;
  return attempt.trace_id ? "Stop reason: final answer" : null;
}
