import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import {
  QueryClient,
  QueryClientProvider,
  focusManager,
} from "@tanstack/react-query";
import axios from "src/utils/axios";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import RunTray, { RunTrayDock } from "../RunTray";
import { RUN_TRAY_KEY, useRunTray } from "../runTrayStore";

// Keep the real useExperimentDetail/queryFn, QueryClient and watcher options.
// Only the transport is replaced: no requests or provider egress are needed.
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: {
    develop: {
      experiment: {
        getExperimentDetails: (id) => `/model-hub/experiments/v2/${id}/`,
      },
    },
  },
}));
const { notify } = vi.hoisted(() => ({ notify: vi.fn() }));
vi.mock("src/sections/common/ux/toast", () => ({ notify }));
vi.mock("src/components/iconify", () => ({ default: () => null }));

const RUN = {
  run_id: "run-retained",
  experiment_id: "experiment-retained",
  label: "#4A",
  status: "Running",
};
const ENTRY = {
  runId: RUN.run_id,
  experimentId: RUN.experiment_id,
  label: RUN.label,
};
const QUERY_KEY = ["experiment", ENTRY.experimentId];
const RESPONSE = { data: { result: { runs: [RUN] } } };
let client;

const tick = async (ms = 1) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
};
const view = () => (
  <QueryClientProvider client={client}>
    <MemoryRouter>
      <RunTray />
      <RunTrayDock />
    </MemoryRouter>
  </QueryClientProvider>
);
const expectUnavailable = (retry, status) => {
  expect(screen.getByTestId("run-tray").textContent).toBe(
    "1 run · Status unavailable",
  );
  expect(screen.getByTestId("run-tray-dock").textContent).toBe(
    "1 run · Status unavailable",
  );
  expect(screen.getByRole("status")).toBe(status);
  expect(status.textContent).toBe("Status unavailable");
  expect(screen.getByRole("button", { name: "Retry" })).toBe(retry);
  expect(retry.disabled).toBe(false);
  expect(document.activeElement).toBe(retry);
  expect(screen.queryByText("Checking…")).toBeNull();
  expect(useRunTray.getState().runs).toEqual([ENTRY]);
  expect(notify).not.toHaveBeenCalled();
};
const focusRetry = () => {
  fireEvent.click(screen.getByTestId("run-tray"));
  const retry = screen.getByRole("button", { name: "Retry" });
  act(() => retry.focus());
  return { retry, status: screen.getByRole("status") };
};

beforeEach(() => {
  vi.useFakeTimers();
  axios.get.mockReset();
  notify.mockReset();
  focusManager.setFocused(true);
  // Match app defaults; the watcher supplies its own n < 2 retry policy.
  // Do not shorten the real 1 s + 2 s backoff or the watcher's 4 s poll.
  client = new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 5000,
        gcTime: 60_000,
        refetchOnWindowFocus: true,
        retry: 1,
      },
    },
  });
  setLaunchOverrideForTest({ launch: true, experimentsBasic: false });
  useRunTray.setState({ runs: [ENTRY], live: {}, checks: {} });
});
afterEach(() => {
  cleanup();
  client.clear();
  focusManager.setFocused(undefined);
  useRunTray.setState({ runs: [], live: {}, checks: {} });
  window.sessionStorage.removeItem(RUN_TRAY_KEY);
  setLaunchOverrideForTest(null);
  vi.useRealTimers();
});

