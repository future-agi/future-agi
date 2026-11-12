/* eslint-disable react/prop-types */
// A launch build whose backend does not serve the connected machines path
// sends that path no request and starts no poll, even while unreleased parts
// are shown; the pill stays visible and reads "Not available in this release".
// A run's live activity, which the backend does not serve either, is held the
// same way.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  render,
  renderHook,
  screen,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { useRunActivity } from "src/api/develop/r4";
import {
  isSurfaceShown,
  setLaunchOverrideForTest,
} from "src/launch/launchSwitch";
import ConnectionPill from "../ConnectionPill";
import { useMachines } from "../useConnect";

// A launch build with the show-unreleased switch left as shipped (on).
const LAUNCH_SWITCH_ON = { launch: true, experimentsBasic: true, madeReal: [] };

let client;
let get;
const wrapper = ({ children }) => (
  <QueryClientProvider client={client}>
    <MemoryRouter>{children}</MemoryRouter>
  </QueryClientProvider>
);
const callsTo = (part) =>
  get.mock.calls.filter(([url]) => String(url).includes(part)).length;
const advance = (ms) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

beforeEach(() => {
  vi.useFakeTimers();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  get = vi
    .spyOn(axios, "get")
    .mockResolvedValue({ data: { result: { results: [] } } });
  setLaunchOverrideForTest(LAUNCH_SWITCH_ON);
});

afterEach(() => {
  cleanup();
  client.clear();
  get.mockRestore();
  setLaunchOverrideForTest(null);
  vi.useRealTimers();
});

describe("a path the build does not serve is never polled", () => {
  it("connected machines: no request after mount or after 15 s, and the pill says Not available in this release", async () => {
    // The switch shows every registered part, this one included.
    expect(isSurfaceShown("runners.connect")).toBe(true);
    render(<ConnectionPill machineId="m-1" agentId="a-1" />, { wrapper });
    await advance(0);
    expect(callsTo("/r4/connect/machines/")).toBe(0);
    await advance(15_000);
    expect(callsTo("/r4/connect/machines/")).toBe(0);
    expect(screen.getByText("Not available in this release")).toBeTruthy();
    expect(screen.queryByText("Not connected")).toBeNull();
  });

  it("the machines hook reports the path as held and fetches nothing", async () => {
    const { result } = renderHook(() => useMachines(), { wrapper });
    await advance(15_000);
    expect(result.current.held).toBe(true);
    expect(result.current.machines).toEqual([]);
    expect(callsTo("/r4/connect/machines/")).toBe(0);
  });

  it("a run's live activity: no request and no poll after 15 s", async () => {
    const { result } = renderHook(
      () => useRunActivity("run-1", { poll: 1000 }),
      { wrapper },
    );
    await advance(15_000);
    expect(callsTo("/r4/runs/run-1/activity/")).toBe(0);
    expect(result.current.held).toBe(true);
  });
});
