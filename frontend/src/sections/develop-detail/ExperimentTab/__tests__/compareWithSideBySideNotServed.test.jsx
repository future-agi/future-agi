// A leaderboard row's Compare with… list (the split half of Side by side)
// where the server has no side-by-side read: every run stays in the list,
// turned off, with the shared line beside the heading as its description,
// and a pick opens nothing. Where the read is served, a pick opens the two
// runs side by side as before.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "src/utils/test-utils";
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";
import { setRealBackendForTests } from "src/sections/develop/realBackendCaps";
import CompareWithPicker from "../CompareWithPicker";

vi.mock("src/components/iconify", () => ({ default: () => null }));

const RUNS = [
  { run_id: "r1", label: "#1A", experiment_id: "e1" },
  { run_id: "r2", label: "#1B", experiment_id: "e1" },
  { run_id: "r3", label: "#2A", experiment_id: "e2" },
];

let client;
let onPick;
const mount = () =>
  render(
    <QueryClientProvider client={client}>
      <CompareWithPicker run={RUNS[0]} runs={RUNS} onPick={onPick} />
    </QueryClientProvider>,
  );
const openList = () =>
  fireEvent.click(
    screen.getByRole("button", { name: "Compare #1A with another run" }),
  );
const runItems = () =>
  screen
    .getAllByRole("menuitem")
    .filter((el) => /^#\d+[A-Z]+/.test(el.textContent ?? ""));

beforeEach(() => {
  onPick = vi.fn();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});
afterEach(() => {
  cleanup();
  client.clear();
  setRealBackendForTests(null);
});

describe("Compare with… where the server has no side-by-side read", () => {
  it("keeps every run in the list, turned off, with the shared line as its description", () => {
    setRealBackendForTests(true);
    mount();
    openList();

    const items = runItems();
    expect(items.map((el) => el.textContent?.split(" ")[0])).toEqual([
      "#1B",
      "#2A",
    ]);
    for (const item of items) {
      expect(item).toBeVisible();
      expect(item).toHaveAttribute("aria-disabled", "true");
      expect(item).toHaveAccessibleDescription(NOT_IN_RELEASE);
    }
    expect(screen.getByText(NOT_IN_RELEASE)).toBeVisible();
  });

  it("a pick opens nothing", () => {
    setRealBackendForTests(true);
    mount();
    openList();

    for (const item of runItems()) fireEvent.click(item);

    expect(onPick).not.toHaveBeenCalled();
  });
});

// Guard (green before and after): where the read is served, a pick opens the
// two runs side by side.
describe("Compare with… where the side-by-side read is served", () => {
  it("picks the run and shows no line", () => {
    setRealBackendForTests(false);
    mount();
    openList();

    const [first] = runItems();
    expect(first).not.toHaveAttribute("aria-disabled");
    fireEvent.click(first);

    expect(onPick).toHaveBeenCalledTimes(1);
    expect(onPick.mock.calls[0][0]).toMatchObject({ run_id: "r2" });
    expect(screen.queryByText(NOT_IN_RELEASE)).toBeNull();
  });
});