describe("run tray failures across real query fetch transitions", () => {
  it.each([
    { kind: "network", error: new Error("Network unavailable") },
    { kind: "403", error: { statusCode: 403 } },
    { kind: "503", error: { response: { status: 503 } } },
  ])(
    "holds a cold-cache $kind failure through polling and refocus until success",
    async ({ error }) => {
      axios.get.mockRejectedValue(error);
      render(view());
      expect(screen.getByTestId("run-tray").textContent).toBe(
        "1 run · Checking…",
      );
      await tick(3100);
      expect(axios.get).toHaveBeenCalledTimes(3);
      expect(client.getQueryState(QUERY_KEY)).toMatchObject({
        data: undefined,
        status: "error",
        fetchStatus: "idle",
      });
      const { retry, status } = focusRetry();
      expectUnavailable(retry, status);
      const failedAt = client.getQueryState(QUERY_KEY).errorUpdatedAt;

      // A real poll clears error/status while its retries are in flight.
      await tick(4000);
      expect(axios.get).toHaveBeenCalledTimes(4);
      expect(client.getQueryState(QUERY_KEY)).toMatchObject({
        data: undefined,
        error: null,
        status: "pending",
        fetchStatus: "fetching",
        errorUpdatedAt: failedAt,
      });
      expectUnavailable(retry, status);
      fireEvent.click(retry);
      await tick();
      expect(axios.get).toHaveBeenCalledTimes(4);
      expectUnavailable(retry, status);

      // The next exhausted read and a refocus must not replace either node.
      await tick(3100);
      expect(axios.get).toHaveBeenCalledTimes(6);
      expect(client.getQueryState(QUERY_KEY).fetchStatus).toBe("idle");
      expectUnavailable(retry, status);
      act(() => {
        focusManager.setFocused(false);
        focusManager.setFocused(true);
      });
      await tick();
      expect(axios.get).toHaveBeenCalledTimes(7);
      expect(client.getQueryState(QUERY_KEY)).toMatchObject({
        error: null,
        status: "pending",
        fetchStatus: "fetching",
      });
      expectUnavailable(retry, status);

      axios.get.mockResolvedValue(RESPONSE);
      await tick(1100);
      expect(client.getQueryState(QUERY_KEY).status).toBe("success");
      expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
      expect(screen.queryByText("Status unavailable")).toBeNull();
      expect(screen.getByTestId("run-tray").textContent).toBe("1 run going");
      expect(screen.getByTestId("run-tray-dock").textContent).toBe(
        "1 run going · #4A",
      );
      expect(useRunTray.getState().live[RUN.run_id]).toEqual(RUN);
      expect(notify).not.toHaveBeenCalled();
    },
  );

  it("keeps cached-failure Retry enabled and deduplicates clicks during polling", async () => {
    client.setQueryData(QUERY_KEY, RESPONSE);
    axios.get.mockRejectedValue({ statusCode: 500 });
    render(view());
    expect(screen.getByTestId("run-tray").textContent).toBe("1 run going");
    await tick(7100);
    expect(axios.get).toHaveBeenCalledTimes(3);
    const { retry, status } = focusRetry();
    expectUnavailable(retry, status);

    await tick(4000);
    expect(client.getQueryState(QUERY_KEY).fetchStatus).toBe("fetching");
    expect(axios.get).toHaveBeenCalledTimes(4);
    expectUnavailable(retry, status);
    fireEvent.click(retry);
    fireEvent.click(retry);
    await tick();
    // Default cancelRefetch would restart a cached read on each click.
    expect(axios.get).toHaveBeenCalledTimes(4);
    expectUnavailable(retry, status);

    axios.get.mockResolvedValue(RESPONSE);
    await tick(1100);
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(screen.getByTestId("run-tray").textContent).toBe("1 run going");
    expect(useRunTray.getState().runs).toEqual([ENTRY]);
    expect(notify).not.toHaveBeenCalled();
  });

  it("clears the held failure with one neutral removal when a later poll returns 404", async () => {
    axios.get.mockRejectedValue({ statusCode: 503 });
    render(view());
    await tick(3100);
    const { retry, status } = focusRetry();
    expectUnavailable(retry, status);

    axios.get.mockRejectedValue({ statusCode: 404 });
    await tick(4000);
    expect(axios.get).toHaveBeenCalledTimes(4);
    expect(screen.queryByTestId("run-tray")).toBeNull();
    expect(useRunTray.getState().runs).toEqual([]);
    expect(notify).toHaveBeenCalledTimes(1);
    expect(notify).toHaveBeenCalledWith("Run #4A was removed", {
      key: `run-tray-removed-${ENTRY.experimentId}`,
      variant: "default",
    });
    await tick(8000);
    expect(axios.get).toHaveBeenCalledTimes(4);
  });
});
