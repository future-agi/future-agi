import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ENVIRONMENT_RUN } from "src/api/agents/_fixtures/agentDetail";
import AgentOverviewTab from "../AgentOverviewTab";

// On a connected agent's Overview at 1024 px
// the Recent runs table cut the When column at the right edge ("Whe",
// "star"). Every column's minimum added up to 890 px.
//
// jsdom has no layout, so this binds the column contract the table hands to
// MUI DataGrid (src/components/data-table/DataTable.jsx): a `meta.flex`
// column gets `minSize` as its minWidth, a `size` column gets that fixed
// width. The pixel proof is a browser walk.
const captured = vi.hoisted(() => ({ columns: null, props: null }));

vi.mock("../FitDataTable", () => ({
  default: (props) => {
    captured.columns = props.columns;
    captured.props = props;
    return null;
  },
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: {} }),
}));

vi.mock("src/api/agents/agentDetail", async () => {
  const actual = await vi.importActual("src/api/agents/agentDetail");
  return {
    ...actual,
    useAgentRuns: () => ({
      isLoading: false,
      isError: false,
      data: [ENVIRONMENT_RUN].map(actual.toRun),
    }),
  };
});

const GRID_DEFAULT_MIN_WIDTH = 50;
// The table's width: the window less the 200 px menu and 2 x 16 px page
// padding (agents-connected-w2-1024.jpg: x 216 to 1008). The table is sized
// to its 15 rows at most, so no vertical bar takes from it.
const TABLE_WIDTH_AT_1024 = 792;
// The widest When value, "started yesterday", measured from the app's own
// font, Inter (@fontsource/inter 5.2.8), in the s1 type (12 px/400) by its
// advance widths with and without pair kerning, the wider: 99.5 px. Plus the
// cell's 2 x 10 px padding and 1 px for sub-pixel rounding. (The first
// round's 120 px left it 0.5 px.)
const WHEN_WIDTH = Math.ceil(99.5 + 2 * 10 + 1);

const minWidthOf = (column) =>
  column.meta?.flex
    ? column.minSize ?? GRID_DEFAULT_MIN_WIDTH
    : column.size ?? GRID_DEFAULT_MIN_WIDTH;

describe("Recent runs table at 1024 px", () => {
  it("fits every column, When whole, in a 1024 px window", () => {
    render(
      <MemoryRouter
        initialEntries={["/dashboard/agents/connected/a1/overview"]}
      >
        <Routes>
          <Route
            path="/dashboard/agents/connected/:agentId/overview"
            element={<AgentOverviewTab />}
          />
        </Routes>
      </MemoryRouter>,
    );
    const byId = Object.fromEntries(captured.columns.map((c) => [c.id, c]));
    expect(minWidthOf(byId.when)).toBeGreaterThanOrEqual(WHEN_WIDTH);
    const minimumTotal = captured.columns.reduce(
      (sum, c) => sum + minWidthOf(c),
      0,
    );
    expect(minimumTotal).toBeLessThanOrEqual(TABLE_WIDTH_AT_1024);
  });
});

// The table's width at each window, by the same arithmetic as at 1024.
const TABLE_WIDTH = { 1024: TABLE_WIDTH_AT_1024, 1280: 1048, 1440: 1208 };
const EVERY_COLUMN = [
  "Run",
  "Status",
  "Environment",
  "Agent version",
  "Pass rate",
  "When",
];
// The widest status, "Completed" (61.99 px at 12 px/400 Inter), after its
// 8 px dot and 6 px gap, plus the cell's padding and rounding.
const STATUS_WIDTH = Math.ceil(8 + 6 + 61.99 + 2 * 10 + 1);

const renderTab = () =>
  render(
    <MemoryRouter initialEntries={["/dashboard/agents/connected/a1/overview"]}>
      <Routes>
        <Route
          path="/dashboard/agents/connected/:agentId/overview"
          element={<AgentOverviewTab />}
        />
      </Routes>
    </MemoryRouter>,
  );

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

