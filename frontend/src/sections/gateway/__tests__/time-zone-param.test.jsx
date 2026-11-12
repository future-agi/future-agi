import React from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  afterAll,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import { useAnalyticsUsage } from "../analytics/hooks/useAnalyticsUsage";
import { useAnalyticsErrors } from "../analytics/hooks/useAnalyticsErrors";
import { useAnalyticsLatency } from "../analytics/hooks/useAnalyticsLatency";
import { useGuardrailTrends } from "../guardrails/hooks/useGuardrailAnalytics";
import { useRequestVolume } from "../logs/hooks/useRequestLogs";

// Every gateway time series asks for the viewer's zone
// (`tz`), so the API cuts day buckets at the viewer's midnight.
const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, get } };
});

const wrapper = ({ children }) => (
  <QueryClientProvider
    client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
  >
    {children}
  </QueryClientProvider>
);

const WINDOW = {
  start: "2026-09-22T00:00:00Z",
  end: "2026-10-22T00:00:00Z",
  granularity: "day",
};

// [path, the hook under test, its arguments]
const HOOKS = {
  "usage-timeseries": [useAnalyticsUsage, [WINDOW]],
  "error-breakdown": [useAnalyticsErrors, [WINDOW]],
  "latency-stats": [useAnalyticsLatency, [WINDOW]],
  "guardrail-trends": [useGuardrailTrends, [WINDOW]],
  "request-logs/volume": [useRequestVolume, [{}, WINDOW]],
};

function renderSeriesHook(path) {
  const [useSeries, args] = HOOKS[path];
  return renderHook(() => useSeries(...args), { wrapper });
}

describe("gateway time series ask for the viewer's zone", () => {
  beforeAll(() => vi.stubEnv("TZ", "America/Los_Angeles"));
  afterAll(() => vi.unstubAllEnvs());
  beforeEach(() => {
    get.mockReset();
    get.mockResolvedValue({
      data: { status: true, result: { series: [], time_zone: "UTC" } },
    });
  });

  it.each(Object.keys(HOOKS))("%s sends tz", async (path) => {
    renderSeriesHook(path);
    await waitFor(() => expect(get).toHaveBeenCalled());
    const [url, config] = get.mock.calls[0];
    expect(url).toContain(path);
    expect(config.params.tz).toBe("America/Los_Angeles");
  });

  it("guardrail trends keep the zone the API used", async () => {
    get.mockResolvedValue({
      data: {
        status: true,
        result: {
          granularity: "day",
          time_zone: "America/Los_Angeles",
          series: [
            { timestamp: "2026-10-21T00:00:00-07:00", trigger_count: 2 },
          ],
        },
      },
    });
    const { result } = renderSeriesHook("guardrail-trends");
    await waitFor(() => expect(result.current.data).toBeTruthy());
    expect(result.current.data.time_zone).toBe("America/Los_Angeles");
    expect(result.current.data.series).toHaveLength(1);
  });
});
