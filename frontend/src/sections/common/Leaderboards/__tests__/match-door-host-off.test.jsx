// The leaderboard tab's host holds the compare-by-key dialog for its rows'
// Compare with… menus. Where this build does not offer the compare-by-key
// route, opening the door shows no dialog and sends nothing, and the host
// still counts as mounted, so a row's item stays on screen (turned off).
// Not a launch build keeps the dialog and its read.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, renderHook, screen } from "src/utils/test-utils";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import MatchDoorHost from "../MatchDoorHost";
import { closeMatchDoor, openMatchDoor, useMatchDoorReady } from "../matchDoor";

const h = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), post: vi.fn() }));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: { ...actual.default, get: h.get, put: h.put, post: h.post },
  };
});
vi.mock("src/components/iconify", () => ({ default: () => null }));

const LINE = "Not available in this release";
const MATCH_PATH = "/model-hub/results/match/";
const RUNS = [
  { run_id: "r1", label: "#1A", name: "Reply v1", experiment_id: "e1" },
  { run_id: "r2", label: "#2A", name: "Reply v2", experiment_id: "e2" },
];

// A launch build as shipped (every unreleased part shown): the route, not
// the switch, decides, so compare by key is still not offered.
const launch = () => setLaunchOverrideForTest({ launch: true });
const prototype = () => setLaunchOverrideForTest(null);

let client;
const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
const nothingSent = () => {
  expect(h.get).not.toHaveBeenCalled();
  expect(h.put).not.toHaveBeenCalled();
  expect(h.post).not.toHaveBeenCalled();
};
const mountHost = () =>
  render(
    <QueryClientProvider client={client}>
      <MatchDoorHost />
    </QueryClientProvider>,
  );
const openDoor = () =>
  act(() => {
    openMatchDoor({ runs: RUNS, defaultRunId: "r1" });
  });

beforeEach(() => {
  for (const fn of [h.get, h.put, h.post])
    fn.mockReset().mockImplementation(() => new Promise(() => {}));
  client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
});
afterEach(() => {
  cleanup();
  closeMatchDoor();
  client.clear();
  setLaunchOverrideForTest(null);
});

describe("the leaderboard's compare-by-key host, where the route is not offered", () => {
  it("opening the door shows no dialog and sends nothing", async () => {
    launch();
    mountHost();

    openDoor();
    await settle();

    expect(screen.queryByText("Compare rows by key")).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
    nothingSent();
  });

  // Guard (green before and after): the row's item shows only while a host
  // is mounted, so the host stays mounted and counted.
  it("still counts as mounted, so a row's item stays on screen", () => {
    launch();
    mountHost();

    const { result } = renderHook(() => useMatchDoorReady());
    expect(result.current).toBe(true);
  });
});

// Guard (green before and after): not a launch build keeps the dialog.
describe("the leaderboard's compare-by-key host, not a launch build", () => {
  it("opening the door shows the match dialog and asks for its read", async () => {
    prototype();
    mountHost();

    openDoor();
    await settle();

    expect(screen.getByRole("dialog")).toHaveTextContent("Compare rows by key");
    expect(h.get.mock.calls.map(([path]) => path)).toContain(MATCH_PATH);
    expect(screen.queryByText(LINE)).toBeNull();
  });
});
