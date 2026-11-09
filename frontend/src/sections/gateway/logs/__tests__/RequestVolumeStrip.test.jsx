import React from "react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import { createTheme } from "@mui/material/styles";

import { palette } from "src/theme/palette";

import RequestVolumeStrip from "../RequestVolumeStrip";

const usage = vi.hoisted(() => ({
  calls: [],
  result: { isLoading: false, isError: false, data: undefined },
}));
const oldest = vi.hoisted(() => ({
  calls: [],
  result: { isLoading: false, data: "2026-10-20T09:38:06.404Z" },
}));
vi.mock("../hooks/useRequestLogs", () => ({
  useOldestRequestTime: (filters, options) => {
    oldest.calls.push({ filters, options });
    return oldest.result;
  },
  useRequestVolume: (filters, window, options) => {
    usage.calls.push({ filters, params: window, options });
    return usage.result;
  },
}));
const charts = vi.hoisted(() => ({ props: null }));
vi.mock("react-apexcharts", () => ({
  default: (props) => {
    charts.props = props;
    return <div data-testid="chart" />;
  },
}));

const SERIES = [
  { bucket: "2026-10-21T08:00:00Z", request_count: 10, error_count: 2 },
  { bucket: "2026-10-21T09:00:00Z", request_count: 5, error_count: 0 },
];

const LIGHT = createTheme({ palette: palette("light") });

beforeEach(() => {
  // The fixed oldest-request fixture must not age from hourly to daily buckets
  // as the real clock advances. Leave async timers real for RTL and MUI.
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-22T08:00:00.000Z"));
  oldest.calls = [];
  oldest.result = { isLoading: false, data: "2026-10-20T09:38:06.404Z" };
  usage.calls = [];
  usage.result = {
    isLoading: false,
    isError: false,
    data: { granularity: "hour", series: SERIES },
  };
  charts.props = null;
});

afterEach(() => {
  vi.useRealTimers();
});

const lastParams = () => usage.calls.at(-1).params;

