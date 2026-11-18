import React from "react";
import PropTypes from "prop-types";
import { act, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const http = vi.hoisted(() => ({ read: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: {},
  readQuery: (...args) => http.read(...args),
  endpoints: {
    project: {
      insightsConfigs: () => "/configs",
      insightsConfig: (id) => `/configs/${id}`,
      insightsRun: (id) => `/runs/${id}`,
    },
  },
}));

import {
  failedRunNext,
  useInsightsConfig,
  useInsightsConfigs,
  useInsightsRun,
} from "../insightsApi";

const ok = (result) => Promise.resolve({ data: { status: true, result } });
const RUNNING = { id: "r1", status: "running" };

const wrapper = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const Wrapper = ({ children }) =>
    React.createElement(QueryClientProvider, { client }, children);
  Wrapper.propTypes = { children: PropTypes.node };
  return Wrapper;
};

// A running report read once, then every read failing: polling must stop on
// the failure instead of going on from the last good (running) data.
describe.each([
  ["a run", () => useInsightsRun("r1"), RUNNING],
  [
    "a report",
    () => useInsightsConfig("c1"),
    { id: "c1", latest_run: RUNNING },
  ],
  [
    "the report list",
    () => useInsightsConfigs("p1"),
    { configs: [{ id: "c1", latest_run: RUNNING }] },
  ],
])("polling %s that is running", (_, useHook, first) => {
  beforeEach(() => {
    vi.useFakeTimers();
    http.read.mockReset();
    http.read
      .mockImplementationOnce(() => ok(first))
      .mockImplementation(() =>
        Promise.reject({ statusCode: 503, message: "down" }),
      );
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  // In steps, so each read's result lands before the next timer.
  const wait = async (ms) => {
    for (let left = ms; left > 0; left -= 1000) {
      // eslint-disable-next-line no-await-in-loop
      await act(async () => {
        await vi.advanceTimersByTimeAsync(Math.min(1000, left));
      });
    }
  };

  it("stops once a read fails", async () => {
    const { result } = renderHook(useHook, { wrapper: wrapper() });
    await wait(100);
    expect(http.read).toHaveBeenCalledTimes(1);
    // Read as the page reads it (the result only re-renders for what is read).
    expect(result.current.isError).toBe(false);
    // The next poll fails...
    await wait(6000);
    expect(result.current.isError).toBe(true);
    const reads = http.read.mock.calls.length;
    // ...and nothing polls after it.
    await wait(60_000);
    expect(http.read).toHaveBeenCalledTimes(reads);
  });
});

describe("failedRunNext", () => {
  it("offers what can help after a run fails, by its error code", () => {
    const next = (code, options) =>
      failedRunNext({ error_code: code }, options);
    expect(next("")).toEqual({
      stopped: false,
      license: false,
      rerun: "Retry",
    });
    expect(next("stopped")).toEqual({
      stopped: true,
      license: false,
      rerun: "Run again",
    });
    expect(next("model_setup")).toEqual({
      stopped: false,
      license: false,
      rerun: null,
    });
    // A licence that can't run Insights (or lapsed mid-run): the License
    // settings on a self-hosted install, never Retry; Cloud has no License
    // settings, so its reason (the plan) stands alone.
    expect(next("license")).toEqual({
      stopped: false,
      license: true,
      rerun: null,
    });
    expect(next("license", { cloud: true })).toEqual({
      stopped: false,
      license: false,
      rerun: null,
    });
  });
});

describe("a run being stopped", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("refreshes the reports once it stops, and is read no more: its counts are final", async () => {
    vi.useFakeTimers();
    const run = { id: "r1", config_id: "c1", status: "running" };
    let read = run;
    http.read.mockReset();
    http.read.mockImplementation(() => ok(read));
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const refreshed = vi.spyOn(client, "invalidateQueries");
    const Wrapper = ({ children }) =>
      React.createElement(QueryClientProvider, { client }, children);
    Wrapper.propTypes = { children: PropTypes.node };
    const { result } = renderHook(() => useInsightsRun("r1"), {
      wrapper: Wrapper,
    });
    const tick = () =>
      act(async () => {
        await vi.advanceTimersByTimeAsync(2000);
      });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(result.current.data.status).toBe("running");

    read = {
      ...run,
      status: "failed",
      error_code: "stopped",
      analysed_count: 8,
      // Written by a build that waited for the reads under way: nothing
      // is saved after a stop now.
      progress: { in_flight: { 7: new Date().toISOString() } },
    };
    await tick();
    expect(result.current.data.analysed_count).toBe(8);
    const refreshes = (key) =>
      refreshed.mock.calls.filter(
        ([filters]) => JSON.stringify(filters.queryKey) === JSON.stringify(key),
      ).length;
    expect(refreshes(["insights", "configs"])).toBe(1);
    expect(refreshes(["insights", "config", "c1"])).toBe(1);
    const reads = http.read.mock.calls.length;
    for (let i = 0; i < 5; i += 1) {
      // eslint-disable-next-line no-await-in-loop
      await tick();
    }
    expect(http.read).toHaveBeenCalledTimes(reads);
    expect(refreshes(["insights", "configs"])).toBe(1);
  });
});
