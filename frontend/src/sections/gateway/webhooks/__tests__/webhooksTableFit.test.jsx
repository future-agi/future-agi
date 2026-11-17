import React, { forwardRef } from "react";
import { HelmetProvider } from "react-helmet-async";
import { Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "src/utils/test-utils";

import { NAV } from "src/layouts/config-layout";
import WebhookManagementSection from "../WebhookManagementSection";
import {
  CELL_PADDING_PX,
  SCROLLBAR_PX,
  allColumns,
  columnOf,
  effectiveColumns,
  gridBudget1440,
  minimumGridWidth,
  minimumWidth,
} from "../../logs/__tests__/gridFit";

// The Webhooks table at 1440, 1280 and 1024 px: every column keeps its place
// and label, the row controls and Status stay on screen, and a long name,
// URL or event list ends in an ellipsis with the whole value on hover.

const grids = vi.hoisted(() => ({ list: [] }));
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function MockGrid(props, _ref) {
    grids.list.push(props);
    return <div data-testid="ag-grid" />;
  }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));
vi.mock("../../guardrails/hooks/useGuardrailsOff", () => ({
  useGuardrailsOff: () => false,
}));

const state = vi.hoisted(() => ({ webhooks: [], events: [] }));
vi.mock("../hooks/useWebhooks", () => ({
  useWebhooks: () => ({
    data: state.webhooks,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
  useCreateWebhook: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateWebhook: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteWebhook: () => ({ mutate: vi.fn(), isPending: false }),
  useTestWebhook: () => ({ mutate: vi.fn(), isPending: false }),
  useWebhookEvents: () => ({
    data: state.events,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
  useRetryWebhookEvent: () => ({ mutate: vi.fn(), isPending: false }),
}));

const LONG_NAME = "payments-production-incident-escalation-webhook";
const LONG_URL =
  "https://hooks.example.com/services/incident-channel/escalations/production/payments";
const HOOK = {
  id: "wh-long",
  name: LONG_NAME,
  url: LONG_URL,
  events: ["budget.exceeded", "error.occurred", "guardrail.triggered"],
  is_active: true,
  headers: {},
  description: "Incidents to the on-call channel",
  created_at: "2026-05-24T04:44:00Z",
};
const HTTP_URL = "http://hooks.example.com/services/incident-channel";
const BADGE = "Unencrypted (HTTP)";

// The page around a gateway grid from md up: the sidebar
// (layouts/dashboard/nav-vertical.jsx, NAV.W_VERTICAL - 40), the page
// frame's 16 px padding each side (GatewayPageFrame, p: 2) and the grid's
// 1 px border each side, less a classic vertical scrollbar once the page
// scrolls.
const SIDEBAR_PX = NAV.W_VERTICAL - 40;
const FRAME_PADDING_PX = 2 * 16;
const GRID_BORDER_PX = 2;
const gridBudget = (viewport) =>
  viewport - SIDEBAR_PX - FRAME_PADDING_PX - GRID_BORDER_PX - SCROLLBAR_PX;

// The widest date and time the Last delivery cell shows ("May 24, 2026,
// 04:44 AM", Inter 400 11 px advance widths from the bundled font file, no
// kerning: 131 px), rounded up plus 1 px.
const LAST_DELIVERY_TIME_PX = 132;

const OLD_LAYOUT = "gateway-grid:gateway-webhooks";
const LAYOUT = "gateway-grid:gateway-webhooks-v2";

function renderPage() {
  window.history.pushState({}, "", "/dashboard/gateway/webhooks");
  render(
    <HelmetProvider>
      <Routes>
        <Route
          path="/dashboard/gateway/webhooks"
          element={<WebhookManagementSection />}
        />
      </Routes>
    </HelmetProvider>,
  );
  return grids.list[grids.list.length - 1];
}

const idOf = (col) => col.colId || col.field;

// Whether a column is on screen at every sideways scroll of a grid this
// wide, every column at its minimum width: a pinned column always is; a
// centre column is when it sits inside the centre viewport both before the
// scroll and at its end.
function alwaysInView(grid, id, budget) {
  const columns = effectiveColumns(grid);
  const width = (list) => list.reduce((sum, col) => sum + minimumWidth(col), 0);
  const pinned = width(columns.filter((col) => col.pinned));
  const target = columns.find((col) => idOf(col) === id);
  if (target.pinned) return pinned <= budget;
  const centre = columns.filter((col) => !col.pinned);
  const viewport = budget - pinned;
  const furthest = Math.max(0, width(centre) - viewport);
  const start = width(centre.slice(0, centre.indexOf(target)));
  return start >= furthest && start + minimumWidth(target) <= viewport;
}

// A column as a cell renderer sees it.
const columnAt = (width) => ({
  getActualWidth: () => width,
  addEventListener: () => {},
  removeEventListener: () => {},
});

beforeEach(() => {
  grids.list = [];
  state.webhooks = [HOOK];
  state.events = [];
  window.localStorage.clear();
});

afterEach(() => {
  window.localStorage.clear();
});

describe("Webhooks table widths", () => {
  it("measures the page around the grid as gridFit does at 1440 px", () => {
    expect(gridBudget(1440)).toBe(gridBudget1440());
  });

  it("keeps every column in its place, with its label", () => {
    const grid = renderPage();
    expect(effectiveColumns(grid).map((col) => col.headerName)).toEqual([
      "Webhook",
      "Endpoint",
      "Events",
      "Status",
      "Last delivery",
      "Created",
      "",
    ]);
  });

  it("never pins a column that flexes", () => {
    const grid = renderPage();
    const pinnedAndFlex = allColumns(grid)
      .filter((col) => col.pinned && col.flex)
      .map(idOf);
    expect(pinnedAndFlex).toEqual([]);
  });

  it("gives Status and the row actions fixed widths, the actions pinned right", () => {
    const grid = renderPage();
    for (const id of ["is_active", "actions"]) {
      const col = columnOf(grid, id);
      expect(col.flex, id).toBeFalsy();
      expect(col.width, id).toBeGreaterThan(0);
      expect(col.minWidth, id).toBe(col.width);
    }
    expect(columnOf(grid, "actions").pinned).toBe("right");
    // Status keeps its place among the columns.
    expect(columnOf(grid, "is_active").pinned).toBeFalsy();
  });

  it("fits every column at 1280 and 1440 px without scrolling sideways", () => {
    const grid = renderPage();
    expect(minimumGridWidth(grid)).toBeLessThanOrEqual(gridBudget(1280));
    expect(minimumGridWidth(grid)).toBeLessThanOrEqual(gridBudget(1440));
  });

  it("keeps Status and the row actions on screen at 1024 px, however far the other columns scroll", () => {
    const grid = renderPage();
    const fixed = allColumns(grid)
      .filter((col) => !col.flex)
      .reduce((sum, col) => sum + minimumWidth(col), 0);
    expect(fixed).toBeLessThanOrEqual(gridBudget(1024));
    expect(alwaysInView(grid, "is_active", gridBudget(1024))).toBe(true);
    expect(alwaysInView(grid, "actions", gridBudget(1024))).toBe(true);
  });

  it("grows to its rows, so a sideways scrollbar never covers the last row", () => {
    const grid = renderPage();
    expect(grid.domLayout).toBe("autoHeight");
  });

  it("keeps the widest date and time whole in Last delivery", () => {
    const lastDelivery = minimumWidth(columnOf(renderPage(), "lastDelivery"));
    expect(lastDelivery).toBeGreaterThanOrEqual(
      LAST_DELIVERY_TIME_PX + CELL_PADDING_PX,
    );
  });
});

describe("Webhooks table: a long value ends in an ellipsis and shows in full on hover", () => {
  it("the webhook's name", () => {
    const Name = columnOf(renderPage(), "name").cellRenderer;
    render(<Name data={HOOK} />);
    const name = screen.getByText(LONG_NAME);
    expect(window.getComputedStyle(name).textOverflow).toBe("ellipsis");
    expect(name).toHaveAttribute("title", LONG_NAME);
  });

  it("the endpoint URL", () => {
    const Endpoint = columnOf(renderPage(), "url").cellRenderer;
    render(<Endpoint value={LONG_URL} />);
    const url = screen.getByText(LONG_URL);
    expect(window.getComputedStyle(url).textOverflow).toBe("ellipsis");
    expect(url).toHaveAttribute("title", LONG_URL);
  });

  // At 1280 and 1024 px the Endpoint column can be narrower than the badge
  // under an http:// URL. The badge ends in an ellipsis within the cell and
  // shows its words on hover. The ellipsis only clips what is painted: the
  // badge's text stays whole, so a screen reader reads every word.
  it("the HTTP badge under an http:// URL", () => {
    const Endpoint = columnOf(renderPage(), "url").cellRenderer;
    render(<Endpoint value={HTTP_URL} />);
    const badge = screen.getByText(BADGE);
    expect(badge.textContent).toBe(BADGE);
    expect(badge).toHaveAttribute("title", BADGE);
    // Its own text names it: a span with no role takes no aria-label.
    expect(badge).not.toHaveAttribute("aria-label");
    expect(badge).toHaveStyle({
      overflow: "hidden",
      textOverflow: "ellipsis",
      whiteSpace: "nowrap",
      maxWidth: "100%",
    });
  });

  it("no HTTP badge under an https:// URL", () => {
    const Endpoint = columnOf(renderPage(), "url").cellRenderer;
    render(<Endpoint value={LONG_URL} />);
    expect(screen.queryByText(BADGE)).not.toBeInTheDocument();
    expect(screen.queryByTitle(BADGE)).not.toBeInTheDocument();
  });

  it("the subscribed events, at the column's narrowest", () => {
    const events = columnOf(renderPage(), "events");
    const Events = events.cellRenderer;
    render(
      <Events
        data={HOOK}
        value={HOOK.events}
        column={columnAt(minimumWidth(events))}
      />,
    );
    const shown = HOOK.events.filter((name) =>
      screen.queryByTestId(`webhook-event-${name}`),
    );
    expect(shown.length).toBeGreaterThan(0);
    for (const name of shown) {
      const chip = screen.getByTestId(`webhook-event-${name}`);
      expect(window.getComputedStyle(chip).textOverflow, name).toBe("ellipsis");
      expect(chip, name).toHaveAttribute("title", name);
    }
    const hidden = HOOK.events.filter((name) => !shown.includes(name));
    expect(screen.getByTestId("webhook-events-more")).toHaveAccessibleName(
      `${hidden.length} more events: ${hidden.join(", ")}`,
    );
  });
});

describe("Webhooks table: a layout saved in this browser before", () => {
  it("comes back without its old widths, flex or pin, so the row actions stay pinned", () => {
    window.localStorage.setItem(
      OLD_LAYOUT,
      JSON.stringify([
        { colId: "name", width: 300, flex: 1.1, sort: "asc", sortIndex: 0 },
        { colId: "events", width: 230, flex: 1.5, hide: true },
        { colId: "actions", width: 228, flex: 0.9, pinned: null },
      ]),
    );
    const grid = renderPage();
    const carried = [
      { colId: "name", sort: "asc", sortIndex: 0 },
      { colId: "events", hide: true },
      { colId: "actions" },
    ];
    expect(window.localStorage.getItem(OLD_LAYOUT)).toBeNull();
    expect(JSON.parse(window.localStorage.getItem(LAYOUT))).toEqual(carried);
    const applyColumnState = vi.fn();
    grid.onGridReady({ api: { applyColumnState } });
    expect(applyColumnState).toHaveBeenCalledWith({
      state: carried,
      applyOrder: true,
    });
  });
});

// At 1024 px Last delivery and Created sit past the grid's right edge until
// it scrolls. AG Grid moves cell focus with the arrow keys and Tab and
// scrolls the cell it lands on into view, so a keyboard user reaches them
// once the cells take focus and no column is skipped.
describe("Webhooks table: keyboard users reach every column", () => {
  it("lets its cells take focus, so the arrow keys reach the columns past the right edge", () => {
    const grid = renderPage();
    expect(grid.suppressCellFocus).toBe(false);
    expect(grid.suppressHeaderFocus).toBeFalsy();
  });

  it("skips no column and leaves moving focus to the grid", () => {
    const grid = renderPage();
    for (const col of allColumns(grid)) {
      expect(col.suppressNavigable, idOf(col)).toBeFalsy();
    }
    for (const id of ["lastDelivery", "created_at"]) {
      expect(columnOf(grid, id).hide, id).toBeFalsy();
    }
    expect(grid.navigateToNextCell).toBeUndefined();
    expect(grid.tabToNextCell).toBeUndefined();
  });
});

// The grid's api at 1024 px, every column at its minimum width and scrolled
// fully left: the middle columns run past what is on screen, and the row
// actions are pinned right.
function apiAt1024(grid) {
  const columns = effectiveColumns(grid);
  const widthOf = (list) =>
    list.reduce((sum, col) => sum + minimumWidth(col), 0);
  const asColumns = (list) =>
    list.map((col) => ({ getActualWidth: () => minimumWidth(col) }));
  const pinned = columns.filter((col) => col.pinned === "right");
  const centre = columns.filter((col) => !col.pinned);
  return {
    getHorizontalPixelRange: () => ({
      left: 0,
      right: gridBudget(1024) - widthOf(pinned),
    }),
    getDisplayedCenterColumns: () => asColumns(centre),
    getDisplayedRightColumns: () => asColumns(pinned),
    getColumnState: () => [],
  };
}

describe("Webhooks table: the sideways-scroll shade at 1024 px", () => {
  it("sits where the hidden columns are, at the left edge of the pinned row actions", () => {
    const grid = renderPage();
    act(() => grid.onGridSizeChanged({ api: apiAt1024(grid) }));
    const actions = minimumWidth(columnOf(grid, "actions"));
    // 1 px for the grid's border.
    expect(screen.getByTestId("gateway-grid-more-right")).toHaveStyle({
      right: `${actions + 1}px`,
    });
  });
});