// Vercel's logs open with a time histogram; ours had none.
describe("Logs request volume strip", () => {
  it("stacks OK and error requests per bucket, in the info and error colours", () => {
    render(<RequestVolumeStrip filters={{}} />);
    expect(charts.props.type).toBe("bar");
    expect(charts.props.options.chart.stacked).toBe(true);
    expect(charts.props.series.map((s) => s.name)).toEqual(["OK", "Errors"]);
    expect(charts.props.series[0].data.map((p) => p.y)).toEqual([8, 5]);
    expect(charts.props.series[1].data.map((p) => p.y)).toEqual([2, 0]);
    expect(charts.props.options.colors).toEqual([
      LIGHT.palette.info.main,
      LIGHT.palette.error.main,
    ]);
    // Identity is never colour alone: a legend names both series.
    expect(screen.getByText("OK")).toBeInTheDocument();
    expect(screen.getByText("Errors")).toBeInTheDocument();
    expect(
      screen.getByRole("img", {
        name: /^Requests per hour, since .*: 15 requests, 2 errors\.$/,
      }),
    ).toBeInTheDocument();
  });

  it("uses daily buckets when the oldest request is more than seven days old", () => {
    vi.setSystemTime(new Date("2026-10-28T08:00:00.000Z"));
    usage.result.data = { granularity: "day", series: SERIES };
    render(<RequestVolumeStrip filters={{}} />);
    expect(lastParams()).toMatchObject({
      start: "2026-10-20T09:38:06.404Z",
      end: "2026-10-28T08:00:00.000Z",
      granularity: "day",
    });
    expect(
      screen.getByRole("img", {
        name: /^Requests per day, since .*: 15 requests, 2 errors\.$/,
      }),
    ).toBeInTheDocument();
  });

  // The strip said "last 24 hours" while the table
  // under it listed every request back to Oct 20.
  it("with no time filter, covers the table's window: the oldest matching request to now", () => {
    const before = Date.now();
    const filters = { model: "gpt-4o", apiKeyId: "key-1" };
    render(<RequestVolumeStrip filters={filters} />);
    // The oldest request that matches the table's own filters.
    expect(oldest.calls.at(-1).filters).toEqual(filters);
    expect(oldest.calls.at(-1).options.enabled).toBe(true);
    const { start, end, gatewayId } = lastParams();
    expect(start).toBe("2026-10-20T09:38:06.404Z");
    expect(Date.parse(end)).toBeGreaterThanOrEqual(before - 1000);
    // The usage contract's gateway_id is never sent from here.
    expect(gatewayId).toBeUndefined();
    expect(screen.getByText(/^Requests per \w+, since /)).toBeInTheDocument();
    expect(screen.queryByText(/last 24 hours/)).toBeNull();
  });

  it("does not look for the oldest request when the table has a start time", () => {
    render(
      <RequestVolumeStrip
        filters={{ startedAfter: "2026-10-21T06:00:00.000Z" }}
      />,
    );
    expect(oldest.calls.at(-1).options.enabled).toBe(false);
    expect(lastParams().start).toBe("2026-10-21T06:00:00.000Z");
  });

  it("says there are no requests when the table has none", () => {
    oldest.result = { isLoading: false, data: null };
    render(<RequestVolumeStrip filters={{}} />);
    expect(screen.getByText("No requests in this window.")).toBeInTheDocument();
    expect(charts.props).toBeNull();
  });

  it("follows the logs' time range and API key filters", () => {
    render(
      <RequestVolumeStrip
        filters={{
          startedAfter: "2026-10-21T06:00:00.000Z",
          startedBefore: "2026-10-21T08:00:00.000Z",
          apiKeyId: "key-1",
        }}
      />,
    );
    expect(lastParams()).toMatchObject({
      start: "2026-10-21T06:00:00.000Z",
      end: "2026-10-21T08:00:00.000Z",
      granularity: "minute",
    });
    expect(usage.calls.at(-1).filters).toMatchObject({ apiKeyId: "key-1" });
    expect(
      screen.queryByText(/other filters are not applied/),
    ).not.toBeInTheDocument();
  });

  // The strip counted every request in the window
  // whatever the table's model, status, provider or search filter, and said
  // so in a note. It now counts under the table's own filters.
  it("counts under every table filter, so no filter is left out", () => {
    const filters = {
      model: "gpt-4o",
      isError: "true",
      statusCode: "429",
      provider: "anthropic",
    };
    render(<RequestVolumeStrip filters={filters} />);
    expect(usage.calls.at(-1).filters).toEqual(filters);
    expect(
      screen.queryByText(/other filters are not applied/),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/Counts every request/)).not.toBeInTheDocument();
  });

  it("counts under the table's search too", () => {
    render(<RequestVolumeStrip filters={{ search: "timeout" }} />);
    expect(usage.calls.at(-1).filters).toEqual({ search: "timeout" });
    expect(
      screen.queryByText(/other filters are not applied/),
    ).not.toBeInTheDocument();
  });

  it("keeps a failed read to one quiet line, with no toast", () => {
    usage.result = { isLoading: false, isError: true, data: undefined };
    render(<RequestVolumeStrip filters={{}} />);
    expect(
      screen.getByText("Request volume could not be loaded."),
    ).toBeInTheDocument();
    expect(charts.props).toBeNull();
    expect(usage.calls.at(-1).options.meta).toEqual({ errorHandled: true });
  });

  it("says so when the window has no requests", () => {
    usage.result = {
      isLoading: false,
      isError: false,
      data: {
        series: SERIES.map((p) => ({ ...p, request_count: 0, error_count: 0 })),
      },
    };
    render(<RequestVolumeStrip filters={{}} />);
    expect(screen.getByText("No requests in this window.")).toBeInTheDocument();
    expect(charts.props).toBeNull();
  });
});
