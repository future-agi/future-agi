// A leaderboard row's Compare with… menu offers "A run on another dataset…"
// while the tab's host is mounted. Where this build does not offer the
// compare-by-key route, the item stays in the menu with its label, turned
// off, with the shared line beside it and as its description; a click opens
// no dialog and sends nothing. Not a launch build keeps it working.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "src/utils/test-utils";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import MatchDoorHost from "src/sections/common/Leaderboards/MatchDoorHost";
import { closeMatchDoor } from "src/sections/common/Leaderboards/matchDoor";
import CompareWithPicker from "../CompareWithPicker";

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
const ITEM = "A run on another dataset…";

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
// The row's menu with the tab's host, as the leaderboard mounts them.
const mount = () =>
  render(
    <QueryClientProvider client={client}>
      <CompareWithPicker run={RUNS[0]} runs={RUNS} onPick={vi.fn()} />
      <MatchDoorHost />
    </QueryClientProvider>,
  );
const openMenu = () =>
  fireEvent.click(
    screen.getByRole("button", { name: "Compare #1A with another run" }),
  );

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

describe("leaderboard Compare with… · a run on another dataset, where the route is not offered", () => {
  it("keeps the item in the menu with its label, turned off, the line beside it and as its description", () => {
    launch();
    mount();
    openMenu();

    const item = screen.getByRole("menuitem", { name: ITEM });
    expect(item).toBeVisible();
    expect(item).toHaveAttribute("aria-disabled", "true");
    expect(item).toHaveAccessibleDescription(LINE);
    expect(within(item).getByText(LINE)).toBeVisible();
  });

  it("a click on the item opens no dialog and sends nothing", async () => {
    launch();
    mount();
    openMenu();

    fireEvent.click(screen.getByRole("menuitem", { name: ITEM }));
    await settle();

    expect(screen.queryByText("Compare rows by key")).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
    nothingSent();
  });
});

// Guard (green before and after): not a launch build keeps the item working.
describe("leaderboard Compare with… · a run on another dataset, not a launch build", () => {
  it("opens the tab's match dialog and asks for its read", async () => {
    prototype();
    mount();
    openMenu();

    const item = screen.getByRole("menuitem", { name: ITEM });
    expect(item).not.toHaveAttribute("aria-disabled");
    fireEvent.click(item);
    await settle();

    expect(screen.getByRole("dialog")).toHaveTextContent("Compare rows by key");
    expect(h.get.mock.calls.map(([path]) => path)).toContain(MATCH_PATH);
    expect(screen.queryByText(LINE)).toBeNull();
  });
});
