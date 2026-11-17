import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import ConnectedAgentsView from "../ConnectedAgentsView";

// At 1024 px the Connected agents table cut
// "Used in" mid-word and hid Created at past the right edge, with no
// scrollbar in sight; at 1280 the Created at value was cut. Every column's
// minimum added up to 1110 px.
//
// jsdom has no layout, so this binds the column contract DataTable hands to
// MUI DataGrid (src/components/data-table/DataTable.jsx): a `meta.flex`
// column gets `minSize` as its minWidth, a `size` column gets that fixed
// width. MUI never shrinks a column below its minWidth. The pixel proof is
// a browser walk.
const captured = vi.hoisted(() => ({ props: null }));

vi.mock("src/components/data-table", () => ({
  DataTable: (props) => {
    captured.props = props;
    return null;
  },
  DataTablePagination: () => null,
}));

vi.mock("src/api/agents/connectedAgents", () => ({
  useConnectedAgents: () => ({
    data: {
      items: [{ id: "ca-01", name: "Customer support line" }],
      total: 1,
    },
    isLoading: false,
    isError: false,
  }),
}));

const GRID_DEFAULT_MIN_WIDTH = 50;
// The table's width: the window less the 200 px menu and 2 x 24 px page
// padding (agents-connected-w1-1024.jpg: x 224 to 1000).
const TABLE_WIDTH_AT_1024 = 776;
// When the rows scroll inside the grid, DataGrid sizes the columns from the
// table width less the vertical bar: DataTable's always-visible bar is 8 px.
const SCROLLBAR = 8;

// Text widths below are measured from the app's own font, Inter (the
// @fontsource/inter 5.2.8 latin files), by its advance widths with and
// without pair kerning, the wider of the two: values are 12 px/400. A cell
// adds MUI's 2 x 10 px padding, and 1 px for sub-pixel rounding.
const CELL_PADDING = 2 * 10;
const SUBPIXEL = 1;
const cellWidthFor = (textPx) => Math.ceil(textPx + CELL_PADDING + SUBPIXEL);
// "No environments yet": 118.1 px.
const USED_IN_WIDTH = cellWidthFor(118.1);
// "dd-MM-yyyy, h:mm a" at its widest from 2026 to 2030, "04-04-2030,
// 10:44 AM": 132.3 px ("20-10-2026, 10:00 AM" is 128.4).
const DATE_WIDTH = cellWidthFor(132.3);
// "Voice · Outbound" (98.7 px) after its 16 px icon and the 6 px gap.
const MODALITY_WIDTH = cellWidthFor(98.7 + 16 + 6);

const minWidthOf = (column) =>
  column.meta?.flex
    ? column.minSize ?? GRID_DEFAULT_MIN_WIDTH
    : column.size ?? GRID_DEFAULT_MIN_WIDTH;

const renderView = () =>
  render(
    <MemoryRouter initialEntries={["/dashboard/agents/connected"]}>
      <ConnectedAgentsView onConnect={vi.fn()} />
    </MemoryRouter>,
  );

describe("Connected agents table at 1024 px", () => {
  it("fits every column, whole, in a 1024 px window with the bars showing", () => {
    renderView();
    const { columns, showScrollbar } = captured.props;
    const byId = Object.fromEntries(columns.map((c) => [c.id, c]));

    expect(minWidthOf(byId.createdAt)).toBeGreaterThanOrEqual(DATE_WIDTH);
    expect(minWidthOf(byId.usedIn)).toBeGreaterThanOrEqual(USED_IN_WIDTH);
    const minimumTotal = columns.reduce((sum, c) => sum + minWidthOf(c), 0);
    expect(minimumTotal).toBeLessThanOrEqual(TABLE_WIDTH_AT_1024 - SCROLLBAR);
    // Should a column still not fit (a long font fallback), the bar that
    // reaches it is drawn, not left to the overlay bars macOS hides.
    expect(showScrollbar).toBe(true);
  });

  // Repair round: 'No environments yet' still ended in an ellipsis at 1024
  // (130 px left it 110 px), and Modality's 140 px was 0.7 px short of
  // 'Voice · Outbound' with its icon.
  it("keeps 'No environments yet', 'Voice · Outbound' and every date whole, by the font's own widths", () => {
    renderView();
    const byId = Object.fromEntries(
      captured.props.columns.map((c) => [c.id, c]),
    );
    expect(USED_IN_WIDTH).toBeGreaterThanOrEqual(139);
    expect(minWidthOf(byId.usedIn)).toBeGreaterThanOrEqual(USED_IN_WIDTH);
    expect(minWidthOf(byId.modality)).toBeGreaterThanOrEqual(MODALITY_WIDTH);
    expect(minWidthOf(byId.createdAt)).toBeGreaterThanOrEqual(DATE_WIDTH);
  });

  it("puts the whole date in the Created at cell's title", () => {
    renderView();
    const createdAt = captured.props.columns.find((c) => c.id === "createdAt");
    render(
      createdAt.cell({
        row: { original: { createdAt: "2026-10-20T10:00:00" } },
      }),
    );
    expect(screen.getByText("20-10-2026, 10:00 AM")).toHaveAttribute(
      "title",
      "20-10-2026, 10:00 AM",
    );
  });
});

