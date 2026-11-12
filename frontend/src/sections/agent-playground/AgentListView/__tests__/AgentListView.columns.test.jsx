import { describe, it, expect, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import AgentListView from "../AgentListView";

// At 1280 px the Agent Playground
// header "Created by" was cut to "Created...". The column was flex with no
// minimum, so after Agent Name took its 280 px minimum it got the 72 px left.
//
// jsdom has no layout, so this binds the column contract DataTable hands to
// MUI DataGrid (src/components/data-table/DataTable.jsx): a `meta.flex`
// column gets `minSize` as its minWidth, a `size` column gets that fixed
// width, and the selection checkbox column is 50 px. MUI never shrinks a
// column below its minWidth.
const captured = vi.hoisted(() => ({ columns: null, props: null }));

vi.mock("src/components/data-table", () => ({
  DataTable: (props) => {
    captured.columns = props.columns;
    captured.props = props;
    return null;
  },
  DataTablePagination: () => null,
}));

vi.mock("src/sections/agent-playground/hooks/useCanEditAgent", () => ({
  default: () => ({ canCreate: true, canUpdate: true, canDelete: true }),
}));

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(() =>
      Promise.resolve({
        data: { result: { graphs: [], metadata: { total_count: 0 } } },
      }),
    ),
  },
  endpoints: { agentPlayground: { listGraphs: "/graphs/" } },
}));

// MUI DataGrid's checkbox column width and default column minWidth.
const CHECKBOX_WIDTH = 50;
const GRID_DEFAULT_MIN_WIDTH = 50;
// The table's width in a 1280 px window: 1280 - 200 sidebar - 2 x 24 page
// padding, as measured in agents-playground-15.png.
const TABLE_WIDTH_AT_1280 = 1032;
// When the 25-row page scrolls inside the grid, MUI X DataGrid 6.20.0 sizes
// flex columns from the table width minus the vertical scrollbar. A classic
// scrollbar (Windows, Linux, or macOS set to always show them) is up to 17 px.
const CLASSIC_SCROLLBAR = 17;
// "Created by" in the 11 px header font (about 58 px) plus the header cell's
// 2 x 10 px padding, with room to spare.
const CREATED_BY_HEADER_WIDTH = 100;
// At 1024 px Updated at sat past the right
// edge with no scrollbar in sight. The table there: 1024 - 200 sidebar -
// 2 x 24 page padding (agents-playground-w3-1024.jpg: x 224 to 1000).
const TABLE_WIDTH_AT_1024 = 776;
// DataTable's always-visible bars (showScrollbar) are 8 px.
const SHOWN_SCROLLBAR = 8;
// Text widths are measured from the app's own font, Inter (the
// @fontsource/inter 5.2.8 latin files), by its advance widths with and
// without pair kerning, the wider of the two: values are 12 px/400. A cell
// adds MUI's 2 x 10 px padding, and 1 px for sub-pixel rounding.
const CELL_PADDING = 2 * 10;
const SUBPIXEL = 1;
const cellWidthFor = (textPx) => Math.ceil(textPx + CELL_PADDING + SUBPIXEL);
// "dd-MM-yyyy, h:mm a" at its widest from 2026 to 2030, "04-04-2030,
// 10:44 AM": 132.3 px. "20-10-2026, 10:00 AM" is 128.4 px and
// "28-12-2026, 12:58 PM" 126.5 px: the 142 px of the first round cut them.
const DATE_WIDTH = cellWidthFor(132.3);
// "No. of nodes" and "Collaborators" in the 11 px header font plus padding.
const NODES_HEADER_WIDTH = 86;
const COLLABORATORS_HEADER_WIDTH = 92;

const minWidthOf = (column) =>
  column.meta?.flex
    ? column.minSize ?? GRID_DEFAULT_MIN_WIDTH
    : column.size ?? GRID_DEFAULT_MIN_WIDTH;

const renderList = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={["/dashboard/agents/playground"]}>
        <AgentListView embedded />
      </MemoryRouter>
    </QueryClientProvider>,
  );

// jsdom lays nothing out: `text` reports itself wider than its box, as a
// browser does for text that ends in an ellipsis.
const reportCut = (text) => {
  const proto = HTMLElement.prototype;
  const report = (prop, value) =>
    Object.defineProperty(proto, prop, {
      configurable: true,
      get() {
        return this.childElementCount === 0 && this.textContent === text
          ? value
          : 0;
      },
    });
  report("scrollWidth", 162);
  report("clientWidth", 100);
  return () => {
    delete proto.scrollWidth;
    delete proto.clientWidth;
  };
};

