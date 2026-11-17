import React, { createElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, renderHook } from "@testing-library/react";
import { createTheme, ThemeProvider } from "@mui/material/styles";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { palette } from "src/theme/palette";
import { ISSUE_FILTER_FIELDS } from "../common";
import { useAlertSheetView } from "../store/useAlertSheetView";
import {
  resetAlertSheetStoreState,
  useAlertSheetStore,
} from "../store/useAlertSheetStore";

// The issues sheet shows Trigger type and Status as chips, and a chip with too
// little room ends its label in an ellipsis. jsdom has no layout, so these
// tests read the widths the sheet hands the grid and do the grid's arithmetic.

// Label text widths in the chip's font, Inter at 12 px and weight 500, rounded
// up to whole pixels.
const LABEL_TEXT = { Critical: 41, Warning: 49, Open: 31, Resolved: 53 };
// The chip adds 6 px of padding on each side, a 6 px dot and a 6 px gap.
const CHIP_CHROME = 24;
// The grid pads every cell by 16 px on each side.
const CELL_PADDING = 32;
// The Actions menu button is never narrower than 71 px.
const ACTIONS_BUTTON = 71;
// The checkbox column is 50 px wide.
const SELECTION_COLUMN = 50;
const CHIP_COLUMNS = ["Trigger type", "Status"];

// The issues grid takes 8.5 of 12 parts of the window less the sheet's 20 px
// side padding, less its own 16 px padding on each side.
const gridWidthAt = (windowWidth) => ((windowWidth - 40) * 8.5) / 12 - 32;

const theme = createTheme({ palette: palette("light") });

let client;

// The sheet builds its column defs in a hook and keeps them in its store.
const sheetColumns = () => {
  renderHook(() => useAlertSheetView(), {
    wrapper: ({ children }) =>
      createElement(
        MemoryRouter,
        null,
        createElement(QueryClientProvider, { client }, children),
      ),
  });
  return useAlertSheetStore.getState().columnDefs;
};

const columnIn = (defs, name) => defs.find((def) => def.headerName === name);

// The text a column's cell shows for one issue, rendered the way the grid does.
const shownLabel = (column, data) => {
  const Cell = column.cellRenderer;
  const { container, unmount } = render(
    <ThemeProvider theme={theme}>
      <Cell {...column.cellRendererParams({ data })} />
    </ThemeProvider>,
  );
  const label = container.textContent;
  unmount();
  return label;
};

beforeEach(() => {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  resetAlertSheetStoreState();
});

afterEach(() => {
  cleanup();
  client.clear();
});

describe("alert issues sheet chip columns", () => {
  it("keeps the Trigger type column wide enough for every trigger type label", () => {
    const column = columnIn(sheetColumns(), "Trigger type");
    const { choices } = ISSUE_FILTER_FIELDS.find(
      (field) => field.value === "type",
    );
    const labels = choices.map((type) =>
      shownLabel(column, { id: `issue-${type}`, type }),
    );

    expect(labels).toContain("Warning");
    for (const label of labels) expect(LABEL_TEXT).toHaveProperty(label);
    const widest = Math.max(...labels.map((label) => LABEL_TEXT[label]));
    // A minWidth only holds back a flex column.
    expect(column.flex).toBeTruthy();
    expect(column.minWidth ?? 0).toBeGreaterThanOrEqual(
      widest + CHIP_CHROME + CELL_PADDING,
    );
  });

  it("keeps the Status column wide enough for Open and Resolved", () => {
    const column = columnIn(sheetColumns(), "Status");
    const labels = [false, true].map((resolved) =>
      shownLabel(column, { id: `issue-${resolved}`, resolved }),
    );

    expect(labels).toEqual(["Open", "Resolved"]);
    const widest = Math.max(...labels.map((label) => LABEL_TEXT[label]));
    expect(column.flex).toBeTruthy();
    expect(column.minWidth ?? 0).toBeGreaterThanOrEqual(
      widest + CHIP_CHROME + CELL_PADDING,
    );
  });

  it("takes under 5 px from each other column at a 1024 px window", () => {
    const defs = sheetColumns();
    // Issue takes two shares of the row, every other column one.
    expect(defs.map((def) => def.flex)).toEqual([2, 1, 1, 1, 1]);
    const room = gridWidthAt(1024) - SELECTION_COLUMN;
    const evenShare = room / 6;
    // A chip column keeps its minWidth when its share would be smaller, and
    // the other columns split what is left.
    const chipWidths = CHIP_COLUMNS.map((name) =>
      Math.max(columnIn(defs, name).minWidth ?? 0, evenShare),
    );
    const otherShare = (room - chipWidths[0] - chipWidths[1]) / 4;

    expect(evenShare - otherShare).toBeLessThan(5);
    // The Actions button still clears the cell's 16 px left padding.
    expect(otherShare).toBeGreaterThanOrEqual(
      CELL_PADDING / 2 + ACTIONS_BUTTON,
    );
    // The minimum widths add up to less than the grid, so it never scrolls
    // sideways.
    const minimums = defs.reduce(
      (sum, def) => sum + (def.minWidth ?? 0),
      SELECTION_COLUMN,
    );
    expect(minimums).toBeLessThanOrEqual(gridWidthAt(1024));
  });

  it("leaves every column its even share at 1280 and 1440 px windows", () => {
    const defs = sheetColumns();
    const widestChip = Math.max(...Object.values(LABEL_TEXT)) + CHIP_CHROME;

    for (const windowWidth of [1280, 1440]) {
      const evenShare = (gridWidthAt(windowWidth) - SELECTION_COLUMN) / 6;
      for (const name of CHIP_COLUMNS) {
        expect(columnIn(defs, name).minWidth ?? 0).toBeLessThan(evenShare);
      }
      // Every chip label has room in that share.
      expect(evenShare - CELL_PADDING).toBeGreaterThanOrEqual(widestChip);
    }
  });
});
