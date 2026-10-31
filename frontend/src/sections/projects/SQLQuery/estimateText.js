// Typing pauses this long before an estimate is requested; each newer
// keystroke cancels the pending request.
export const ESTIMATE_DEBOUNCE_MS = 800;
const UNAVAILABLE = "Estimate unavailable. Run is not affected.";
const count = new Intl.NumberFormat("en-US");

function plural(value, word) {
  return `${count.format(value)} ${word}${value === 1 ? "" : "s"}`;
}

// One compact advisory line; never a reason to disable Run.
export function describeEstimate(state) {
  if (state.status === "loading") {
    return { tone: "info", text: "Estimating scan…" };
  }
  if (state.status === "error") {
    const code = state.error?.response?.status;
    const detail = state.error?.response?.data?.detail;
    if (code === 429) {
      return {
        tone: "info",
        text: "Estimate paused: too many estimates. Run is not affected.",
      };
    }
    if (code === 400 && typeof detail === "string") {
      return {
        tone: "warning",
        text: `This query will be rejected: ${detail}`,
      };
    }
    return { tone: "info", text: UNAVAILABLE };
  }
  const value = state.value;
  if (!value || value.status !== "ok") {
    return { tone: "info", text: value?.reason || UNAVAILABLE };
  }
  const parts = (value.sources || []).map((source) =>
    source.estimated
      ? `${source.dataset} ≤ ${plural(source.max_rows, "row")} (${plural(
          source.marks,
          "index granule",
        )})`
      : `${source.dataset} not estimated (shared index)`,
  );
  const limit = count.format(value.max_rows_to_read);
  if (value.exceeds_scan_bound) {
    return {
      tone: "warning",
      text: `Estimate: likely over the ${limit}-row scan limit. Narrow the time window or filter the query. ${parts.join(" · ")}`,
    };
  }
  const bound =
    value.exceeds_scan_bound === false
      ? `within the ${limit}-row scan limit`
      : "scan limit not checked";
  return {
    tone: "info",
    text: `Estimate: ${[...parts, bound].join(" · ")}`,
  };
}
