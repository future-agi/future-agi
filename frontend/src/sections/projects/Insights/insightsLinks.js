import { formatDate } from "src/utils/report-utils";

// Where an analysed unit opens in the rest of the app.
//   trace   -> the trace page
//   session -> the Traces list filtered to that session (the Sessions view
//              opens a session only from a grid click; it has no URL for one)
//   user    -> the cross-project user page, keyed by the external user id,
//              on the report's days (its own default is Today)

// Same name as OBSERVE_LINK_FILTER_PARAM in LLMTracing/common.js, which the
// trace list hydrates its filter chips from.
const LINK_FILTER_PARAM = "observeLinkFilter";
const DAY_MS = 24 * 3600 * 1000;

export function sessionTracesLink(projectId, sessionId, time) {
  const params = new URLSearchParams({ tab: "traces", selectedTab: "trace" });
  const at = time ? new Date(time) : null;
  if (at && !Number.isNaN(at.getTime())) {
    // The list's default window may not reach back to the session.
    const end = Math.min(Date.now(), at.getTime() + 2 * DAY_MS);
    params.set(
      "primaryTraceDateFilter",
      JSON.stringify({
        dateFilter: [
          formatDate(new Date(at.getTime() - DAY_MS)),
          formatDate(new Date(end)),
        ],
        dateOption: "Custom",
      }),
    );
  }
  params.set(
    LINK_FILTER_PARAM,
    JSON.stringify([
      {
        column_id: "session_id",
        filter_config: {
          filter_type: "text",
          filter_op: "equals",
          filter_value: sessionId,
        },
      },
    ]),
  );
  return `/dashboard/observe/${projectId}/llm-tracing?${params.toString()}`;
}

// A window that holds an example: the report's own, else a day either side
// of the example. Null when neither is known.
function exampleWindow(window, time) {
  const at = time ? new Date(time).getTime() : NaN;
  const [from, to] = (window?.length === 2 ? window : []).map((value) =>
    new Date(value).getTime(),
  );
  if (Number.isFinite(from) && Number.isFinite(to) && from <= to)
    return Number.isFinite(at)
      ? [Math.min(from, at), Math.max(to, at)]
      : [from, to];
  if (!Number.isFinite(at)) return null;
  return [at - DAY_MS, Math.max(at, Math.min(Date.now(), at + DAY_MS))];
}

/**
 * The cross-project user page, its Sessions list set to a window that holds
 * the example (`window`: the report's [from, to]; `time`: the example's). The
 * page's own default is Today, which misses an older example.
 */
export function userPageLink(userId, { window, time } = {}) {
  const params = new URLSearchParams({ userTab: "sessions" });
  const range = exampleWindow(window, time);
  if (range)
    params.set(
      "sessionDateFilter",
      JSON.stringify({
        dateFilter: range.map((ms) => formatDate(new Date(ms))),
        dateOption: "Custom",
      }),
    );
  return `/dashboard/users/${encodeURIComponent(userId)}?${params.toString()}`;
}

/**
 * {href, label} to open a unit, or null when it cannot be opened. `window`
 * is the report's, so a user opens on the days it covers.
 */
export function unitLink(projectId, unit, row, { window } = {}) {
  if (!row) return null;
  if (unit === "traces" && row.unit_id)
    return {
      href: `/dashboard/observe/${projectId}/trace/${row.unit_id}`,
      label: "Open trace",
    };
  if (unit === "sessions" && (row.session_id || row.unit_id))
    return {
      href: sessionTracesLink(
        projectId,
        row.session_id || row.unit_id,
        row.time,
      ),
      label: "Open session's traces",
    };
  if (unit === "users" && row.user_id)
    return {
      href: userPageLink(row.user_id, { window, time: row.time }),
      label: "Open user",
    };
  return null;
}

// Ids per grid link: enough for a category, short enough for a URL.
export const GRID_LINK_MAX = 200;

/**
 * The Traces list filtered to a set of analysed units: traces by id, or the
 * traces of the given sessions. Users have no list filter by id, so null.
 *   rows: [{unit_id, session_id?, time?}]
 */
export function unitsGridLink(projectId, unit, rows) {
  const items = (rows || []).slice(0, GRID_LINK_MAX);
  if (!items.length || unit === "users") return null;
  const column = unit === "sessions" ? "session_id" : "trace_id";
  const ids = [
    ...new Set(
      items
        .map((row) =>
          unit === "sessions" ? row.session_id || row.unit_id : row.unit_id,
        )
        .filter(Boolean),
    ),
  ];
  const params = new URLSearchParams({ tab: "traces", selectedTab: "trace" });
  const times = items
    .map((row) => new Date(row.time).getTime())
    .filter(Number.isFinite);
  if (times.length) {
    const end = Math.min(Date.now(), Math.max(...times) + 2 * DAY_MS);
    params.set(
      "primaryTraceDateFilter",
      JSON.stringify({
        dateFilter: [
          formatDate(new Date(Math.min(...times) - DAY_MS)),
          formatDate(new Date(end)),
        ],
        dateOption: "Custom",
      }),
    );
  }
  params.set(
    LINK_FILTER_PARAM,
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
