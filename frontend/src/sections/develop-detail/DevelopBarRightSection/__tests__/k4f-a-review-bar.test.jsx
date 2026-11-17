// integration review fixes (as-of selection bar):
//   4. a row this version deleted that is back in the latest version (restored
//      since) is not offered for restore: Download (1) and Clear only;
//   6. Download (n) counts and exports rows that exist where they are read:
//      rows this version deleted are exported from its parent (where the grid
//      shows them from), never as an empty export of vN.
import React from "react";
import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { palette } from "src/theme/palette";
import { useDevelopSelectedRowsStore } from "../../states";

const asOfState = { current: null };
vi.mock("src/api/develop/dataset-versions", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, useAsOfVersion: () => asOfState.current };
});

const ROWS = {
  // In v5 › deleted, Row 31 was restored in v7; Row 77 is still deleted.
  r31: {
    row_id: "r31",
    order: 30,
    row_meta: {
      change: { kind: "deleted", version: 5, in_latest: true, restored_in: 7 },
    },
  },
  r77: {
    row_id: "r77",
    order: 76,
    row_meta: { change: { kind: "deleted", version: 5 } },
  },
  r1: { row_id: "r1", order: 0, row_meta: { change: null } },
};
vi.mock("../../Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({
    refreshGrid: () => {},
    gridApi: {
      current: {
        getRowNode: (id) => (ROWS[id] ? { data: ROWS[id] } : null),
        getGridOption: () => ({ totalRowCount: 2, filteredRowCount: 2 }),
        deselectAll: () => {},
      },
    },
  }),
}));

const theme = createTheme({ palette: palette("light") });
const V5 = { id: "V5", number: 5 };
const V7 = { id: "V7", number: 7 };

let Bar = null;
let asOfExport = null;
beforeAll(async () => {
  const mod = await import("../DevelopDataSelectionActive");
  Bar = mod.default;
  asOfExport = mod.asOfExport;
}, 180000);

async function renderBar(url) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={theme}>
        <MemoryRouter initialEntries={[url]}>
          <Routes>
            <Route path="/develop/:dataset" element={<Bar />} />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}
const buttonNames = (root) =>
  within(root)
    .getAllByRole("button")
    .map((b) => b.textContent.replace(/\s+/g, " ").trim());

describe("as of v5 › In this version › deleted", () => {
  afterEach(cleanup);
  beforeEach(() => {
    asOfState.current = {
      isAsOf: true,
      isLoading: false,
      version: V5,
      latest: V7,
      versions: [V7, V5],
    };
    useDevelopSelectedRowsStore.setState({
      selectAll: false,
      toggledNodes: [],
    });
  });

  it("a row restored since: Download (1) and Clear, no Restore", async () => {
    useDevelopSelectedRowsStore.setState({ toggledNodes: ["r31"] });
    await renderBar("/develop/GS?version=5");
    const bar = screen.getByTestId("selection-bar-as-of");
    expect(buttonNames(bar)).toEqual(["Download (1)", "Clear Esc"]);
  });

  it("a row still deleted: Download (1), Restore 1 deleted row…, Clear", async () => {
    useDevelopSelectedRowsStore.setState({ toggledNodes: ["r77"] });
    await renderBar("/develop/GS?version=5");
    const bar = screen.getByTestId("selection-bar-as-of");
    expect(buttonNames(bar)).toEqual([
      "Download (1)",
      "Restore 1 deleted row…",
      "Clear Esc",
    ]);
  });
});

describe("what Download (n) exports as of vN", () => {
  it("rows this version deleted are exported from its parent", () => {
    expect(asOfExport([ROWS.r31, ROWS.r77], 5)).toEqual({
      exportFrom: 4,
      exportRows: [ROWS.r31, ROWS.r77],
    });
  });

  it("rows in vN are exported from vN", () => {
    expect(asOfExport([ROWS.r1], 5)).toEqual({
      exportFrom: 5,
      exportRows: [ROWS.r1],
    });
  });

  it("a mixed selection exports only the rows in vN", () => {
    expect(asOfExport([ROWS.r1, ROWS.r77], 5)).toEqual({
      exportFrom: 5,
      exportRows: [ROWS.r1],
    });
  });
});
