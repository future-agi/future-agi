// On Run Simulation, at 1280 px the row's
// settings (gear) button was cut at the right edge, and at 1024 Last Run
// and the gear were both past the edge, with no scrollbar in sight. Every
// column's minimum added up to 1110 px.
//
// jsdom has no layout, so this binds the column contract DataTable hands to
// MUI DataGrid (src/components/data-table/DataTable.jsx): a `meta.flex`
// column gets `minSize` as its minWidth, a `size` column gets that fixed
// width. The pixel proof is a browser walk.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "src/utils/test-utils";

import RunTestsContent from "../RunTestsContent";

const captured = vi.hoisted(() => ({ props: null }));
const http = vi.hoisted(() => ({ get: vi.fn(), delete: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { get: http.get, delete: http.delete },
  endpoints: {
    runTests: {
      list: "/simulate/run-tests/",
      detail: (id) => `/simulate/run-tests/${id}/`,
    },
  },
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  Events: {},
  PropertyName: {},
}));
vi.mock("src/components/data-table", () => ({
  DataTable: (props) => {
    captured.props = props;
    return null;
  },
  DataTablePagination: () => null,
}));

const GRID_DEFAULT_MIN_WIDTH = 50;
// The table's width: the window less the 200 px menu and 2 x 16 px page
// padding (runsim-create-w3-1024.jpg: x 216 to 1008).
const TABLE_WIDTH_AT_1024 = 792;
// DataTable's always-visible bars (showScrollbar) are 8 px.
const SCROLLBAR = 8;
// "less than a minute ago", date-fns' longest relative time, measured from
// the app's own font, Inter (@fontsource/inter 5.2.8), at 12 px/400 by its
// advance widths with and without pair kerning, the wider: 127.5 px. Plus
// the cell's 2 x 10 px padding and 1 px for sub-pixel rounding. (The first
// round's 146 px left it 126 px: it ended in an ellipsis.)
const RELATIVE_TIME_WIDTH = Math.ceil(127.5 + 2 * 10 + 1);
// The row's settings button is at least 71 px wide, plus padding.
const ACTIONS_WIDTH = 91;

const minWidthOf = (column) =>
  column.meta?.flex
    ? column.minSize ?? GRID_DEFAULT_MIN_WIDTH
    : column.size ?? GRID_DEFAULT_MIN_WIDTH;

const SIMULATION = {
  id: "sim-1",
  name: "Checkout voice agent",
  agent_definition_detail: null,
  scenarios_detail: [],
  evals_detail: [],
  last_run_at: null,
};

describe("Run Simulation table at 1024 px", () => {
  it("fits every column and the row's settings button in a 1024 px window, and keeps the button on the right edge if not", async () => {
    http.get.mockResolvedValue({ data: { count: 1, results: [SIMULATION] } });
    render(
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <RunTestsContent onCreateClick={vi.fn()} />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(captured.props?.data).toHaveLength(1));

    const { columns, pinnedRight, showScrollbar } = captured.props;
    const byId = Object.fromEntries(columns.map((c) => [c.id, c]));
    expect(byId.actions).toBeTruthy();
    expect(minWidthOf(byId.actions)).toBeGreaterThanOrEqual(ACTIONS_WIDTH);
    expect(minWidthOf(byId.last_run_at)).toBeGreaterThanOrEqual(
      RELATIVE_TIME_WIDTH,
    );
    const minimumTotal = columns.reduce((sum, c) => sum + minWidthOf(c), 0);
    expect(minimumTotal).toBeLessThanOrEqual(TABLE_WIDTH_AT_1024 - SCROLLBAR);
    expect(pinnedRight).toBe("actions");
    expect(showScrollbar).toBe(true);
  });

  // Repair round: at 1024 px the name column is about 150 px, so a long
  // simulation name ends in an ellipsis. Its full text must be readable
  // from the keyboard (the grid's arrow keys move focus from cell to cell).
  it("shows a cut simulation name whole in a tooltip when its cell takes keyboard focus", async () => {
    http.get.mockResolvedValue({ data: { count: 1, results: [SIMULATION] } });
    render(
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <RunTestsContent onCreateClick={vi.fn()} />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(captured.props?.data).toHaveLength(1));
    const name = captured.props.columns.find((c) => c.id === "name");

    const NAME = "Checkout voice agent, returns and exchanges";
    // jsdom lays nothing out: the name reports itself wider than its box,
    // as a browser does for a cut name.
    const proto = HTMLElement.prototype;
    const report = (prop, value) =>
      Object.defineProperty(proto, prop, {
        configurable: true,
        get() {
          return this.childElementCount === 0 && this.textContent === NAME
            ? value
            : 0;
        },
      });
    report("scrollWidth", 260);
    report("clientWidth", 130);
    try {
      render(
        <div className="MuiDataGrid-cell" tabIndex={-1} data-testid="cell">
          {name.cell({ getValue: () => NAME })}
        </div>,
      );
      act(() => screen.getByTestId("cell").focus());
      expect(await screen.findByRole("tooltip")).toHaveTextContent(NAME);
    } finally {
      delete proto.scrollWidth;
      delete proto.clientWidth;
    }
  });
});
