import { describe, expect, it } from "vitest";

import { INTERACTIVE_REQUEST_TIMEOUT_MS } from "src/config/runtime_limits";

import {
  keepPreviousMonitorGraphData,
  MONITOR_GRAPH_CLIENT_TIMEOUT_MS,
  monitorGraphDisplayState,
  monitorGraphRequestConfig,
} from "./monitor_graph_read";

describe("monitor graph bounded reads", () => {
  it("forwards cancellation and applies a client timeout beyond the backend wall", () => {
    const controller = new AbortController();
    const dateFilter = {
      dateFilter: ["2026-08-01T00:00:00Z", "2026-08-02T00:00:00Z"],
    };

    expect(
      monitorGraphRequestConfig({ signal: controller.signal, dateFilter }),
    ).toEqual({
      signal: controller.signal,
      timeout: MONITOR_GRAPH_CLIENT_TIMEOUT_MS,
      params: {
        start_date: "2026-08-01T00:00:00Z",
        end_date: "2026-08-02T00:00:00Z",
      },
    });
    expect(MONITOR_GRAPH_CLIENT_TIMEOUT_MS).toBe(
      INTERACTIVE_REQUEST_TIMEOUT_MS,
    );
  });

  it("keeps the previous query payload while a new key is loading", () => {
    const previous = { data: { result: [{ timestamp: "old", value: 1 }] } };

    expect(keepPreviousMonitorGraphData(previous)).toBe(previous);
  });

  it("keeps prior data visible with an explicit error after transport failure", () => {
    const retainedData = [{ timestamp: "old", value: 1 }];

    expect(
      monitorGraphDisplayState({
        latestData: undefined,
        retainedData,
        isError: true,
      }),
    ).toEqual({
      data: retainedData,
      showError: true,
      showGraph: true,
    });
  });

  it("never turns a first transport failure into an empty graph", () => {
    expect(
      monitorGraphDisplayState({
        latestData: undefined,
        retainedData: undefined,
        isError: true,
      }),
    ).toEqual({
      data: undefined,
      showError: true,
      showGraph: false,
    });
  });
});
