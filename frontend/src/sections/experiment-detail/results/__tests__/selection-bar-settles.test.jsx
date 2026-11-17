// The results selection bar settles on one layout. In the results toolbar
// row the "Hide rows with no output" switch shows only when the row has
// room for it beside what the selection bar shows now, and the bar fits its
// actions to the room the switch leaves; each answers the other a frame
// later. The ResizeObserver stand-in below gives the bar that room: the
// row's room, less the switch while the switch is on screen, where the
// switch decides from what the bar showed one step before. The bar must
// settle on a layout that fits, stay there, and still unfold when the
// window gets wider. A room that changes with the window (or slowly, with
// a neighbour) lays out as before at each width.
import React from "react";
import { act, cleanup, render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock(
  "src/sections/common/CommandPalette/commandRegistry",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useRegisterCommands: () => {},
  }),
);
vi.mock("src/sections/common/Collab/AssignDialog", () => ({
  default: () => null,
}));
vi.mock("src/sections/develop-detail/Review/StartReviewDialog", () => ({
  default: () => null,
}));
vi.mock("../AddResultsToDatasetDialog", () => ({ default: () => null }));
vi.mock("src/sections/common/export/ExportDialog", () => ({
  ResultsExportDialog: () => null,
}));
vi.mock("src/sections/common/Access/useDatasetAccess", () => ({
  default: () => ({
    can: { edit: true, download: true },
    reason: () => null,
    isLoading: false,
  }),
}));
vi.mock("src/api/develop/run-results", () => ({
  useRerunResultCells: () => ({ mutate: vi.fn() }),
}));
vi.mock("src/sections/common/Runs/useRescore", () => ({
  useRescore: () => ({ request: vi.fn(), dialog: null }),
}));
vi.mock("src/sections/common/Runs/rescoreCopyModel", () => ({
  scoreWithChoices: () => [],
}));
vi.mock("src/sections/common/Runs/runKind", () => ({
  canRerunHere: () => true,
}));
vi.mock("../../iterate/iterateModel", () => ({ canIterate: () => true }));
vi.mock("src/sections/common/Analysis/analysisLinks", () => ({
  iterateHref: () => "",
}));
vi.mock("src/sections/common/RunSources/rerunBus", () => ({
  openRerunWithChanges: vi.fn(),
}));
vi.mock("src/sections/common/Selection/askFalconAboutRows", () => ({
  askFalconAboutRows: vi.fn(),
}));
vi.mock("src/sections/common/Selection/useSelectAllMatching", () => ({
  useSelectAllMatching: ({ selectedIds }) => ({
    ids: selectedIds,
    text: `${selectedIds.length} selected`,
  }),
}));
vi.mock("src/sections/common/Selection/SelectionCount", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/export/exportSelection", () => ({
  resultsSelectionView: () => ({}),
}));

const { default: SelectionBar } = await import("../SelectionBar");

// The selection bar's actions, in their order, with natural widths (px).
const WIDTHS = {
  addToDataset: 120,
  sendToQa: 100,
  assign: 95,
  comment: 95,
  reviewPairwise: 125,
  rerun: 85,
  testFix: 170,
  rerunWithChanges: 160,
  downloadSelection: 150,
  askFalcon: 240,
  clear: 32,
};
const ALL = Object.keys(WIDTHS);
const ICON = 32;
const MORE = 72;
const GAP = 4;
// The switch's width with its gap.
const SWITCH = 185;

/** What a set of shown actions takes in the bar (with More when needed). */
const contentOf = (ids) =>
  ids.reduce((sum, id) => sum + WIDTHS[id], 0) +
  GAP * Math.max(0, ids.length - 1) +
  (ids.length < ALL.length ? GAP + MORE : 0);

// The toolbar row: its room for the bar, whether the switch is on screen,
// and the window's width. The bar's box is the room less the switch.
const row = { room: 682, switchShown: true, room0: 682 };
const barWidth = () => row.room - (row.switchShown ? SWITCH : 0);
let seen = ALL;
// The switch answers from what the bar showed one step before.
const switchAnswers = (shown) => {
  row.switchShown = contentOf(seen) <= row.room - SWITCH;
  seen = shown;
};

const observers = new Set();
class ResizeObserverStandIn {
  constructor(callback) {
    this.callback = callback;
    observers.add(this);
  }

  observe() {}

  unobserve() {}

  disconnect() {
    observers.delete(this);
  }
}
const fire = () =>
  act(() => {
    [...observers].forEach((o) => o.callback([]));
  });

const widthOf = (el) => {
  if (el.hasAttribute?.("data-action-bar")) return barWidth();
  const id = el.getAttribute?.("data-measure-id");
  if (id)
    return el.getAttribute("data-measure-kind") === "icon"
      ? ICON
      : WIDTHS[id] ?? 0;
  if (el.hasAttribute?.("data-measure-more")) return MORE;
  return 0;
};

