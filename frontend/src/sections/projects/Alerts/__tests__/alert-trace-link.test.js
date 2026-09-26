import { describe, expect, it } from "vitest";

import { OBSERVE_LINK_FILTER_PARAM } from "src/sections/projects/LLMTracing/common";

import { buildAlertTraceLink } from "../alertTraceLink";
import { pickTraceWindow } from "../store/useAlertSheetView";

const PROJECT_ID = "30885e2f-8da1-43d8-94b4-d7cc77347df2";

const LOG = {
  id: "5cfa68e4-eeb2-4fdb-9ad3-824f67c994fe",
  time_window_start: "2026-08-13T07:02:55.906630Z",
  time_window_end: "2026-08-13T08:02:55.906630Z",
};

const MONITOR_FILTERS = {
  observation_type: ["llm"],
  span_attributes_filters: [
    {
      column_id: "customer_tier",
      filter_config: {
        col_type: "SPAN_ATTRIBUTE",
        filter_op: "equals",
        filter_type: "text",
        filter_value: "enterprise",
      },
    },
  ],
};

const paramsOf = (url) => new URLSearchParams(url.split("?")[1]);

describe("buildAlertTraceLink", () => {
  it("targets the project's trace list on the trace tab", () => {
    const url = buildAlertTraceLink({
      projectId: PROJECT_ID,
      windowStart: LOG.time_window_start,
      windowEnd: LOG.time_window_end,
      monitorFilters: MONITOR_FILTERS,
    });

    expect(url.split("?")[0]).toBe(
      `/dashboard/observe/${PROJECT_ID}/llm-tracing`,
    );
    expect(paramsOf(url).get("tab")).toBe("traces");
    expect(paramsOf(url).get("selectedTab")).toBe("trace");
  });

  it("carries the fire's window as a custom range", () => {
    const url = buildAlertTraceLink({
      projectId: PROJECT_ID,
      windowStart: LOG.time_window_start,
      windowEnd: LOG.time_window_end,
      monitorFilters: MONITOR_FILTERS,
    });
    const dateFilter = JSON.parse(paramsOf(url).get("primaryTraceDateFilter"));

    expect(dateFilter.dateOption).toBe("Custom");
    expect(dateFilter.dateFilter).toHaveLength(2);
  });

  // The picker round-trips local-time "yyyy-MM-dd HH:mm:ss"; the log's window is
  // UTC ISO. Slicing the ISO string would silently shift the window by the
  // viewer's offset.
  it("converts the window to local time rather than slicing the ISO string", () => {
    const url = buildAlertTraceLink({
      projectId: PROJECT_ID,
      windowStart: LOG.time_window_start,
      windowEnd: LOG.time_window_end,
      monitorFilters: MONITOR_FILTERS,
    });
    const [start, end] = JSON.parse(
      paramsOf(url).get("primaryTraceDateFilter"),
    ).dateFilter;

    for (const value of [start, end]) {
      expect(value).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/);
      expect(value).not.toContain("T");
      expect(value).not.toContain("Z");
    }
    expect(start).toBe(
      new Date(LOG.time_window_start)
        .toLocaleString("sv-SE", { hour12: false })
        .replace("T", " "),
    );
  });

  it("carries the rule's span type and attribute filters", () => {
    const url = buildAlertTraceLink({
      projectId: PROJECT_ID,
      windowStart: LOG.time_window_start,
      windowEnd: LOG.time_window_end,
      monitorFilters: MONITOR_FILTERS,
    });
    const rows = JSON.parse(paramsOf(url).get(OBSERVE_LINK_FILTER_PARAM));

    expect(rows.map((row) => row.column_id).sort()).toEqual([
      "customer_tier",
      "node_type",
    ]);
  });

  it("passes the metric type through, so the link scopes to failed spans", () => {
    const url = buildAlertTraceLink({
      projectId: PROJECT_ID,
      windowStart: LOG.time_window_start,
      windowEnd: LOG.time_window_end,
      monitorFilters: MONITOR_FILTERS,
      metricType: "count_of_errors",
    });
    const rows = JSON.parse(paramsOf(url).get(OBSERVE_LINK_FILTER_PARAM));

    expect(rows.map((row) => row.column_id)).toContain("status");
  });

  it("carries only the rule's filters when the metric adds nothing", () => {
    const url = buildAlertTraceLink({
      projectId: PROJECT_ID,
      windowStart: LOG.time_window_start,
      windowEnd: LOG.time_window_end,
      monitorFilters: MONITOR_FILTERS,
      metricType: "span_response_time",
    });
    const rows = JSON.parse(paramsOf(url).get(OBSERVE_LINK_FILTER_PARAM));

    expect(rows.map((row) => row.column_id)).not.toContain("status");
  });

  it("still builds a filter param for a metric-only scope", () => {
    const url = buildAlertTraceLink({
      projectId: PROJECT_ID,
      windowStart: LOG.time_window_start,
      windowEnd: LOG.time_window_end,
      monitorFilters: {},
      metricType: "count_of_errors",
    });
    const rows = JSON.parse(paramsOf(url).get(OBSERVE_LINK_FILTER_PARAM));

    expect(rows.map((row) => row.column_id)).toEqual(["status"]);
  });

  it("omits the filter param when the rule has no filters", () => {
    const url = buildAlertTraceLink({
      projectId: PROJECT_ID,
      windowStart: LOG.time_window_start,
      windowEnd: LOG.time_window_end,
      monitorFilters: {},
    });

    expect(paramsOf(url).get(OBSERVE_LINK_FILTER_PARAM)).toBeNull();
    expect(paramsOf(url).get("primaryTraceDateFilter")).not.toBeNull();
  });

  // Both window fields are nullable on the model, so alerts that fired before
  // the columns existed have neither.
  it("omits the date param when the log has no window", () => {
    const url = buildAlertTraceLink({
      projectId: PROJECT_ID,
      windowStart: null,
      windowEnd: null,
      monitorFilters: MONITOR_FILTERS,
    });

    expect(paramsOf(url).get("primaryTraceDateFilter")).toBeNull();
    expect(paramsOf(url).get(OBSERVE_LINK_FILTER_PARAM)).not.toBeNull();
  });

  it("still returns the bare trace list when there is no log at all", () => {
    const url = buildAlertTraceLink({
      projectId: PROJECT_ID,
      windowStart: null,
      windowEnd: null,
      monitorFilters: {},
    });

    expect(url).toBe(
      `/dashboard/observe/${PROJECT_ID}/llm-tracing?tab=traces&selectedTab=trace`,
    );
  });

  it("returns null without a project", () => {
    expect(
      buildAlertTraceLink({
        projectId: null,
        windowStart: LOG.time_window_start,
        windowEnd: LOG.time_window_end,
        monitorFilters: MONITOR_FILTERS,
      }),
    ).toBeNull();
  });
});

