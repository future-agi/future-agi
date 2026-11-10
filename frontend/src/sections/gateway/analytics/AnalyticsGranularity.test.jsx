import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "src/utils/test-utils";

import UsageCharts from "./UsageCharts";
import ErrorAnalytics from "./ErrorAnalytics";
import LatencyAnalytics from "./LatencyAnalytics";

const { usageHook, errorsHook, latencyHook } = vi.hoisted(() => ({
  usageHook: vi.fn(),
  errorsHook: vi.fn(),
  latencyHook: vi.fn(),
}));

vi.mock("./hooks/useAnalyticsUsage", () => ({
  useAnalyticsUsage: (...args) => usageHook(...args),
}));

vi.mock("./hooks/useAnalyticsErrors", () => ({
  useAnalyticsErrors: (...args) => errorsHook(...args),
}));

vi.mock("./hooks/useAnalyticsLatency", () => ({
  useAnalyticsLatency: (...args) => latencyHook(...args),
}));

vi.mock("react-apexcharts", () => ({
  default: () => null,
}));

// The bucket sizes the analytics API accepts (TRUNC_MAP in
// futureagi/agentcc/services/analytics.py). It serves anything else as
// "hour", so an unknown value would leave every range on hourly buckets.
const API_GRANULARITIES = ["minute", "hour", "day", "week", "month"];

const END = "2026-01-31T12:00:00.000Z";
const rangeOf = (hours) => ({
  start: new Date(
    new Date(END).getTime() - hours * 60 * 60 * 1000,
  ).toISOString(),
  end: END,
});

const CHARTS = [
  ["Usage", UsageCharts, usageHook],
  ["Errors", ErrorAnalytics, errorsHook],
  ["Latency", LatencyAnalytics, latencyHook],
];

const RANGES = [
  ["6h", 6, "minute"],
  ["24h", 24, "hour"],
  ["7d", 7 * 24, "hour"],
  ["30d", 30 * 24, "day"],
];

describe("Gateway analytics chart granularity", () => {
  beforeEach(() => {
    for (const hook of [usageHook, errorsHook, latencyHook]) {
      hook.mockReset();
      hook.mockReturnValue({ data: undefined, isLoading: false });
    }
  });

  describe.each(CHARTS)("%s chart", (_name, Component, hook) => {
    it.each(RANGES)(
      "asks the API for a bucket size it accepts over %s",
      (_preset, hours, expected) => {
        render(<Component {...rangeOf(hours)} gatewayId="gw_123" />);

        const { granularity } = hook.mock.calls.at(-1)[0];
        expect(API_GRANULARITIES).toContain(granularity);
        expect(granularity).toBe(expected);
      },
    );
  });
});
