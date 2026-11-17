// integration (DV-06): as of an older version the selection bar is read-only
// (count, Download (n), Restore n deleted rows…, Clear); on the latest
// version the write actions are back. Plus the pure restore words.
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
import {
  deltaParts,
  deltaTooltip,
  restorableRows,
  restoreAsNewRowsText,
  restoreRowsText,
  restoredRowsText,
  rowNumbersText,
} from "../../DataTab/Versions/versionText";
import {
  rowAccessGate,
  rowViewGate,
} from "../../DataTab/DatapointDrawerV2/rowAccessGate";
import { deletedRowText } from "../../DataTab/DatapointDrawerV2/RowViewStates";

const asOfState = { current: null };
vi.mock("src/api/develop/dataset-versions", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, useAsOfVersion: () => asOfState.current };
});

const ROWS = {
  r31: {
    row_id: "r31",
    order: 30,
    row_meta: { change: { kind: "deleted_later", version: 5 } },
  },
  r1: { row_id: "r1", order: 0, row_meta: { change: null } },
};
vi.mock("../../Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({
    refreshGrid: () => {},
    gridApi: {
      current: {
        getRowNode: (id) => (ROWS[id] ? { data: ROWS[id] } : null),
        getGridOption: () => ({ totalRowCount: 240, filteredRowCount: 240 }),
        deselectAll: () => {},
      },
    },
  }),
}));

const theme = createTheme({ palette: palette("light") });
const V4 = { id: "V4", number: 4 };
const V6 = { id: "V6", number: 6 };

// The bar's module graph is large: import it once, before the tests (a
// cold import under a loaded runner can pass the per-test timeout).
let Bar = null;
beforeAll(async () => {
  Bar = (await import("../DevelopDataSelectionActive")).default;
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

describe("selection bar as of an older version", () => {
  afterEach(cleanup);
  beforeEach(() => {
    useDevelopSelectedRowsStore.setState({
      selectAll: false,
      toggledNodes: [],
    });
  });

  it("a deleted row: Download (1), Restore 1 deleted row…, Clear only", async () => {
    asOfState.current = {
      isAsOf: true,
      isLoading: false,
      version: V4,
      latest: V6,
      versions: [V6, V4],
    };
    useDevelopSelectedRowsStore.setState({ toggledNodes: ["r31"] });
    await renderBar("/develop/GS?version=4");
    const bar = screen.getByTestId("selection-bar-as-of");
    expect(buttonNames(bar)).toEqual([
      "Download (1)",
      "Restore 1 deleted row…",
      "Clear Esc",
    ]);
  });

  it("a live row: Download (1) and Clear only", async () => {
    asOfState.current = {
      isAsOf: true,
      isLoading: false,
      version: V4,
      latest: V6,
      versions: [V6, V4],
    };
    useDevelopSelectedRowsStore.setState({ toggledNodes: ["r1"] });
    await renderBar("/develop/GS?version=4");
    const bar = screen.getByTestId("selection-bar-as-of");
    expect(buttonNames(bar)).toEqual(["Download (1)", "Clear Esc"]);
    for (const gone of [
      /Duplicate/,
      /Delete/,
      /Assign split/,
      /Add to queue/,
      /New experiment/,
      /Generate more/,
      /Status/,
      /^Run$/,
    ])
      expect(screen.queryByRole("button", { name: gone })).toBeNull();
  });

  it("latest: the write actions are there (and no restore)", async () => {
    asOfState.current = {
      isAsOf: false,
      isLoading: false,
      version: V6,
      latest: V6,
      versions: [V6, V4],
    };
    useDevelopSelectedRowsStore.setState({ toggledNodes: ["r1"] });
    await renderBar("/develop/GS");
    expect(screen.queryByTestId("selection-bar-as-of")).toBeNull();
    expect(screen.getByRole("button", { name: /Assign split/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /New experiment/ })).toBeTruthy();
    expect(screen.queryByTestId("selection-restore-rows")).toBeNull();
  });
});

describe("restore words and gates (pure)", () => {
  it("restorableRows: deleted later → from the viewed version", () => {
    expect(restorableRows(Object.values(ROWS), 4)).toEqual({
      rows: [{ row_id: "r31", n: 31 }],
      from: 4,
    });
    expect(restorableRows([ROWS.r1], 4)).toBeNull();
    // compare=in: rows this version deleted come back from its parent.
    expect(
      restorableRows(
        [{ row_id: "x", order: 10, row_meta: { change: { kind: "deleted" } } }],
        5,
      ),
    ).toEqual({ rows: [{ row_id: "x", n: 11 }], from: 4 });
  });

  it("dialog words", () => {
    const t = restoreRowsText({ rows: [31, 11], from: 4, latest: 7 });
    expect(t.title).toBe("Restore 2 rows from v4 as v8?");
    expect(t.body).toBe(
      "Rows 11 and 31 come back as they were in v4, with the same ids and row numbers. No other row changes. v7 stays in the history.",
    );
    expect(t.description).toBe("Restored Rows 11 and 31 from v4");
    expect(t.toast(8)).toBe("Saved v8 · 2 rows restored");
    const one = restoreRowsText({ rows: [31], from: 4, latest: 6 });
    expect(one.body).toBe(
      "Row 31 comes back as it was in v4, with the same id and row number. No other row changes. v6 stays in the history.",
    );
    expect(rowNumbersText([40, 11, 31])).toBe("Rows 11, 31 and 40");
  });

  it("history line and deltas name restored rows", () => {
    const counts = { added: 2, restored: 2, restored_from: 4, deleted: 0 };
    expect(restoredRowsText(2, 4)).toBe("Restored 2 rows (from v4)");
    expect(deltaParts(counts).map((p) => [p.key, p.n])).toEqual([
      ["restored", 2],
    ]);
    expect(deltaTooltip(counts)).toBe("Restored 2 rows (from v4)");
    expect(deltaTooltip({ added: 3, restored: 1, restored_from: 4 })).toBe(
      "+2 rows added, Restored 1 row (from v4)",
    );
  });

  it("Restore version says rows deleted later come back as new rows", () => {
    expect(restoreAsNewRowsText({ deleted: 2 }, 4)).toBe(
      "Rows deleted after v4 come back as new rows with new numbers. To keep a row's id, open v4 and use Restore row.",
    );
    expect(restoreAsNewRowsText({ deleted: 0, added: 3 }, 4)).toBeNull();
  });

  it("Row view gate: read-only as of a version; M6's own gate unchanged", () => {
    const access = { can: { edit: true } };
    expect(rowViewGate({ access, asOf: true })).toMatchObject({
      canEdit: false,
      annotate: false,
      asOf: true,
    });
    expect(rowViewGate({ access })).toMatchObject({
      canEdit: true,
      annotate: true,
    });
    expect(rowAccessGate({ access, asOf: true }).annotate).toBe(true);
    expect(deletedRowText({ version: 6, latest: 7 })).toBe(
      "Deleted in v6 · not in the latest version (v7).",
    );
  });
});
