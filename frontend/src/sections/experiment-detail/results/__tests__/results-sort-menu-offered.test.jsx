// The results toolbar's Sort list, by the read's published query contract.
// While the contract lists fixed choices, a launch build's read answers Row
// order and Regressed first only: those stay on, and every other order stays
// in the list, turned off with the shared line as its reason, and choosing it
// sets nothing. Once the contract takes the sort as a pattern, every order is
// on, and Most variable and Most flaky sort by the metric and run chosen
// beside the list. In a build that is not a launch build, every order is on.
import React from "react";
import * as zod from "zod";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "src/utils/test-utils";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";
import SortMenu from "../SortMenu";
import { REGRESSED_FIRST, SORT_ENTRIES } from "../sortModel";

const contract = vi.hoisted(() => ({ sort: null }));

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/generated/api-contracts/api.zod", async (importOriginal) => {
  const actual = await importOriginal();
  const real = actual.DatasetResultsReadQueryParams;
  return {
    ...actual,
    DatasetResultsReadQueryParams: {
      ...real,
      shape: {
        ...real.shape,
        get sort() {
          return contract.sort;
        },
      },
    },
  };
});

const FIXED = ["order", "-order", "input", "-input", "regressed"];
const fixedChoices = () => zod.enum(FIXED).default("order");
const pattern = () => zod.string().max(256).default("order");

const A = "run-a";
const B = "run-b";
const RUNS = [
  { run_id: A, label: "#1A" },
  { run_id: B, label: "#1B" },
];
const METRICS = [{ key: "eval:correctness", name: "Correctness" }];
const ANSWERED_FIXED = ["order", REGRESSED_FIRST];

const openList = (onChange) => {
  render(
    <SortMenu
      sort="order"
      onChange={onChange}
      metrics={METRICS}
      runs={RUNS}
      baselineId={A}
      hasTrials
    />,
  );
  fireEvent.click(screen.getByRole("button"));
  return SORT_ENTRIES.map((e) => ({
    entry: e,
    item: screen.getByRole("menuitem", { name: e.label }),
  }));
};

const expectOff = (items, onChange) => {
  for (const { entry, item } of items) {
    expect(item).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(item);
    // No order form opens beside the list.
    expect(screen.getAllByText(entry.label)).toHaveLength(1);
  }
  expect(onChange).not.toHaveBeenCalled();
};

beforeEach(() => {
  contract.sort = fixedChoices();
});
afterEach(() => {
  cleanup();
  setLaunchOverrideForTest(null);
});

describe("The toolbar's Sort list while the contract lists fixed choices", () => {
  it("keeps Row order and Regressed first on, and turns every other order off", () => {
    setLaunchOverrideForTest({ launch: true });
    const onChange = vi.fn();
    const items = openList(onChange);
    const off = items.filter(({ entry }) => !ANSWERED_FIXED.includes(entry.id));
    expect(off.length).toBe(SORT_ENTRIES.length - ANSWERED_FIXED.length);
    expectOff(off, onChange);
    for (const { entry, item } of items) {
      if (ANSWERED_FIXED.includes(entry.id))
        expect(item).not.toHaveAttribute("aria-disabled");
    }
  });

  it("names the shared line as a turned-off order's reason", async () => {
    setLaunchOverrideForTest({ launch: true });
    const items = openList(vi.fn());
    const { item } = items.find(
      ({ entry }) => !ANSWERED_FIXED.includes(entry.id),
    );
    fireEvent.mouseOver(item.parentElement);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      NOT_IN_RELEASE,
    );
  });
});

describe("The toolbar's Sort list once the contract takes a pattern", () => {
  it("keeps every order on", () => {
    setLaunchOverrideForTest({ launch: true });
    contract.sort = pattern();
    for (const { item } of openList(vi.fn()))
      expect(item).not.toHaveAttribute("aria-disabled");
  });

  it.each([
    ["variance", `variance:${METRICS[0].key}:${B}`],
    ["flakiness", `flakiness:${B}`],
  ])("sorts by %s on the chosen metric and run", (id, sort) => {
    setLaunchOverrideForTest({ launch: true });
    contract.sort = pattern();
    const onChange = vi.fn();
    const { item } = openList(onChange).find(({ entry }) => entry.id === id);
    fireEvent.click(item);
    // The form's Sort button (the toolbar's own button also reads Sort).
    fireEvent.click(screen.getAllByRole("button", { name: "Sort" }).at(-1));
    expect(onChange).toHaveBeenCalledWith(sort);
  });
});

describe("The toolbar's Sort list in a build that is not a launch build", () => {
  it.each([fixedChoices, pattern])("keeps every order on (%#)", (shape) => {
    contract.sort = shape();
    const items = openList(vi.fn());
    for (const { item } of items)
      expect(item).not.toHaveAttribute("aria-disabled");
  });
});
