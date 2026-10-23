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
