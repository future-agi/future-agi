export const GRAPH_QUERY_UNAVAILABLE_MESSAGE =
  "Graph data is temporarily unavailable. Narrow the time range or filters and try again.";
export const GRAPH_QUERY_ADJUSTED_MESSAGE =
  "Complete UTC hours are shown; partial boundary hours are excluded.";

function readableUtcBoundary(value) {
  const match = value.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/);
  return match ? `${match[1]} ${match[2]}` : value;
}

export function isGraphQueryDegraded(result) {
  return (
    result?.query_complete === false || result?.query_status === "degraded"
  );
}

export function isGraphQueryAdjusted(result) {
  return (
    result?.query_status === "adjusted" &&
    result?.query_window_adjusted === true
  );
}

export function getGraphQueryAdjustedMessage(result) {
  if (!isGraphQueryAdjusted(result)) return "";
  const start = result?.query_window_start;
  const end = result?.query_window_end;
  if (typeof start !== "string" || typeof end !== "string") {
    return GRAPH_QUERY_ADJUSTED_MESSAGE;
  }
  return (
    `Complete UTC hours shown: ${readableUtcBoundary(start)} to ` +
    `${readableUtcBoundary(end)} (end exclusive); partial boundary hours excluded.`
  );
}
