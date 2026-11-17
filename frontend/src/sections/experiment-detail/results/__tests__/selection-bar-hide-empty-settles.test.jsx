// The results selection bar still settles on one layout while its More
// carries "Hide rows with no output" for a switch the row leaves out. In the
// results toolbar row the switch shows only when the row has room for it
// beside what the selection bar shows now; while it is left out, the page
// gives the bar the View menu's line and More carries it. The line comes and
// goes with the switch in the same commit. The ResizeObserver stand-in below
// gives the bar the row's room, less the switch while the switch is on
// screen; the switch answers from what the bar showed when it last looked.
// The bar must settle on a layout that fits and stay there. Where every
// action fits, More shows while the switch is left out and does not come and
// go.
import React from "react";
import { act, cleanup, render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { hideEmptyViewExtra } from "src/sections/common/filters/hideEmpty";

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

// The toolbar row: its room for the bar and whether the switch is on screen.
const row = { room: 682, switchShown: true };
const barWidth = () => row.room - (row.switchShown ? SWITCH : 0);

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

/** What the bar shows now: its actions (not the measuring row), and More. */
const layoutNow = () => {
  const shown = document.querySelector(
    '[data-action-bar="results-selection"]',
  ).firstElementChild;
  const ids = Array.from(shown.querySelectorAll("[data-action]"))
    .map((el) => el.getAttribute("data-action"))
    .filter((id, i, list) => list.indexOf(id) === i);
  const more = Boolean(shown.querySelector('[aria-label="More actions"]'));
  return { ids, more };
};
/** What a layout takes in the bar. */
const contentOf = ({ ids, more }) =>
  ids.reduce((sum, id) => sum + WIDTHS[id], 0) +
  GAP * Math.max(0, ids.length - 1) +
  (more ? (ids.length ? GAP : 0) + MORE : 0);
const key = ({ ids, more }) => `${ids.join(",")}${more ? " + More" : ""}`;

let now = 0;
const setWindow = (width) =>
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    writable: true,
    value: width,
  });
const windowBefore = window.innerWidth;

// The View menu's line (its words, state and handler), as the page gives it.
const line = hideEmptyViewExtra([], vi.fn());
const bar = () => (
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
      hideEmptyLine={row.switchShown ? null : line}
    />
  </MemoryRouter>
);

let rerender = () => {};
// What the switch saw when it last looked; how often the line was given.
let seen = { ids: ALL, more: false };
let lineGiven = 0;

/**
 * The switch answers from what it saw; the line follows it in the same
 * commit; the switch then sees the bar as that commit left it.
 */
const switchAnswers = () => {
  row.switchShown = contentOf(seen) <= row.room - SWITCH;
  if (!row.switchShown) lineGiven += 1;
  rerender(bar());
  seen = layoutNow();
};

const mount = ({ room, switchShown }) => {
  row.room = room;
  row.switchShown = switchShown;
  rerender = render(bar()).rerender;
  switchAnswers();
};

/** One observer callback: the bar reads its room, then the switch answers. */
const step = () => {
  now += 16;
  const given = barWidth();
  fire();
  const read = layoutNow();
  switchAnswers();
  return { read, given, answered: layoutNow(), room: barWidth() };
};

/** Every layout the bar shows over n callbacks (after each read and answer). */
const run = (n) =>
  Array.from({ length: n }, () => step()).flatMap((s) => [s.read, s.answered]);

/** Settles within six callbacks, stays for eight more, and always fits. */
const expectSettles = () => {
  const keys = run(16).map(key);
  const at = keys.findIndex((k, i) => keys.slice(i).every((x) => x === k));
  expect(at).toBeGreaterThanOrEqual(0);
  expect(at).toBeLessThanOrEqual(12);
  const last = keys[keys.length - 1];
  for (let i = 0; i < 8; i += 1) {
    const { read, given, answered, room } = step();
    expect(key(read)).toBe(last);
    expect(key(answered)).toBe(last);
    // Nothing is cut off: the layout fits the room the bar has.
    expect(contentOf(read)).toBeLessThanOrEqual(given);
    expect(contentOf(answered)).toBeLessThanOrEqual(room);
    // Nothing leaves the bar: what does not show is in More.
    expect(read.ids.length === ALL.length || read.more).toBe(true);
  }
};

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
  seen = { ids: ALL, more: false };
  lineGiven = 0;
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  setWindow(windowBefore);
});

describe("the results selection bar while More carries the hide-empty line", () => {
  // Guard: the room of the 1280 px window. Both layouts fold actions, so More
  // is on screen either way and the line changes nothing in the row.
  it("settles on one layout in a 682 px room while the line comes and goes", () => {
    mount({ room: 682, switchShown: true });
    expectSettles();
    expect(lineGiven).toBeGreaterThan(0);
    // It ends where both the bar and the switch fit.
    expect(row.switchShown).toBe(true);
    expect(contentOf(layoutNow())).toBeLessThanOrEqual(row.room - SWITCH);
  });

  // Guard: every action fits the room without the switch, not beside it.
  it("settles where every action fits only without the switch", () => {
    mount({ room: 1500, switchShown: true });
    expectSettles();
    expect(lineGiven).toBeGreaterThan(0);
    expect(row.switchShown).toBe(true);
    expect(contentOf(layoutNow())).toBeLessThanOrEqual(row.room - SWITCH);
  });

  it("shows More once the switch is left out where every action fits, and keeps it", () => {
    mount({ room: 1500, switchShown: false });
    const layouts = [layoutNow(), ...run(16)];
    // More carries the line from the first layout on and never leaves.
    layouts.forEach((l) => expect(l.more).toBe(true));
    expect(new Set(layouts.map(key)).size).toBe(1);
    expect(row.switchShown).toBe(false);
    expectSettles();
  });

  // Guard: More, shown for the line, makes the bar fold an action, and the
  // switch then has room.
  it("settles when More for the line leaves room for the switch", () => {
    mount({ room: 1450, switchShown: false });
    expectSettles();
  });
});
