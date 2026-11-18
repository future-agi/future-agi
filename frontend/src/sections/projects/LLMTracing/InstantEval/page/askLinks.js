import { formatDate } from "src/utils/report-utils";
import { OBSERVE_LINK_FILTER_PARAM } from "src/sections/projects/LLMTracing/common";
import { UNIT_TAB } from "src/sections/projects/observeHandoff";
import { buildAnswerFilter } from "../useInstantEval";

// Where the Ask page leads: back to the view its rows came from, into the
// Traces list filtered to some of them, or to one row.

export const ASK_RUN_PARAM = "run";
export const ASK_UNIT_PARAM = "unit";
export const OBSERVE_TAB_PARAM = "tab";

const DAY_MS = 24 * 3600 * 1000;
// Ids per grid link: enough for a selection, short enough for a URL.
export const GRID_LINK_MAX = 200;

export const tabForUnit = (unit) => UNIT_TAB[unit] || UNIT_TAB.traces;

/** The view a unit's rows came from, with the tab bar on it. */
export function sourceViewLink(projectId, unit) {
  const base = `/dashboard/observe/${projectId}`;
  if (unit === "sessions") return `${base}/sessions?tab=sessions`;
  if (unit === "users") return `${base}/users?tab=users`;
  if (unit === "spans")
    return `${base}/llm-tracing?tab=spans&selectedTab=spans`;
  return `${base}/llm-tracing?tab=traces&selectedTab=trace`;
}

export const sourceViewTitle = (unit) =>
  ({ sessions: "Sessions", users: "Users", spans: "Spans" })[unit] || "Traces";

function dateParam(times) {
  const valid = times
    .filter(Boolean)
    .map((time) => new Date(time).getTime())
    .filter(Number.isFinite);
  if (!valid.length) return null;
  // The list's default window may not reach back to these rows.
  const end = Math.min(Date.now(), Math.max(...valid) + DAY_MS);
  return JSON.stringify({
    dateFilter: [
      formatDate(new Date(Math.min(...valid) - DAY_MS)),
      formatDate(new Date(end)),
    ],
    dateOption: "Custom",
  });
}

/**
 * The Traces list filtered to some rows of a run: traces by id, the traces
 * of sessions, or the traces the spans belong to. Users have no list filter
 * by id, and more rows than one link names are never cut short, so null.
 */
export function rowsGridLink(projectId, unit, records = []) {
  if (unit === "users" || !records.length || records.length > GRID_LINK_MAX)
    return null;
  const column = unit === "sessions" ? "session_id" : "trace_id";
  const ids = [
    ...new Set(
      records
        .map((record) => (unit === "spans" ? record.trace_id : record.id))
        .filter(Boolean),
    ),
  ];
  if (!ids.length) return null;
  const params = new URLSearchParams({ tab: "traces", selectedTab: "trace" });
  const dates = dateParam(records.map((record) => record.time));
  if (dates) params.set("primaryTraceDateFilter", dates);
  params.set(
    OBSERVE_LINK_FILTER_PARAM,
    JSON.stringify([
      {
        column_id: column,
        filter_config: {
          filter_type: "text",
          filter_op: "in",
          filter_value: ids,
        },
      },
    ]),
  );
  return `/dashboard/observe/${projectId}/llm-tracing?${params.toString()}`;
}

/**
 * A trace run's answers as a filter chip on the Traces list (each question
 * is an eval column there): the traces that got `answer`, in the run's
 * window.
 */
export function answerGridLink({ projectId, question, answer, window }) {
  const params = new URLSearchParams({ tab: "traces", selectedTab: "trace" });
  if (window?.length === 2)
    params.set(
      "primaryTraceDateFilter",
      JSON.stringify({
        dateFilter: window.map((value) => formatDate(new Date(value))),
        dateOption: "Custom",
      }),
    );
  params.set(
    OBSERVE_LINK_FILTER_PARAM,
    JSON.stringify([buildAnswerFilter(question, [answer])]),
  );
  return `/dashboard/observe/${projectId}/llm-tracing?${params.toString()}`;
}

// A user's page, keyed by the external user id, its Sessions list on the
// run's days (`window`) widened to the row's `time`: the page's own default
// is Today. Insights opens a user the same way.
export { userPageLink } from "src/sections/projects/Insights/insightsLinks";
