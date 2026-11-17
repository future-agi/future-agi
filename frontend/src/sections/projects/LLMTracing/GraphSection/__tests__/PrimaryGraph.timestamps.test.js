import process from "node:process";
import React from "react";
import {
  afterAll,
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, waitFor } from "src/utils/test-utils";
import axios from "src/utils/axios";
import PrimaryGraph from "../PrimaryGraph";

// Capture the real chart inputs without rendering ApexCharts. The mock does
// not consume props or hooks; assertions inspect the recorded calls instead.
const { chartMock } = vi.hoisted(() => ({ chartMock: vi.fn(() => null) }));
vi.mock("react-apexcharts", () => ({ default: chartMock }));
vi.mock("src/components/custom-datepicker/DatePicker", () => ({
  default: () => null,
}));
vi.mock("src/hooks/useDashboards", () => ({
  PROPERTY_CATALOG_REQUEST_TIMEOUT_MS: 9_000,
  isPropertyCatalogNotReadyError: () => false,
  usePropertyCatalog: () => ({ metrics: [], legacyFallbackRequired: false }),
}));
vi.mock("../../common", () => ({ toBackendFilters: (filters) => filters }));
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  endpoints: {
    dashboard: { metrics: "/dashboard/metrics/" },
    project: {
      getTraceGraphData: () => "/tracer/trace/get_graph_methods/",
    },
  },
}));

const TIMEZONES = [
  {
    zone: "Asia/Kolkata",
    offsetMinutes: -330,
    localBucket: [15, 30],
    localRange: ["2026-10-20 15:00:00", "2026-10-20 16:00:00"],
  },
  {
    zone: "America/New_York",
    offsetMinutes: 240,
    localBucket: [6, 0],
    localRange: ["2026-10-20 05:30:00", "2026-10-20 06:30:00"],
  },
];

const TIMESTAMPS = [
  {
    label: "zone-less UTC bucket",
    timestamp: "2026-10-20T10:00:00",
    expectedIso: "2026-10-20T10:00:00.000Z",
  },
  {
    label: "fractional zone-less UTC bucket",
    timestamp: "2026-10-20T10:00:00.123",
    expectedIso: "2026-10-20T10:00:00.123Z",
  },
  {
    label: "explicit +00:00 offset",
    timestamp: "2026-10-20T10:00:00+00:00",
    expectedIso: "2026-10-20T10:00:00.000Z",
  },
  {
    label: "explicit Z offset",
    timestamp: "2026-10-20T10:00:00Z",
    expectedIso: "2026-10-20T10:00:00.000Z",
  },
  {
    label: "positive non-UTC offset",
    timestamp: "2026-10-20T15:30:00+05:30",
    expectedIso: "2026-10-20T10:00:00.000Z",
  },
  {
    label: "negative non-UTC offset",
    timestamp: "2026-10-20T06:00:00-04:00",
    expectedIso: "2026-10-20T10:00:00.000Z",
  },
];

// The normal Vitest fork pool supports changing TZ. Assert the effective zone
// in every case so a runner that ignores TZ cannot silently pass in UTC.
describe.each(TIMEZONES)(
  "PrimaryGraph timestamp instants in $zone",
  ({ zone, offsetMinutes, localBucket, localRange }) => {
    let previousTimezone;
    let queryClient;

    beforeAll(() => {
      previousTimezone = process.env.TZ;
      process.env.TZ = zone;
    });

    afterAll(() => {
      if (previousTimezone === undefined) delete process.env.TZ;
      else process.env.TZ = previousTimezone;
    });

    beforeEach(() => {
      vi.clearAllMocks();
      expect(new Date("2026-10-20T10:00:00Z").getTimezoneOffset()).toBe(
        offsetMinutes,
      );
      queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false, gcTime: Infinity } },
      });
      axios.get.mockResolvedValue({
        data: { result: { metrics: [], has_more: false, page: 1 } },
      });
    });

    afterEach(() => {
      cleanup();
      queryClient?.clear();
    });

    async function renderBucket(timestamp, setDateFilter = vi.fn()) {
      axios.post.mockResolvedValue({
        data: {
          result: {
            metric_name: "latency",
            query_complete: true,
            query_status: "complete",
            query_sampled: false,
            query_completed_at: "2026-10-20T11:00:00Z",
            data: [{ timestamp, value: 120, primary_traffic: 3 }],
          },
        },
      });
      render(
        React.createElement(
          QueryClientProvider,
          { client: queryClient },
          React.createElement(PrimaryGraph, {
            observeIdOverride: "project-timestamps",
            selectedInterval: "hour",
            setDateFilter,
          }),
        ),
      );
      await waitFor(() => expect(chartMock).toHaveBeenCalled());
      return chartMock.mock.calls.at(-1)[0];
    }

    it.each(TIMESTAMPS)(
      "plots $label at the same instant in metric and traffic series",
      async ({ timestamp, expectedIso }) => {
        const { series, options } = await renderBucket(timestamp);
        const expectedEpoch = Date.parse(expectedIso);
        expect(series[0].data[0]).toMatchObject({ x: expectedEpoch, y: 120 });
        expect(series[1].data[0]).toEqual({ x: expectedEpoch, y: 3 });
        expect(options.xaxis.labels.datetimeUTC).toBe(false);
        const localDate = new Date(series[0].data[0].x);
        expect([localDate.getHours(), localDate.getMinutes()]).toEqual(
          localBucket,
        );
      },
    );

    it.each([
      { label: "zone-less UTC bucket", timestamp: "2026-10-20T10:00:00" },
      {
        label: "explicit +00:00 offset",
        timestamp: "2026-10-20T10:00:00+00:00",
      },
    ])(
      "zooming around $label keeps the represented trace in the UTC filter",
      async ({ timestamp }) => {
        const setDateFilter = vi.fn();
        const { series, options } = await renderBucket(
          timestamp,
          setDateFilter,
        );
        // Start from the plotted point, not a separately supplied correct epoch:
        // a shifted series must also fail the subsequent investigation filter.
        const bucket = series[0].data[0].x;
        const halfHour = 30 * 60 * 1000;
        act(() => {
          options.chart.events.zoomed(null, {
            xaxis: { min: bucket - halfHour, max: bucket + halfHour },
          });
        });
        expect(setDateFilter).toHaveBeenCalledExactlyOnceWith({
          dateFilter: localRange,
          dateOption: "Custom",
        });
        // useLLMTracingFilters converts these local strings with the same
        // Date(...).toISOString() operation before building the next request.
        const nextRange = setDateFilter.mock.calls[0][0].dateFilter.map(
          (value) => new Date(value).toISOString(),
        );
        expect(nextRange).toEqual([
          "2026-10-20T09:30:00.000Z",
          "2026-10-20T10:30:00.000Z",
        ]);
        const trace = Date.parse("2026-10-20T10:10:00Z");
        expect(trace).toBeGreaterThanOrEqual(Date.parse(nextRange[0]));
        expect(trace).toBeLessThanOrEqual(Date.parse(nextRange[1]));
      },
    );
  },
);
