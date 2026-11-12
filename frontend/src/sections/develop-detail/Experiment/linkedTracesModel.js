// Traces I already have (R2-SPEC §5.3.9): the IM1 `match_rows` request,
// the live match line and the card's stepper issues. Pure.

export const ROOT_OUTPUT_PATH = "$.root.output";
export const MATCH_BY_OPTIONS = [
  { label: "Metadata key", value: "metadata" },
  { label: "Input equals column", value: "input" },
  { label: "Session id equals column", value: "session" },
];
export const WINDOW_OPTIONS = [
  { label: "Last 24 hours", value: "24h" },
  { label: "Last 7 days", value: "7d" },
  { label: "Last 30 days", value: "30d" },
];
export const DEFAULT_WINDOW = "7d";
export const FILTER_OPS = ["=", "≠", "contains"];

/** Filters with a key and an operator (a value may be empty). */
export const validFilters = (filters) =>
  (Array.isArray(filters) ? filters : []).filter(
    (f) => f?.key && FILTER_OPS.includes(f.op),
  );

/** `env = prod` (a filter chip). */
export const filterLabel = (f) => `${f.key} ${f.op} ${f.value ?? ""}`.trim();

const MAX_LISTED = 5;
const fmt = (n) => Number(n || 0).toLocaleString("en-US");

/**
 * IM1 `op:"preview"` body with `shape:"match_rows"`, or null while the card
 * cannot match yet (no project, or an experiment project without version).
 * @param {{datasetId: string, traces: object, project?: {id, type}}} p
 */
export function matchRequest({ datasetId, traces, project }) {
  if (!datasetId || !traces?.project_id || !project) return null;
  if (project.type === "experiment" && !traces.project_version_id) return null;
  const by = traces.match?.by ?? "metadata";
  const match =
    by === "metadata"
      ? { by, key: traces.match?.key || "row_id" }
      : { by, column: traces.match?.column ?? null };
  if (by !== "metadata" && !match.column) return null;
  return {
    source: {
      kind: "traces",
      project_id: traces.project_id,
      ...(project.type === "experiment"
        ? { project_version: traces.project_version_id }
        : {
            filters: {
              window: traces.window ?? DEFAULT_WINDOW,
              ...(validFilters(traces.filters).length
                ? { conditions: validFilters(traces.filters) }
                : {}),
            },
          }),
    },
    shape: "match_rows",
    target: { dataset_id: datasetId },
    match,
    max_trials: Number(traces.max_trials) || 1,
  };
}

/**
 * `28 of 30 rows matched · 2 not matched (T13, T19) · 0 rows with more
 * traces than trials`, split so the unmatched labels can be links.
 * @param {object|null} summary  IM1 match_rows `summary`
 */
export function matchLine(summary) {
  if (!summary) return null;
  const matched = Number(summary.matched) || 0;
  const ids = summary.unmatched_row_ids ?? [];
  const labels = summary.unmatched_row_labels ?? [];
  const total = matched + ids.length;
  const unmatched = ids
    .slice(0, MAX_LISTED)
    .map((id, i) => ({ id, label: labels[i] ?? `Row ${i + 1}` }));
  const extra = Number(summary.rows_with_more_than_trials) || 0;
  return {
    matched,
    head: `${fmt(matched)} of ${fmt(total)} rows matched`,
    unmatchedHead: `${fmt(ids.length)} not matched`,
    unmatched,
    more: Math.max(0, ids.length - MAX_LISTED),
    tail: `${fmt(extra)} ${extra === 1 ? "row" : "rows"} with more traces than trials`,
  };
}

/** The plain-text match line (tests, tooltips). */
export function matchLineText(summary) {
  const l = matchLine(summary);
  if (!l) return "";
  const list = l.unmatched.length
    ? ` (${l.unmatched.map((u) => u.label).join(", ")}${l.more ? `, +${l.more}` : ""})`
    : "";
  return l.unmatched.length
    ? `${l.head} · ${l.unmatchedHead}${list} · ${l.tail}`
    : `${l.head} · ${l.tail}`;
}

/** Stepper issues of a Traces card. */
export function tracesIssues(item) {
  const t = item?.traces ?? {};
  if (!t.project_id) return ["Pick the project your script traced to"];
  if (item?._matchedRows === 0) return ["No rows matched yet"];
  return [];
}