// The table's width at each window: the window less the 200 px menu and
// 2 x 24 px page padding. The rows scroll inside the grid, so DataTable's
// 8 px bar comes off it as well.
const TABLE_WIDTH = { 1024: TABLE_WIDTH_AT_1024, 1280: 1032, 1440: 1192 };
// Every "dd-MM-yyyy, h:mm a" from 2024 to 2035: the widest, "04-04-2034,
// 10:44 AM", is 132.47 px.
const WIDEST_DATE_WIDTH = cellWidthFor(132.47);
const EVERY_COLUMN = [
  "Agent name",
  "Modality",
  "Source",
  "Versions",
  "Used in",
  "Created at",
];

// jsdom has no viewport: answer a width media query as a browser this wide
// would, so a column put away below a breakpoint shows up here.
const atViewport = (width) => {
  window.matchMedia = (query) => {
    const bounds = [...query.matchAll(/(min|max)-width:\s*([\d.]+)px/g)];
    return {
      matches:
        bounds.length > 0 &&
        bounds.every(([, bound, px]) =>
          bound === "min" ? width >= Number(px) : width <= Number(px),
        ),
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    };
  };
};

describe.each([1024, 1280, 1440])("Connected agents at %i px", (width) => {
  let realMatchMedia;
  beforeEach(() => {
    realMatchMedia = window.matchMedia;
    atViewport(width);
  });
  afterEach(() => {
    window.matchMedia = realMatchMedia;
  });

  it("shows every column, hides none, and fits them all with no sideways scroll", () => {
    renderView();
    const { columns, columnVisibility } = captured.props;
    expect(columns.map((c) => c.header)).toEqual(EVERY_COLUMN);
    expect(Object.values(columnVisibility ?? {})).not.toContain(false);
    const minimumTotal = columns.reduce((sum, c) => sum + minWidthOf(c), 0);
    expect(minimumTotal).toBeLessThanOrEqual(TABLE_WIDTH[width] - SCROLLBAR);
  });

  it("keeps every date from 2024 to 2035 and 'No environments yet' whole, and the whole row opens the agent", () => {
    renderView();
    const byId = Object.fromEntries(
      captured.props.columns.map((c) => [c.id, c]),
    );
    expect(minWidthOf(byId.createdAt)).toBeGreaterThanOrEqual(
      WIDEST_DATE_WIDTH,
    );
    expect(minWidthOf(byId.usedIn)).toBeGreaterThanOrEqual(USED_IN_WIDTH);
    // The list has no row actions to reach: the row itself opens the agent.
    expect(captured.props.onRowClick).toEqual(expect.any(Function));
  });
});

// jsdom lays nothing out: an element whose only text is `value` reports
// itself wider than its box, as a browser does for a cut value.
const reportCut = (value) => {
  const proto = HTMLElement.prototype;
  const report = (prop, px) =>
    Object.defineProperty(proto, prop, {
      configurable: true,
      get() {
        return this.childElementCount === 0 && this.textContent === value
          ? px
          : 0;
      },
    });
  report("scrollWidth", 300);
  report("clientWidth", 120);
  return () => {
    delete proto.scrollWidth;
    delete proto.clientWidth;
  };
};

// A cell drawn on its own, inside a grid cell as DataGrid draws it.
const renderCell = (node) =>
  render(
    <div className="MuiDataGrid-cell" tabIndex={-1} data-testid="cell">
      {node}
    </div>,
  );

const LONG_NAME = "Downtown clinic receptionist for evening and weekend calls";
const LONG_ENVIRONMENT = "Downtown clinic front desk, evening shift";

describe("Connected agents table, values cut short", () => {
  // A cut value shows whole in a tooltip on hover, and when its cell takes
  // keyboard focus (the grid's arrow keys move focus from cell to cell).
  it.each([
    ["name", { id: "ca-09", name: LONG_NAME }, LONG_NAME],
    [
      "usedIn",
      {
        id: "ca-09",
        environmentCount: 1,
        environments: [{ name: LONG_ENVIRONMENT }],
      },
      LONG_ENVIRONMENT,
    ],
  ])(
    "shows a cut %s whole on hover and on keyboard focus",
    async (id, agent, value) => {
      renderView();
      const column = captured.props.columns.find((c) => c.id === id);
      const makeCell = () => column.cell({ row: { original: agent } });
      const restore = reportCut(value);
      try {
        const hovered = renderCell(makeCell());
        fireEvent.mouseEnter(screen.getByText(value));
        expect(await screen.findByRole("tooltip")).toHaveTextContent(value);
        hovered.unmount();

        renderCell(makeCell());
        act(() => screen.getByTestId("cell").focus());
        expect(await screen.findByRole("tooltip")).toHaveTextContent(value);
      } finally {
        restore();
      }
    },
  );
});
