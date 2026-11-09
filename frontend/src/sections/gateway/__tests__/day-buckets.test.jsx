import React, { forwardRef } from "react";
import { screen, within } from "@testing-library/react";
import {
  afterAll,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import UsageCharts from "../analytics/UsageCharts";
import ErrorAnalytics from "../analytics/ErrorAnalytics";
import LatencyAnalytics from "../analytics/LatencyAnalytics";
import GuardrailAnalyticsTab from "../guardrails/GuardrailAnalyticsTab";
import RequestVolumeStrip from "../logs/RequestVolumeStrip";
import {
  DEMO_OVERVIEW,
  DEMO_RULE_STATS,
  MockAgGrid,
  renderPage,
} from "../guardrails/__tests__/helpers";

// Day buckets arrived as UTC midnights and were labelled in
// the viewer's zone, so for a viewer in Los Angeles the Oct 21 bar read
// "Oct 20" and sat at 17:00 the day before, on all five gateway day charts.
// Each series now names the zone it cut its days in (`time_zone`): the
// viewer's when the API took `tz`, UTC otherwise. A chart labels day
// buckets in that zone and says when they are UTC days.

const LA = "America/Los_Angeles";
const UTC_OCT_21 = "2026-10-21T00:00:00Z"; // a UTC day
const LA_OCT_21 = "2026-10-21T07:00:00Z"; // midnight in Los Angeles

const state = vi.hoisted(() => ({ charts: [], data: null }));

vi.mock("react-apexcharts", () => ({
  default: (props) => {
    state.charts.push(props);
    return <div data-testid="chart" />;
  },
}));
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function Grid(props, _ref) {
    return <MockAgGrid {...props} />;
  }),
}));
vi.mock("../analytics/hooks/useAnalyticsUsage", () => ({
  useAnalyticsUsage: () => ({ isLoading: false, data: state.data }),
}));
vi.mock("../analytics/hooks/useAnalyticsErrors", () => ({
  useAnalyticsErrors: () => ({ isLoading: false, data: state.data }),
}));
vi.mock("../analytics/hooks/useAnalyticsLatency", () => ({
  useAnalyticsLatency: () => ({ isLoading: false, data: state.data }),
}));
vi.mock("../logs/hooks/useRequestLogs", () => ({
  useOldestRequestTime: () => ({
    isLoading: false,
    data: "2026-09-01T00:00:00Z",
  }),
  useRequestVolume: () => ({
    isLoading: false,
    isError: false,
    data: state.data,
  }),
}));
const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, get } };
});

// One day bucket in each series' own shape.
function usage(bucket, zone) {
  return {
    granularity: "day",
    ...(zone && { time_zone: zone }),
    series: [{ bucket, request_count: 4, input_tokens: 9, output_tokens: 3 }],
  };
}
function errors(bucket, zone) {
  return {
    ...(zone && { time_zone: zone }),
    breakdown: [],
    error_timeseries: [
      { bucket, error_count: 1, total_count: 4, error_rate: 25 },
    ],
  };
}
function latency(bucket, zone) {
  return {
    granularity: "day",
    ...(zone && { time_zone: zone }),
    summary: { total_requests: 4 },
    timeseries: [
      { bucket, request_count: 4, p50_ms: 100, p95_ms: 200, p99_ms: 300 },
    ],
  };
}
function volume(bucket, zone) {
  return {
    granularity: "day",
    ...(zone && { time_zone: zone }),
    series: [{ bucket, request_count: 4, error_count: 1 }],
  };
}
function trends(bucket, zone) {
  return {
    granularity: "day",
    ...(zone && { time_zone: zone }),
    series: [{ timestamp: bucket, trigger_count: 3 }],
  };
}

function answerGuardrails(trendResult) {
  get.mockImplementation((url) => {
    if (url.includes("guardrail-overview"))
      return Promise.resolve({ data: { result: DEMO_OVERVIEW } });
    if (url.includes("guardrail-rules"))
      return Promise.resolve({ data: { result: DEMO_RULE_STATS } });
    if (url.includes("guardrail-trends"))
      return Promise.resolve({ data: { result: trendResult } });
    return Promise.reject(new Error(`unexpected ${url}`));
  });
}

const RANGE = {
  start: "2026-09-22T00:00:00Z",
  end: "2026-10-22T00:00:00Z",
  granularity: "day",
};

// Each site: render it with one day bucket, return its time chart's props.
const SITES = {
  "Analytics requests over time": {
    shape: usage,
    render: () => renderPage(<UsageCharts {...RANGE} />),
    chart: () =>
      state.charts.find((c) => c.series?.[0]?.name !== "Input tokens"),
    region: "Requests over time",
  },
  "Analytics error rate over time": {
    shape: errors,
    render: () => renderPage(<ErrorAnalytics {...RANGE} />),
    chart: () => state.charts.find((c) => c.type === "area"),
    region: "Error rate over time",
  },
  "Analytics latency over time": {
    shape: latency,
    render: () => renderPage(<LatencyAnalytics {...RANGE} />),
    chart: () => state.charts.find((c) => c.type === "line"),
    region: "Latency percentiles over time",
  },
  "Guardrail matches over time": {
    shape: trends,
    guardrails: true,
    render: () => renderPage(<GuardrailAnalyticsTab gatewayId="gw-1" />),
    chart: () => state.charts.at(-1),
    region: "Matches over time",
  },
  "Logs request volume": {
    shape: volume,
    render: () => renderPage(<RequestVolumeStrip filters={{}} />),
    chart: () => state.charts.at(-1),
    region: null, // the strip: data-testid request-volume
  },
};

async function drawn(site, bucket, zone) {
  state.charts = [];
  state.data = site.shape(bucket, zone);
  if (site.guardrails) answerGuardrails(site.shape(bucket, zone));
  site.render();
  await screen.findAllByTestId("chart");
  const props = site.chart();
  const area = site.region
    ? screen.getByRole("region", { name: site.region })
    : screen.getByTestId("request-volume");
  return { props, area };
}

describe("gateway day charts for a viewer in Los Angeles", () => {
  beforeAll(() => vi.stubEnv("TZ", LA));
  afterAll(() => vi.unstubAllEnvs());
  beforeEach(() => {
    get.mockReset();
  });

  describe.each(Object.entries(SITES))("%s", (_name, site) => {
    it("labels a UTC day bucket as its own date, on a UTC axis, and says they are UTC days", async () => {
      const { props, area } = await drawn(site, UTC_OCT_21, undefined);
      expect(props.options.xaxis.labels.datetimeUTC).toBe(true);
      expect(props.options.tooltip.x.formatter(Date.parse(UTC_OCT_21))).toBe(
        "Oct 21",
      );
      expect(within(area).getByText(/Daily bars are UTC days\./)).toBeTruthy();
    });

    it("labels the viewer's own day as its date, on a local axis", async () => {
      const { props, area } = await drawn(site, LA_OCT_21, LA);
      expect(props.options.xaxis.labels.datetimeUTC).toBe(false);
      expect(props.options.tooltip.x.formatter(Date.parse(LA_OCT_21))).toBe(
        "Oct 21",
      );
      expect(within(area).queryByText(/UTC days/)).toBeNull();
    });
  });
});
