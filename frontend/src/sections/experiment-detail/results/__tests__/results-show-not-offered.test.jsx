// The results toolbar's Worse, Better and Changed where this build's results
// read does not answer them: each stays in the row, turned off, with the
// shared line as its accessible description (and in its tooltip), and a
// click asks for nothing. A turned-off show carries no count, on screen or in
// its name. The narrow toolbar's menu keeps the three items, turned off with
// the line. All stays on. Where the read's published query contract lists
// them, and in a build that is not a launch build, the three work and show
// their counts as before.
import React from "react";
import * as zod from "zod";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "src/utils/test-utils";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";
import ResultsToolbar from "../ResultsToolbar";

const contract = vi.hoisted(() => ({ show: null }));

vi.mock("src/generated/api-contracts/api.zod", async (importOriginal) => {
  const actual = await importOriginal();
  const real = actual.DatasetResultsReadQueryParams;
  return {
    ...actual,
    DatasetResultsReadQueryParams: {
      ...real,
      shape: {
        ...real.shape,
        get show() {
          return contract.show;
        },
      },
    },
  };
});

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/FormSearchField/FormSearchField", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/ux/useShortcuts", () => ({ default: () => {} }));
vi.mock("../FilterMenu", () => ({ default: () => null }));
vi.mock("../SortMenu", () => ({ default: () => null }));
vi.mock("src/sections/common/filters/HideEmptySwitch", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/CustomColumns/CustomColumnHost", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/CustomColumns/CustomSortChip", () => ({
  default: () => null,
}));

const RUNS = [
  { run_id: "run-a", label: "#1A" },
  { run_id: "run-b", label: "#1B" },
];
const NAMES = [
  /^Show rows worse than the baseline/,
  /^Show improved/,
  /^Show changed/,
];
const ITEMS = ["regressed", "improved", "changed"];
// The read's show choices without and with Worse, Better and Changed.
const ROW_SETS = ["all", "errors", "failed", "flaky"];
const withoutMovement = () => zod.enum(ROW_SETS).default("all");
const withMovement = () => zod.enum([...ROW_SETS, ...ITEMS]).default("all");

function renderToolbar(props = {}) {
  const onShow = vi.fn();
  render(
    <ResultsToolbar
      search=""
      onSearch={() => {}}
      show="all"
      showCounts={{ regressed: 2, improved: 1, changed: 3 }}
      onShow={onShow}
      multiRun
      filterView={{ datasetId: "d1", resultsView: {} }}
      onAddPill={() => {}}
      sort="regressed"
      onSort={() => {}}
      sortMetrics={[]}
      runs={RUNS}
      baselineId="run-a"
      {...props}
    />,
  );
  return onShow;
}

beforeEach(() => {
  contract.show = withoutMovement();
});
afterEach(() => {
  cleanup();
  setLaunchOverrideForTest(null);
});