describe.each([1024, 1280, 1440])("Recent runs at %i px", (width) => {
  let realMatchMedia;
  beforeEach(() => {
    realMatchMedia = window.matchMedia;
    atViewport(width);
  });
  afterEach(() => {
    window.matchMedia = realMatchMedia;
  });

  it("shows every column, hides none, and fits them all with no sideways scroll", () => {
    renderTab();
    const { columns, columnVisibility } = captured.props;
    expect(columns.map((c) => c.header)).toEqual(EVERY_COLUMN);
    expect(Object.values(columnVisibility ?? {})).not.toContain(false);
    const minimumTotal = columns.reduce((sum, c) => sum + minWidthOf(c), 0);
    expect(minimumTotal).toBeLessThanOrEqual(TABLE_WIDTH[width]);
  });

  it("keeps Status and When readable without hovering, and the whole row opens the run", () => {
    renderTab();
    const byId = Object.fromEntries(captured.columns.map((c) => [c.id, c]));
    expect(minWidthOf(byId.status)).toBeGreaterThanOrEqual(STATUS_WIDTH);
    // "started yesterday" whole, with room to spare.
    expect(minWidthOf(byId.when)).toBeGreaterThanOrEqual(124);
    // The table has no row actions to reach: the row itself opens the run.
    expect(captured.props.onRowClick).toEqual(expect.any(Function));
  });
});

// A cell drawn on its own, inside a grid cell as DataGrid draws it.
const renderCell = (node) =>
  render(
    <div className="MuiDataGrid-cell" tabIndex={-1} data-testid="cell">
      {node}
    </div>,
  );

// The tooltip of the cell `makeCell` draws holds `whole`, both on hover of
// the text `shown` and when the cell takes keyboard focus (the grid's arrow
// keys move focus from cell to cell).
const expectInTooltip = async (makeCell, shown, whole) => {
  const hovered = renderCell(makeCell());
  fireEvent.mouseEnter(screen.getByText(shown));
  expect(await screen.findByRole("tooltip")).toHaveTextContent(whole);
  hovered.unmount();

  renderCell(makeCell());
  act(() => screen.getByTestId("cell").focus());
  expect(await screen.findByRole("tooltip")).toHaveTextContent(whole);
};

const cellOf = (id, run) => () =>
  captured.columns.find((c) => c.id === id).cell({ row: { original: run } });

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

const LONG_ENVIRONMENT = "Downtown clinic front desk, evening shift";
const EXACT_TIME = "07-03-2025, 3:04 PM";
// No offset, so the times read as local wherever the test runs.
const FINISHED = {
  kind: "environment",
  testExecutionId: "te-2",
  runNumber: 2,
  status: "completed",
  environment: { id: "env-2", name: LONG_ENVIRONMENT },
  startedAt: "2025-03-07T14:50:00",
  completedAt: "2025-03-07T15:04:00",
};
const RUNNING = {
  ...FINISHED,
  testExecutionId: "te-3",
  status: "running",
  startedAt: "2025-03-07T15:04:00",
  completedAt: null,
};

describe("Recent runs, values cut short and times", () => {
  it("shows a cut environment name whole on hover and on keyboard focus", async () => {
    renderTab();
    const restore = reportCut(LONG_ENVIRONMENT);
    try {
      await expectInTooltip(
        cellOf("environment", FINISHED),
        LONG_ENVIRONMENT,
        LONG_ENVIRONMENT,
      );
    } finally {
      restore();
    }
  });

  it("says when a run finished in short, with the exact time on hover and on keyboard focus", async () => {
    renderTab();
    await expectInTooltip(cellOf("when", FINISHED), /ago$/, EXACT_TIME);
  });

  it("says when a run still going started in short, with the exact time on hover and on keyboard focus", async () => {
    renderTab();
    await expectInTooltip(cellOf("when", RUNNING), /^started /, EXACT_TIME);
  });
});
