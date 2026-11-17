/* eslint-disable react/prop-types */
// While rows are selected, the results selection bar stands where the View
// menu was, so the View menu's "Hide rows with no output" line is not in the
// toolbar row. When the row has no room for the hide-empty switch either,
// the bar's More carries that line: the same words, the same state and the
// same handler. While the switch is on screen, More does not carry it.
//
// The page below is the results toolbar row as RunResultsView wires it: the
// switch tells the page when its row leaves it out, and the page then gives
// the selection bar the View menu's line. jsdom has no layout, so each box's
// width is its data-w (the switch's hidden measuring twin, the full labelled
// switch, is 200 px), as in ho-hide-empty-switch.test.jsx.
import React, { useRef, useState } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import HideEmptySwitch from "src/sections/common/filters/HideEmptySwitch";
import {
  HIDE_EMPTY_LABEL,
  hideEmptyViewExtra,
  hidesEmpty,
  withHideEmpty,
} from "src/sections/common/filters/hideEmpty";
import SelectionBar from "../SelectionBar";
import useResultsUrlState from "../useResultsUrlState";

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));
// Icons as their names, so a menu row's check mark can be read.
vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-icon={icon} />,
}));
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
  runResultsKey: (id) => ["run-results", id],
  fetchResultIds: () => Promise.resolve([]),
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
// The URL state reads its other keys with this module: keep the rest real.
vi.mock(
  "src/sections/common/Analysis/analysisLinks",
  async (importOriginal) => ({
    ...(await importOriginal()),
    iterateHref: () => "",
  }),
);
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

/** What the grid reads: the page's pills, from the URL. */
function GridReads() {
  const url = useResultsUrlState();
  const { search } = useLocation();
  return (
    <output
      data-testid="grid-reads"
      data-hides-empty={String(hidesEmpty(url.pills))}
      data-filters={new URLSearchParams(search).get("filters") ?? ""}
    />
  );
}

/**
 * The results toolbar row with rows selected, wired as RunResultsView wires
 * it: the switch in its toolbar (the row is measured) and the selection bar,
 * which gets the View menu's line while the switch is left out.
 */
function Page({ row, toolbar }) {
  const ref = useRef(null);
  const url = useResultsUrlState();
  const [leftOut, setLeftOut] = useState(false);
  const setPills = (next) =>
    url.patch({ filters: next.length ? JSON.stringify(next) : null });
  return (
    <>
      <div data-w={row}>
        <div ref={ref} data-w={toolbar} data-toolbar="">
          <HideEmptySwitch
            datasetId="ds-1"
            view={{ runIds: ["r1", "r2"], pills: url.pills }}
            rowOf={() => ref.current?.parentElement ?? null}
            onLeftOut={setLeftOut}
          />
        </div>
      </div>
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
        pills={url.pills}
        onClear={() => {}}
        hideEmptyLine={leftOut ? hideEmptyViewExtra(url.pills, setPills) : null}
      />
      <GridReads />
    </>
  );
}

const mount = ({ row, toolbar, search = "" }) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={[`/${search}`]}>
        <Page row={row} toolbar={toolbar} />
      </MemoryRouter>
    </QueryClientProvider>,
  );

const gridReads = () => screen.getByTestId("grid-reads");
const theSwitch = () =>
  screen.queryByRole("checkbox", { name: HIDE_EMPTY_LABEL });
const openMore = async () => {
  // A menu that is still closing hides nothing once it has closed.
  await waitFor(() => expect(screen.queryByRole("menu")).toBeNull());
  fireEvent.click(screen.getByRole("button", { name: "More actions" }));
  return screen.findByRole("menu");
};
// The line is a menu checkbox: a screen reader hears its state, and that
// state matches the check mark a person sees.
const lineInMore = () =>
  screen.queryByRole("menuitemcheckbox", { name: HIDE_EMPTY_LABEL });
const checked = (item) => {
  const mark = Boolean(item.querySelector('[data-icon="mdi:check"]'));
  expect(item).toHaveAttribute("aria-checked", String(mark));
  return mark;
};
const hidePill = JSON.stringify(withHideEmpty([], true));

beforeEach(() => {
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(
    function rect() {
      const own = this.getAttribute?.("data-w");
      const twin =
        this.getAttribute?.("aria-hidden") === "true" &&
        this.parentElement?.hasAttribute("data-toolbar");
      const width = own != null ? Number(own) : twin ? 200 : 0;
      return {
        width,
        height: 20,
        left: 0,
        right: width,
        top: 0,
        bottom: 20,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      };
    },
  );
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe("Hide rows with no output while rows are selected", () => {
  it("is in More while the row has no room for the switch, and turns hiding on and off", async () => {
    mount({ row: 400, toolbar: 390 });
    expect(theSwitch()).toBeNull();
    expect(gridReads()).toHaveAttribute("data-hides-empty", "false");

    await openMore();
    const off = lineInMore();
    expect(off).not.toBeNull();
    expect(checked(off)).toBe(false);

    fireEvent.click(off);
    expect(gridReads()).toHaveAttribute("data-hides-empty", "true");
    expect(gridReads()).toHaveAttribute("data-filters", hidePill);

    // Still no room for the switch: More still carries the line, now on.
    expect(theSwitch()).toBeNull();
    await openMore();
    const on = lineInMore();
    expect(on).not.toBeNull();
    expect(checked(on)).toBe(true);

    fireEvent.click(on);
    expect(gridReads()).toHaveAttribute("data-hides-empty", "false");
    expect(gridReads()).toHaveAttribute("data-filters", "");
  });

  it("is not in More while the switch is on screen: one control, not two", async () => {
    mount({ row: 1000, toolbar: 300 });
    expect(
      await screen.findByRole("checkbox", { name: HIDE_EMPTY_LABEL }),
    ).toBeInTheDocument();

    await openMore();
    expect(lineInMore()).toBeNull();
    // The bar's own actions are where they were (its room here is 0 px).
    expect(
      screen.getByRole("menuitem", { name: /^Add to dataset/ }),
    ).toBeInTheDocument();
  });

  it("names itself with the switch's words and shows that hiding is on", async () => {
    mount({
      row: 400,
      toolbar: 390,
      search: `?filters=${encodeURIComponent(hidePill)}`,
    });
    expect(gridReads()).toHaveAttribute("data-hides-empty", "true");

    await openMore();
    const item = lineInMore();
    expect(item).not.toBeNull();
    expect(item).toHaveTextContent(HIDE_EMPTY_LABEL);
    expect(checked(item)).toBe(true);
  });
});