/** The ids of the actions the bar shows now (not its hidden measuring row). */
const shownNow = () => {
  const bar = document.querySelector('[data-action-bar="results-selection"]');
  return Array.from(bar.firstElementChild.querySelectorAll("[data-action]"))
    .map((el) => el.getAttribute("data-action"))
    .filter((id, i, list) => list.indexOf(id) === i);
};

let now = 0;
const setWindow = (width) =>
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    writable: true,
    value: width,
  });
const windowBefore = window.innerWidth;

const mount = () => {
  render(
    <MemoryRouter>
      <SelectionBar
        datasetId="ds-1"
        selectedRows={[{ example_id: "ex-1" }, { example_id: "ex-2" }]}
        runs={[
          { run_id: "r1", label: "#1A" },
          { run_id: "r2", label: "#2A" },
        ]}
        baselineId="r1"
        focusedRunId="r2"
        readOnly={false}
        onClear={() => {}}
        onComment={() => {}}
      />
    </MemoryRouter>,
  );
  const shown = shownNow();
  switchAnswers(shown);
  return shown;
};

/** One observer callback: the bar reads its room, then the switch answers. */
const step = () => {
  now += 16;
  const given = barWidth();
  fire();
  const shown = shownNow();
  switchAnswers(shown);
  return { shown, given };
};

const key = (ids) => ids.join(",");

beforeEach(() => {
  observers.clear();
  now = 1_000_000;
  vi.spyOn(Date, "now").mockImplementation(() => now);
  vi.stubGlobal("ResizeObserver", ResizeObserverStandIn);
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(
    function rect() {
      const width = widthOf(this);
      return {
        width,
        height: 32,
        top: 0,
        left: 0,
        right: width,
        bottom: 32,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      };
    },
  );
  setWindow(1280);
  row.room = row.room0;
  row.switchShown = true;
  seen = ALL;
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  setWindow(windowBefore);
});

describe("the results selection bar beside the hide-empty switch", () => {
  it("settles on one layout within a few callbacks and stays there", () => {
    mount();
    const layouts = Array.from({ length: 16 }, () => key(step().shown));

    const last = layouts[layouts.length - 1];
    const settledAt = layouts.findIndex((l, i) =>
      layouts.slice(i).every((x) => x === l),
    );
    expect(settledAt).toBeGreaterThanOrEqual(0);
    expect(settledAt).toBeLessThanOrEqual(6);
    for (let i = 0; i < 8; i += 1) expect(key(step().shown)).toBe(last);
  });

  it("the settled layout fits the room it is given: nothing is cut off", () => {
    mount();
    for (let i = 0; i < 10; i += 1) step();
    for (let i = 0; i < 6; i += 1) {
      const { shown } = step();
      // The room the bar has while this layout is on screen (the switch
      // has answered by then).
      expect(contentOf(shown)).toBeLessThanOrEqual(barWidth());
    }
    // The bar ends where both it and the switch fit.
    expect(contentOf(shownNow())).toBeLessThanOrEqual(row.room - SWITCH);
    // Nothing leaves the bar: what does not show is in More.
    expect(
      document.querySelector('[aria-label="More actions"]'),
    ).toBeInTheDocument();
  });

  // Guard (green before and after): a much wider window unfolds again.
  it("still unfolds the actions when the window gets much wider", () => {
    mount();
    for (let i = 0; i < 10; i += 1) step();
    const narrow = shownNow();

    setWindow(1680);
    row.room = 1400;
    for (let i = 0; i < 5; i += 1) step();
    const { shown: wide, given } = step();

    expect(wide.length).toBeGreaterThan(narrow.length);
    for (const id of ["addToDataset", "sendToQa", "assign", "rerun", "testFix"])
      expect(wide).toContain(id);
    expect(contentOf(wide)).toBeLessThanOrEqual(given);
  });
});

// Guards (green before and after): where the bar's room does not depend on
// what it shows, each width lays out as it does today.
describe("a bar whose room changes on its own", () => {
  const sequence = [682, 497, 682, 497, 682];
  const expected = {
    682: "addToDataset,sendToQa,assign,reviewPairwise,rerun,clear",
    497: "addToDataset,sendToQa,reviewPairwise,clear",
  };
  const room = (width) => {
    row.room = width;
    row.switchShown = false;
  };

  it("returns to each width's layout when the window is resized", () => {
    room(sequence[0]);
    mount();
    row.switchShown = false;
    for (const width of sequence) {
      setWindow(width === 682 ? 1280 : 1100);
      room(width);
      now += 16;
      fire();
      expect(key(shownNow())).toBe(expected[width]);
    }
  });

  it("returns to each width's layout when a neighbour changes the room", () => {
    room(sequence[0]);
    mount();
    row.switchShown = false;
    for (const width of sequence) {
      room(width);
      // A person's change: seconds apart, the window unchanged.
      now += 5_000;
      fire();
      expect(key(shownNow())).toBe(expected[width]);
    }
  });
});
