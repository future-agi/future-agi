// The results toolbar's Worse, Better and Changed compare the other runs in
// view with the baseline. With no baseline to compare with (none, not set,
// or the baseline is the only run), each stays in the row turned off, with a
// plain line saying why as its accessible description, in the wide buttons
// and in the narrow menu alike, and a click asks for nothing. A show the
// page hands it then reads as All. With a baseline and another run they
// work as before. A count the read did not give is not shown, and the shows
// still work.
import React from "react";
import * as zod from "zod";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "src/utils/test-utils";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";
import ResultsToolbar from "../ResultsToolbar";
import { NO_BASELINE_LINE, ONLY_BASELINE_LINE } from "../showModel";

// The read's published show choices: the real ones unless a test names others.
const contract = vi.hoisted(() => ({ show: null }));

vi.mock("src/generated/api-contracts/api.zod", async (importOriginal) => {
  const actual = await importOriginal();
  const real = actual.DatasetResultsReadQueryParams;
  const realShow = real.shape.show;
  return {
    ...actual,
    DatasetResultsReadQueryParams: {
      ...real,
      shape: {
        ...real.shape,
        get show() {
          return contract.show ?? realShow;
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

// Run labels without digits, so any digit on a show is its count.
const RUNS = [
  { run_id: "run-a", label: "Base" },
  { run_id: "run-b", label: "Next" },
];
const NAMES = [
  /^Show rows worse than the baseline/,
  /^Show improved/,
  /^Show changed/,
];
const ITEMS = ["regressed", "improved", "changed"];
const COUNTS = { regressed: 2, improved: 1, changed: 3 };
const A_COUNT = /\(\d/;

function renderToolbar(props = {}) {
  const onShow = vi.fn();
  render(
    <ResultsToolbar
      search=""
      onSearch={() => {}}
      show="all"
      showCounts={COUNTS}
      onShow={onShow}
      multiRun
      filterView={{ datasetId: "d1", resultsView: {} }}
      onAddPill={() => {}}
      sort="order"
      onSort={() => {}}
      sortMetrics={[]}
      runs={RUNS}
      baselineId="run-a"
      {...props}
    />,
  );
  return onShow;
}

// The read's published contract lists Worse, Better and Changed, so in a
// launch build they are offered and only the baseline can turn them off.
beforeEach(() => setLaunchOverrideForTest({ launch: true }));
afterEach(() => {
  cleanup();
  contract.show = null;
  setLaunchOverrideForTest(null);
});

const NO_COMPARISON = [
  ["the baseline is none", { baselineId: "none" }, NO_BASELINE_LINE],
  ["no baseline is set", { baselineId: undefined }, NO_BASELINE_LINE],
  [
    "the baseline is the only run",
    { runs: [RUNS[0]], baselineId: "run-a" },
    ONLY_BASELINE_LINE,
  ],
  [
    "the baseline is the only run the page holds",
    { runIds: ["run-a"], baselineId: "run-a" },
    ONLY_BASELINE_LINE,
  ],
];

describe("Worse, Better and Changed with no baseline to compare with", () => {
  it.each(NO_COMPARISON)(
    "stay in the row turned off, saying why, when %s",
    (_, props, line) => {
      renderToolbar(props);
      for (const name of NAMES) {
        const button = screen.getByRole("button", { name });
        expect(button).toBeDisabled();
        expect(button).toHaveAccessibleDescription(line);
        expect(button).not.toHaveAccessibleDescription(NOT_IN_RELEASE);
        expect(button).not.toHaveAccessibleName(/\d/);
        expect(button).not.toHaveTextContent(/\d/);
      }
    },
  );

  it.each(NO_COMPARISON)(
    "ask for nothing when pressed, when %s",
    (_, props) => {
      const onShow = renderToolbar(props);
      for (const name of NAMES)
        fireEvent.click(screen.getByRole("button", { name }));
      expect(onShow).not.toHaveBeenCalled();
    },
  );

  it("keep All on and pressed, and read a Worse handed to them as All", () => {
    renderToolbar({ baselineId: "none", show: "regressed" });
    const all = screen.getByRole("button", { name: "Show all rows" });
    expect(all).toBeEnabled();
    expect(all).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: NAMES[0] })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("say the release line instead where this build's read does not answer them", () => {
    contract.show = zod
      .enum(["all", "errors", "failed", "flaky"])
      .default("all");
    renderToolbar({ baselineId: "none" });
    for (const name of NAMES)
      expect(screen.getByRole("button", { name })).toHaveAccessibleDescription(
        NOT_IN_RELEASE,
      );
  });

  it("say why outside a launch build too", () => {
    setLaunchOverrideForTest({ launch: false });
    renderToolbar({ baselineId: "none" });
    for (const name of NAMES) {
      const button = screen.getByRole("button", { name });
      expect(button).toBeDisabled();
      expect(button).toHaveAccessibleDescription(NO_BASELINE_LINE);
    }
  });
});

describe("Worse, Better and Changed in the narrow menu with no baseline", () => {
  it.each(NO_COMPARISON)(
    "stay in the menu turned off, saying why, when %s",
    (_, props, line) => {
      const onShow = renderToolbar({ ...props, compact: true });
      fireEvent.click(screen.getByTestId("results-show-menu"));
      for (const k of ITEMS) {
        const item = screen.getByTestId(`results-show-item-${k}`);
        expect(item).toHaveAttribute("aria-disabled", "true");
        expect(item).toHaveTextContent(line);
        expect(item).not.toHaveTextContent(NOT_IN_RELEASE);
        expect(item).not.toHaveTextContent(A_COUNT);
        fireEvent.click(item);
      }
      expect(onShow).not.toHaveBeenCalled();
    },
  );

  it("name All rows on the menu button for a Worse handed to it", () => {
    renderToolbar({ baselineId: "none", show: "regressed", compact: true });
    expect(screen.getByTestId("results-show-menu")).toHaveAccessibleName(
      "Show: All rows",
    );
    fireEvent.click(screen.getByTestId("results-show-menu"));
    expect(screen.getByTestId("results-show-item-all")).toHaveClass(
      "Mui-selected",
    );
  });
});

describe("Worse, Better and Changed with a baseline and another run", () => {
  it("work and show their counts on the buttons", () => {
    const onShow = renderToolbar();
    for (const name of NAMES) {
      const button = screen.getByRole("button", { name });
      expect(button).toBeEnabled();
      expect(button).not.toHaveAccessibleDescription(NO_BASELINE_LINE);
    }
    expect(screen.getByTestId("results-show-regressed")).toHaveTextContent(
      "(2)",
    );
    fireEvent.click(screen.getByRole("button", { name: NAMES[1] }));
    expect(onShow).toHaveBeenCalledWith("improved");
  });

  it("work and show their counts in the narrow menu", () => {
    const onShow = renderToolbar({ compact: true });
    fireEvent.click(screen.getByTestId("results-show-menu"));
    for (const k of ITEMS)
      expect(screen.getByTestId(`results-show-item-${k}`)).not.toHaveAttribute(
        "aria-disabled",
      );
    expect(screen.getByTestId("results-show-item-changed")).toHaveTextContent(
      "(3)",
    );
    fireEvent.click(screen.getByTestId("results-show-item-regressed"));
    expect(onShow).toHaveBeenCalledWith("regressed");
  });

  it("stay on while the page's runs are still loading", () => {
    renderToolbar({ runs: [], runIds: ["run-a", "run-b"] });
    for (const name of NAMES)
      expect(screen.getByRole("button", { name })).toBeEnabled();
  });
});

describe("A count the read did not give", () => {
  it.each([null, undefined, {}])(
    "is not shown on the buttons, and the shows still work (counts %s)",
    (showCounts) => {
      const onShow = renderToolbar({ showCounts });
      for (const name of NAMES) {
        const button = screen.getByRole("button", { name });
        expect(button).toBeEnabled();
        expect(button).not.toHaveAccessibleName(/\d/);
        expect(button).not.toHaveTextContent(/\d/);
      }
      fireEvent.click(screen.getByRole("button", { name: NAMES[0] }));
      expect(onShow).toHaveBeenCalledWith("regressed");
    },
  );

  it("is not shown in the narrow menu, and the shows still work", () => {
    const onShow = renderToolbar({
      showCounts: null,
      show: "regressed",
      compact: true,
    });
    const button = screen.getByTestId("results-show-menu");
    expect(button).toHaveAccessibleName("Show: Worse");
    fireEvent.click(button);
    for (const k of ITEMS) {
      const item = screen.getByTestId(`results-show-item-${k}`);
      expect(item).not.toHaveAttribute("aria-disabled");
      expect(item).not.toHaveTextContent(A_COUNT);
    }
    fireEvent.click(screen.getByTestId("results-show-item-changed"));
    expect(onShow).toHaveBeenCalledWith("changed");
  });

  it("leaves a zero the read gave on screen", () => {
    renderToolbar({ showCounts: { regressed: 0, improved: 0, changed: 0 } });
    expect(screen.getByTestId("results-show-regressed")).toHaveTextContent(
      "(0)",
    );
    expect(screen.getByRole("button", { name: NAMES[0] })).toHaveAccessibleName(
      /\b0\b/,
    );
  });
});