describe("Worse, Better and Changed where this build's read does not answer them", () => {
  it("stay in the row, turned off with the shared line", () => {
    setLaunchOverrideForTest({ launch: true });
    renderToolbar();
    for (const name of NAMES) {
      const button = screen.getByRole("button", { name });
      expect(button).toBeDisabled();
      expect(button).toHaveAccessibleDescription(NOT_IN_RELEASE);
    }
  });

  it("ask for nothing when pressed", () => {
    setLaunchOverrideForTest({ launch: true });
    const onShow = renderToolbar();
    for (const name of NAMES)
      fireEvent.click(screen.getByRole("button", { name }));
    expect(onShow).not.toHaveBeenCalled();
  });

  it("keep All on and offered", () => {
    setLaunchOverrideForTest({ launch: true });
    renderToolbar();
    const all = screen.getByRole("button", { name: "Show all rows" });
    expect(all).toBeEnabled();
    expect(all).toHaveAttribute("aria-pressed", "true");
  });

  it("carry no count, on screen or in their names", () => {
    setLaunchOverrideForTest({ launch: true });
    renderToolbar();
    for (const name of NAMES) {
      const button = screen.getByRole("button", { name });
      expect(button).not.toHaveAccessibleName(/\d/);
      expect(button).not.toHaveTextContent(/\d/);
    }
  });

  it("carry no count in the narrow toolbar's menu", () => {
    setLaunchOverrideForTest({ launch: true });
    renderToolbar({ compact: true });
    fireEvent.click(screen.getByTestId("results-show-menu"));
    for (const k of ITEMS)
      expect(
        screen.getByTestId(`results-show-item-${k}`),
      ).not.toHaveTextContent(/\d/);
  });

  it("stay in the narrow toolbar's menu, turned off with the shared line", () => {
    setLaunchOverrideForTest({ launch: true });
    const onShow = renderToolbar({ compact: true });
    fireEvent.click(screen.getByTestId("results-show-menu"));
    for (const k of ITEMS) {
      const item = screen.getByTestId(`results-show-item-${k}`);
      expect(item).toHaveAttribute("aria-disabled", "true");
      expect(item).toHaveTextContent(NOT_IN_RELEASE);
      fireEvent.click(item);
    }
    expect(onShow).not.toHaveBeenCalled();
  });
});

describe("Worse, Better and Changed where the read answers every show", () => {
  it("work as before", () => {
    const onShow = renderToolbar();
    for (const name of NAMES)
      expect(screen.getByRole("button", { name })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: NAMES[0] }));
    expect(onShow).toHaveBeenCalledWith("regressed");
  });

  it("show their counts", () => {
    renderToolbar();
    expect(screen.getByRole("button", { name: NAMES[0] })).toHaveAccessibleName(
      /\b2\b/,
    );
    expect(screen.getByTestId("results-show-improved")).toHaveTextContent(
      "(1)",
    );
    expect(screen.getByTestId("results-show-changed")).toHaveTextContent("(3)");
  });
});

describe("Worse, Better and Changed where the read's contract lists them", () => {
  beforeEach(() => {
    setLaunchOverrideForTest({ launch: true });
    contract.show = withMovement();
  });

  it("work and show their counts", () => {
    const onShow = renderToolbar();
    for (const name of NAMES) {
      const button = screen.getByRole("button", { name });
      expect(button).toBeEnabled();
      expect(button).not.toHaveAccessibleDescription(NOT_IN_RELEASE);
    }
    expect(screen.getByRole("button", { name: NAMES[0] })).toHaveAccessibleName(
      /\b2\b/,
    );
    expect(screen.getByTestId("results-show-improved")).toHaveTextContent(
      "(1)",
    );
    expect(screen.getByTestId("results-show-changed")).toHaveTextContent("(3)");
    fireEvent.click(screen.getByRole("button", { name: NAMES[2] }));
    expect(onShow).toHaveBeenCalledWith("changed");
  });

  it("say which rows Worse shows when no eval leads it", async () => {
    renderToolbar();
    fireEvent.mouseOver(screen.getByTestId("results-show-regressed"));
    const tip = await screen.findByRole("tooltip");
    expect(tip).toHaveTextContent(
      "Worse: rows where any eval got worse for any run vs #1A",
    );
    expect(tip).not.toHaveTextContent("failed both times");
  });

  it("work in the narrow toolbar's menu", () => {
    const onShow = renderToolbar({ compact: true });
    fireEvent.click(screen.getByTestId("results-show-menu"));
    for (const k of ITEMS) {
      const item = screen.getByTestId(`results-show-item-${k}`);
      expect(item).not.toHaveAttribute("aria-disabled");
      expect(item).not.toHaveTextContent(NOT_IN_RELEASE);
    }
    expect(screen.getByTestId("results-show-item-improved")).toHaveTextContent(
      "(1)",
    );
    fireEvent.click(screen.getByTestId("results-show-item-regressed"));
    expect(onShow).toHaveBeenCalledWith("regressed");
  });
});