describe("pickTraceWindow", () => {
  // DETAILS.logs.results is whatever slice the issues grid last fetched.
  // window_start/window_end are rule-level and span every fire, so the header
  // must read those and not the slice.
  const DETAILS = {
    window_start: "2026-08-12T07:32:55.906630Z",
    window_end: "2026-08-13T08:02:55.906630Z",
    logs: {
      results: [
        {
          id: "older-slice",
          created_at: "2026-08-12T08:32:55Z",
          time_window_start: "2026-08-12T07:32:55.906630Z",
          time_window_end: "2026-08-12T08:32:55.906630Z",
        },
      ],
    },
  };

  it("uses the clicked row's own window", () => {
    const clicked = {
      time_window_start: "2026-08-13T04:32:55.906630Z",
      time_window_end: "2026-08-13T05:32:55.906630Z",
    };

    expect(pickTraceWindow(DETAILS, clicked)).toEqual({
      windowStart: "2026-08-13T04:32:55.906630Z",
      windowEnd: "2026-08-13T05:32:55.906630Z",
    });
  });

  it("spans every fire for the header button", () => {
    expect(pickTraceWindow(DETAILS, undefined)).toEqual({
      windowStart: "2026-08-12T07:32:55.906630Z",
      windowEnd: "2026-08-13T08:02:55.906630Z",
    });
  });

  // The regression this replaced: the header read the newest row out of
  // logs.results, so a type filter or page 2 silently moved its window.
  it("ignores the loaded slice entirely", () => {
    const paged = {
      ...DETAILS,
      logs: { results: [{ id: "page-2", created_at: "2026-08-12T08:32:55Z" }] },
    };

    expect(pickTraceWindow(paged, undefined)).toEqual(
      pickTraceWindow(DETAILS, undefined),
    );
  });

  it("accepts the camelCase alias the store normalizes to", () => {
    expect(
      pickTraceWindow(
        {
          windowStart: "2026-08-01T00:00:00Z",
          windowEnd: "2026-08-02T00:00:00Z",
        },
        undefined,
      ),
    ).toEqual({
      windowStart: "2026-08-01T00:00:00Z",
      windowEnd: "2026-08-02T00:00:00Z",
    });
  });

  it("yields no window when the alert has never fired", () => {
    expect(pickTraceWindow({}, undefined)).toEqual({
      windowStart: undefined,
      windowEnd: undefined,
    });
    expect(pickTraceWindow(undefined, undefined)).toEqual({
      windowStart: undefined,
      windowEnd: undefined,
    });
  });
});