describe("Agent Playground table columns", () => {
  it("shows the whole Created by header at 1280 px without scrolling sideways", () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter initialEntries={["/dashboard/agents/playground"]}>
          <AgentListView embedded />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const columns = captured.columns;
    expect(columns).toBeTruthy();
    const createdBy = columns.find((column) => column.id === "createdBy");
    expect(createdBy.header).toBe("Created by");
    // DataTable passes minSize on as minWidth only for a flex column.
    expect(createdBy.meta?.flex).toBeTruthy();
    expect(minWidthOf(createdBy)).toBeGreaterThanOrEqual(
      CREATED_BY_HEADER_WIDTH,
    );

    // Every column's minimum still fits, even with a vertical scrollbar, so
    // the grid fills the table without a sideways scroll and Created by gets
    // at least its minimum.
    const minimumTotal =
      CHECKBOX_WIDTH +
      columns.reduce((sum, column) => sum + minWidthOf(column), 0);
    expect(minimumTotal).toBeLessThanOrEqual(
      TABLE_WIDTH_AT_1280 - CLASSIC_SCROLLBAR,
    );
  });
  it("fits every column, headers and dates whole, in a 1024 px window", () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter initialEntries={["/dashboard/agents/playground"]}>
          <AgentListView embedded />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const { columns, showScrollbar } = captured.props;
    const byId = Object.fromEntries(columns.map((c) => [c.id, c]));
    expect(minWidthOf(byId.created)).toBeGreaterThanOrEqual(DATE_WIDTH);
    expect(minWidthOf(byId.updated)).toBeGreaterThanOrEqual(DATE_WIDTH);
    expect(minWidthOf(byId.noOfNodes)).toBeGreaterThanOrEqual(
      NODES_HEADER_WIDTH,
    );
    expect(minWidthOf(byId.collaborators)).toBeGreaterThanOrEqual(
      COLLABORATORS_HEADER_WIDTH,
    );
    expect(minWidthOf(byId.createdBy)).toBeGreaterThanOrEqual(
      CREATED_BY_HEADER_WIDTH,
    );
    const minimumTotal =
      CHECKBOX_WIDTH + columns.reduce((sum, c) => sum + minWidthOf(c), 0);
    expect(minimumTotal).toBeLessThanOrEqual(
      TABLE_WIDTH_AT_1024 - SHOWN_SCROLLBAR,
    );
    // The bars are drawn, so the 8 px above is the bar the grid takes.
    expect(showScrollbar).toBe(true);
  });

  // Repair round: the first round's 142 px left the dates 122 px, so most
  // times past 10 o'clock ended in an ellipsis at every width, 1280 and
  // 1440 included (they were whole at 180).
  it("keeps every Created at and Updated at value whole, by the font's own widths, with the value in the title", () => {
    renderList();
    const byId = Object.fromEntries(
      captured.props.columns.map((c) => [c.id, c]),
    );
    expect(DATE_WIDTH).toBeGreaterThanOrEqual(149);
    expect(minWidthOf(byId.created)).toBeGreaterThanOrEqual(DATE_WIDTH);
    expect(minWidthOf(byId.updated)).toBeGreaterThanOrEqual(DATE_WIDTH);

    ["created", "updated"].forEach((id) => {
      const { unmount } = render(
        byId[id].cell({ getValue: () => "2026-10-20T10:00:00" }),
      );
      expect(screen.getByText("20-10-2026, 10:00 AM")).toHaveAttribute(
        "title",
        "20-10-2026, 10:00 AM",
      );
      unmount();
    });
  });

  // Repair round: at 1024 px the name column is about 120 px, so a long
  // name ends in an ellipsis. Its full text must be readable from the
  // keyboard (the grid's arrow keys move focus from cell to cell).
  it("shows a cut agent name whole in a tooltip when its cell takes keyboard focus", async () => {
    renderList();
    const NAME = "Agent Nov 11, 2026 7:28 AM";
    const name = captured.props.columns.find((c) => c.id === "name");
    const restore = reportCut(NAME);
    try {
      render(
        <div className="MuiDataGrid-cell" tabIndex={-1} data-testid="cell">
          {name.cell({ getValue: () => NAME })}
        </div>,
      );
      act(() => screen.getByTestId("cell").focus());
      expect(await screen.findByRole("tooltip")).toHaveTextContent(NAME);
    } finally {
      restore();
    }
  });
});
