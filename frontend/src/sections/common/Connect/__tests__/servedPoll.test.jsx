/* eslint-disable react/prop-types */
// When the build does serve the connected machines path (a lane backs it), the
// pill polls it every 5 s as before and shows the machine, not the held words.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import ConnectionPill from "../ConnectionPill";

vi.mock("src/launch/madeReal", async (importOriginal) => {
  const real = await importOriginal();
  const MADE_REAL_PATHS = Object.freeze({
    ...real.MADE_REAL_PATHS,
    SRC: Object.freeze(["/model-hub/r4/connect/machines/"]),
  });
  return {
    ...real,
    MADE_REAL_PATHS,
    madeRealPathTemplates: (slots = MADE_REAL_PATHS) =>
      real.madeRealPathTemplates(slots),
  };
});

const LAUNCH_SWITCH_ON = { launch: true, experimentsBasic: true, madeReal: [] };
const MACHINE = {
  id: "m-1",
  name: "alice@mbp-14",
  status: "online",
  may_use: true,
  you: false,
  owner: { name: "Alice Park" },
  agents: [{ id: "a-1" }],
};

let client;
let get;
const wrapper = ({ children }) => (
  <QueryClientProvider client={client}>
    <MemoryRouter>{children}</MemoryRouter>
  </QueryClientProvider>
);
const machineCalls = () =>
  get.mock.calls.filter(([url]) =>
    String(url).includes("/r4/connect/machines/"),
  ).length;
const advance = (ms) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

beforeEach(() => {
  vi.useFakeTimers();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  get = vi.spyOn(axios, "get").mockResolvedValue({
    data: {
      result: {
        results: [{ ...MACHINE, last_seen_at: new Date().toISOString() }],
      },
    },
  });
  setLaunchOverrideForTest(LAUNCH_SWITCH_ON);
});

afterEach(() => {
  cleanup();
  client.clear();
  get.mockRestore();
  setLaunchOverrideForTest(null);
  vi.useRealTimers();
});

describe("a served machines path polls as before", () => {
  it("fetches on mount, polls every 5 s and shows the machine", async () => {
    render(<ConnectionPill machineId="m-1" agentId="a-1" />, { wrapper });
    await advance(0);
    expect(machineCalls()).toBe(1);
    await advance(15_000);
    expect(machineCalls()).toBeGreaterThanOrEqual(3);
    expect(screen.getByText("alice@mbp-14")).toBeTruthy();
    expect(screen.queryByText("Not available in this release")).toBeNull();
  });
});
