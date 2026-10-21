/**
 * Rolling Observe presets send one stable window per hour.
 *
 * The exact latency/system-metric charts cache one snapshot per window. A
 * start that moves every second (and a default that ended at 23:59:59 while a
 * picked preset ended at the next midnight) meant no visit ever matched a
 * previous one. Every rolling preset site now floors the start to the hour and
 * ends at startOfTomorrow, so a default "Past 7D" and a picked "Past 7D" in the
 * same hour send byte-identical bounds; "Today" and custom ranges are as before.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { startOfToday, startOfTomorrow, startOfYesterday, sub } from "date-fns";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import axios from "src/utils/axios";
import { formatDate } from "src/utils/report-utils";
import { observePresetDateFilter, presetToRange } from "../timeWindowPresets";
import { getDefaultDateRange } from "../dateRangeDefaults";
import { dateFilterForOption } from "../LLMTracing/DateRangePill";
import ObserveToolbar from "../LLMTracing/ObserveToolbar";
import PrimaryGraph from "../LLMTracing/GraphSection/PrimaryGraph";
import { CompareGraphHeader } from "../LLMTracing/LLMTracingView";

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/custom-datepicker/DatePicker", () => ({
  default: () => null,
}));
vi.mock("../LLMTracing/TraceFilterPanel", () => ({ default: () => null }));
vi.mock("../LLMTracing/DisplayPanel", () => ({ default: () => null }));
vi.mock("../LLMTracing/BulkActionsBar", () => ({ default: () => null }));
vi.mock("../LLMTracing/tabStore", () => ({
  useTabStoreShallow: (selector) => selector({ openCreateModal: vi.fn() }),
}));
vi.mock("react-apexcharts", () => ({
  default: () => <div data-testid="apex-chart" />,
}));
vi.mock("src/hooks/useDashboards", () => ({
  PROPERTY_CATALOG_REQUEST_TIMEOUT_MS: 9_000,
  isPropertyCatalogNotReadyError: () => true,
  usePropertyCatalog: () => ({
    error: {
      response: { status: 503, data: { code: "property_catalog_not_ready" } },
    },
    legacyFallbackRequired: true,
    metrics: [],
  }),
}));
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  endpoints: {
    dashboard: { metrics: "/dashboard/metrics/" },
    project: {
      getTraceGraphData: () => "/tracer/trace/get_graph_methods/",
      getSpanGraphData: () => "/tracer/observation-span/get_graph_methods/",
    },
  },
}));

const HOUR_MS = 60 * 60 * 1000;
const ROLLING = ["7D", "30D", "3M", "6M", "12M"];
// The start is floored on UTC hours, so the two instants must share a UTC
// hour in every runner timezone (a local 14:37/14:59 pair straddles a UTC
// hour in +05:45 and +12:45 zones). No zone has a local midnight between
// 14:37 and 15:00 UTC, so they also share the viewer's local day.
const NOW = new Date(Date.UTC(2026, 8, 21, 14, 37, 12));
const SAME_HOUR = new Date(Date.UTC(2026, 8, 21, 14, 59, 58));

const freeze = (instant) => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(instant);
};

afterEach(() => {
  vi.useRealTimers();
});

const epochMs = (formatted) => new Date(formatted).getTime();

// Resolve a `setDateFilter(updater)` call the way React state would.
const appliedFilter = (setDateFilter) => {
  const arg = setDateFilter.mock.calls.at(-1)[0];
  return typeof arg === "function" ? arg({}) : arg;
};

describe("observePresetDateFilter", () => {
  it("floors every rolling start to the hour and ends at startOfTomorrow", () => {
    for (const option of ROLLING) {
      const [start, end] = observePresetDateFilter(option, NOW);
      const [rawStart] = presetToRange(option, NOW);
      expect(epochMs(start) % HOUR_MS).toBe(0);
      expect(epochMs(start)).toBeLessThanOrEqual(rawStart.getTime());
      expect(rawStart.getTime() - epochMs(start)).toBeLessThan(HOUR_MS);
      expect(end).toBe(formatDate(presetToRange(option, NOW)[1]));
    }
  });

  it("returns null for presets it does not own", () => {
    // The Observe sites never offer the sub-day presets; if one is ever
    // passed, it must not silently send a window that moves every second.
    for (const option of ["30 mins", "6 hrs", "Custom", "nope", undefined]) {
      expect(observePresetDateFilter(option, NOW)).toBeNull();
    }
    expect(observePresetDateFilter("Today", NOW)).toEqual(
      presetToRange("Today", NOW).map(formatDate),
    );
    expect(observePresetDateFilter("Yesterday", NOW)).toEqual(
      presetToRange("Yesterday", NOW).map(formatDate),
    );
  });

  it("is byte-identical within one hour and moves across the hour", () => {
    for (const option of ROLLING) {
      expect(observePresetDateFilter(option, SAME_HOUR)).toEqual(
        observePresetDateFilter(option, NOW),
      );
      const nextHour = new Date(NOW.getTime() + HOUR_MS);
      expect(observePresetDateFilter(option, nextHour)[0]).not.toBe(
        observePresetDateFilter(option, NOW)[0],
      );
    }
  });

  it("keeps Today and Yesterday unchanged and has no window for Custom", () => {
    freeze(NOW);
    expect(observePresetDateFilter("Today")).toEqual([
      formatDate(startOfToday()),
      formatDate(startOfTomorrow()),
    ]);
    expect(observePresetDateFilter("Yesterday")).toEqual([
      formatDate(startOfYesterday()),
      formatDate(startOfToday()),
    ]);
    expect(observePresetDateFilter("Custom")).toBeNull();
    expect(observePresetDateFilter("nonsense")).toBeNull();
  });

  it("keeps presetToRange unrounded; only the Observe helper floors the start", () => {
    // Task scheduling and the saved-view picker keep their exact instants.
    expect(presetToRange("7D", NOW)[0]).toEqual(sub(NOW, { days: 7 }));
    expect(presetToRange("30 mins", NOW)).toEqual([
      sub(NOW, { minutes: 30 }),
      NOW,
    ]);
  });
});

describe("the default window", () => {
  beforeEach(() => freeze(NOW));

  it("is the same identity as a picked preset", () => {
    expect(getDefaultDateRange("7D")).toEqual({
      dateFilter: observePresetDateFilter("7D"),
      dateOption: "7D",
    });
    expect(getDefaultDateRange("6M").dateFilter).toEqual(
      observePresetDateFilter("6M"),
    );
    expect(dateFilterForOption("7D")).toEqual(
      getDefaultDateRange("7D").dateFilter,
    );
  });

  it("does not move on a reload in the same hour", () => {
    const first = getDefaultDateRange("7D").dateFilter;
    vi.setSystemTime(SAME_HOUR);
    expect(getDefaultDateRange("7D").dateFilter).toEqual(first);
  });

  it("keeps Today exactly as before", () => {
    expect(getDefaultDateRange("Today")).toEqual({
      dateFilter: [formatDate(startOfToday()), formatDate(startOfTomorrow())],
      dateOption: "Today",
    });
  });
});

describe("every preset picker sends the default's window", () => {
  beforeEach(() => freeze(NOW));

  const pick = async (openLabel, optionLabel) => {
    fireEvent.click(await screen.findByRole("button", { name: openLabel }));
    const items = await screen.findAllByRole("menuitem", { name: optionLabel });
    fireEvent.click(items.at(-1));
  };

  it("ObserveToolbar", async () => {
    const setDateFilter = vi.fn();
    render(
      <ObserveToolbar
        inline
        tab="trace"
        isFilterOpen={false}
        onFilterToggle={vi.fn()}
        onApplyExtraFilters={vi.fn()}
        dateLabel="Past 7D"
        setDateFilter={setDateFilter}
      />,
    );
    await pick("Past 7D", "Past 30D");
    expect(appliedFilter(setDateFilter)).toEqual({
      dateFilter: observePresetDateFilter("30D"),
      dateOption: "30D",
    });
    await pick("Past 7D", "Past 7D");
    expect(appliedFilter(setDateFilter).dateFilter).toEqual(
      getDefaultDateRange("7D").dateFilter,
    );
    await pick("Past 7D", "Today");
    expect(appliedFilter(setDateFilter).dateFilter).toEqual([
      formatDate(startOfToday()),
      formatDate(startOfTomorrow()),
    ]);
  });

  it("the PrimaryGraph compare pill", async () => {
    axios.post.mockResolvedValue({ data: { result: completeGraph } });
    const setDateFilter = vi.fn();
    renderGraph(
      <PrimaryGraph
        observeIdOverride="project-1"
        showDateFilter
        dateFilter={{ dateOption: "7D" }}
        setDateFilter={setDateFilter}
      />,
    );
    await pick("7D", "Past 7D");
    expect(appliedFilter(setDateFilter).dateFilter).toEqual(
      getDefaultDateRange("7D").dateFilter,
    );
    await pick("7D", "Past 12M");
    expect(appliedFilter(setDateFilter).dateFilter).toEqual(
      observePresetDateFilter("12M"),
    );
  });

  it("the LLMTracingView compare pill", async () => {
    const setDateFilter = vi.fn();
    render(
      <CompareGraphHeader
        compareType="primary"
        dateFilter={{ dateOption: "7D" }}
        setDateFilter={setDateFilter}
        onFilterToggle={vi.fn()}
        hasActiveFilter={false}
        extraFilters={[]}
        onRemoveFilter={vi.fn()}
        onClearFilters={vi.fn()}
        fieldLabelMap={{}}
      />,
    );
    await pick("7D", "Past 7D");
    expect(appliedFilter(setDateFilter).dateFilter).toEqual(
      getDefaultDateRange("7D").dateFilter,
    );
    await pick("7D", "Past 3M");
    expect(appliedFilter(setDateFilter).dateFilter).toEqual(
      observePresetDateFilter("3M"),
    );
  });
});

const completeGraph = {
  metric_name: "latency",
  data: [],
  query_complete: true,
  query_status: "complete",
  query_sampled: false,
  query_completed_at: "2026-09-21T09:00:00Z",
};

let queryClient;
const renderGraph = (ui) =>
  render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);

beforeEach(() => {
  vi.clearAllMocks();
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  axios.get.mockResolvedValue({ data: { result: { metrics: [] } } });
});

describe("PrimaryGraph revisit", () => {
  it("asks the server again when the same chart mounts again in the app", async () => {
    axios.post.mockResolvedValue({ data: { result: completeGraph } });
    const first = renderGraph(<PrimaryGraph observeIdOverride="project-1" />);
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    first.unmount();

    // Same query key, same QueryClient: an in-app revisit. The server decides
    // whether the cached snapshot is still current.
    renderGraph(<PrimaryGraph observeIdOverride="project-1" />);
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(2));
    expect(axios.post.mock.calls[1][1]).toEqual(axios.post.mock.calls[0][1]);
  });
});
