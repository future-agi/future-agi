import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen } from "src/utils/test-utils";

// On Environments > Personas, at 1280 px
// Last updated was cut ("Last upda", "10 minut") and every row's Duplicate
// sat past the right edge; at 1024 Created By, Last updated and the row
// actions were all off the edge, with no scrollbar in sight. Every column's
// minimum added up to 1218 px.
//
// jsdom has no layout, so this binds the column contract DataTable hands to
// MUI DataGrid (src/components/data-table/DataTable.jsx): a `meta.flex`
// column gets `minSize` as its minWidth, a `size` column gets that fixed
// width, and the selection checkbox column is 50 px. The pixel proof is a
// browser walk.
const captured = vi.hoisted(() => ({ props: null }));
const auth = vi.hoisted(() => ({ role: "Owner" }));

vi.mock("src/components/data-table", () => ({
  DataTable: (props) => {
    captured.props = props;
    return null;
  },
  DataTablePagination: () => null,
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: auth.role }),
}));

vi.mock("src/api/persona/persona", () => ({
  useGetPersonasPaginated: () => ({
    data: {
      results: [{ id: "p1", name: "The Polite Senior Caller" }],
      count: 1,
    },
    isLoading: false,
    isFetching: false,
  }),
  useBulkDeletePersonas: () => ({ mutate: vi.fn(), isPending: false }),
}));

const { default: PersonaListView } = await import("../PersonaListView");

afterEach(() => {
  auth.role = "Owner";
});

const CHECKBOX_WIDTH = 50;
const GRID_DEFAULT_MIN_WIDTH = 50;
// The table's width: the window less the 200 px menu and 2 x 16 px page
// padding (env-persona-w1-1280.jpg: x 216 to 1264).
const TABLE_WIDTH_AT_1280 = 1048;
// DataTable's always-visible bars (showScrollbar) are 8 px.
const SCROLLBAR = 8;
// "less than a minute ago", date-fns' longest relative time, measured from
// the app's own font, Inter (@fontsource/inter 5.2.8), at 12 px/400 by its
// advance widths with and without pair kerning, the wider: 127.5 px. Plus
// the cell's 2 x 10 px padding and 1 px for sub-pixel rounding.
const RELATIVE_TIME_WIDTH = Math.ceil(127.5 + 2 * 10 + 1);
// Duplicate and Edit: two 28 px icon buttons, 2 px apart, plus padding.
const ACTIONS_WIDTH = 78;

const minWidthOf = (column) =>
  column.meta?.flex
    ? column.minSize ?? GRID_DEFAULT_MIN_WIDTH
    : column.size ?? GRID_DEFAULT_MIN_WIDTH;

const renderList = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <PersonaListView />
    </QueryClientProvider>,
  );

describe("Personas table at 1024 and 1280 px", () => {
  it("fits every column, Last updated whole, in a 1280 px window", () => {
    renderList();
    const { columns } = captured.props;
    const byId = Object.fromEntries(columns.map((c) => [c.id, c]));
    expect(minWidthOf(byId.lastUpdated)).toBeGreaterThanOrEqual(
      RELATIVE_TIME_WIDTH,
    );
    const minimumTotal =
      CHECKBOX_WIDTH + columns.reduce((sum, c) => sum + minWidthOf(c), 0);
    expect(minimumTotal).toBeLessThanOrEqual(TABLE_WIDTH_AT_1280 - SCROLLBAR);
  });

  it("keeps Duplicate and Edit on the right edge, and the sideways bar drawn, where the columns cannot all fit (1024 px)", () => {
    renderList();
    const { columns, pinnedRight, showScrollbar } = captured.props;
    const actions = columns.find((c) => c.id === "actions");
    expect(actions).toBeTruthy();
    expect(minWidthOf(actions)).toBeGreaterThanOrEqual(ACTIONS_WIDTH);
    expect(pinnedRight).toBe("actions");
    expect(showScrollbar).toBe(true);
  });

  // Repair round: a viewer cannot duplicate or edit, so every actions cell
  // was empty, and at 1024 px the pinned column drew an empty 80 px sticky
  // strip with a divider down the right edge of the table.
  it("gives a viewer no actions column and no pinned strip", () => {
    auth.role = "Viewer";
    renderList();
    const { columns, pinnedRight, showScrollbar } = captured.props;
    expect(columns.find((c) => c.id === "actions")).toBeUndefined();
    expect(pinnedRight).toBeUndefined();
    // The bar that reaches the columns past the edge still shows.
    expect(showScrollbar).toBe(true);
  });

  // Repair round: the name column is 180 px at 1024 (the table scrolls
  // sideways there), so a long persona name ends in an ellipsis. Its full
  // text must be readable from the keyboard (the grid's arrow keys move
  // focus from cell to cell).
  it("shows a cut persona name whole in a tooltip when its cell takes keyboard focus", async () => {
    renderList();
    const name = captured.props.columns.find((c) => c.id === "name");
    const NAME = "The Polite Senior Caller Who Asks About Every Charge";
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
    report("scrollWidth", 300);
    report("clientWidth", 160);
    try {
      render(
        <div className="MuiDataGrid-cell" tabIndex={-1} data-testid="cell">
          {name.cell({ row: { original: { id: "p9", name: NAME } } })}
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

// The table's width at each window: the window less the 200 px menu and
// 2 x 16 px page padding. The rows scroll, so the 8 px bar comes off it.
const TABLE_WIDTH = { 1024: 792, 1280: TABLE_WIDTH_AT_1280, 1440: 1208 };
const EVERY_COLUMN = [
  "name",
  "description",
  "simulationType",
  "tags",
  "createdBy",
  "lastUpdated",
  "actions",
];
const minimumTotalOf = (columns) =>
  CHECKBOX_WIDTH + columns.reduce((sum, c) => sum + minWidthOf(c), 0);

// jsdom has no viewport: answer a width media query as a browser this wide
// would, so a column put away below a breakpoint shows up here.
let realMatchMedia;
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

describe("Personas table at 1024, 1280 and 1440 px", () => {
  beforeEach(() => {
    realMatchMedia = window.matchMedia;
  });
  afterEach(() => {
    window.matchMedia = realMatchMedia;
  });

  it.each([1024, 1280, 1440])(
    "shows every column at %i, row actions included, hides none and keeps Last updated whole",
    (width) => {
      atViewport(width);
      renderList();
      const { columns, columnVisibility } = captured.props;
      expect(columns.map((c) => c.id)).toEqual(EVERY_COLUMN);
      expect(Object.values(columnVisibility ?? {})).not.toContain(false);
      const lastUpdated = columns.find((c) => c.id === "lastUpdated");
      expect(minWidthOf(lastUpdated)).toBeGreaterThanOrEqual(
        RELATIVE_TIME_WIDTH,
      );
    },
  );

  it.each([1280, 1440])(
    "fits every column at %i, so the table does not scroll sideways",
    (width) => {
      atViewport(width);
      renderList();
      expect(minimumTotalOf(captured.props.columns)).toBeLessThanOrEqual(
        TABLE_WIDTH[width] - SCROLLBAR,
      );
    },
  );

  it("scrolls sideways at 1024 only under a drawn bar, with Duplicate and Edit pinned to the right edge", () => {
    atViewport(1024);
    renderList();
    const { columns, pinnedRight, showScrollbar } = captured.props;
    // The whole minimum widths (950 px) are wider than the 784 px the
    // table has at 1024.
    expect(minimumTotalOf(columns)).toBeGreaterThan(
      TABLE_WIDTH[1024] - SCROLLBAR,
    );
    expect(showScrollbar).toBe(true);
    expect(pinnedRight).toBe("actions");
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

const LONG_PERSONA = {
  id: "p-09",
  name: "Impatient executive calling between two meetings",
  description: "Wants the refund today and keeps asking for a manager",
  simulationType: "voice",
  isDefault: false,
  gender: ["female"],
  ageGroup: ["45-54"],
  profession: ["Executive"],
  accent: ["Scottish"],
  communicationStyle: ["Direct"],
  // No offset, so the time reads as local wherever the test runs.
  updatedAt: "2025-03-07T15:04:00",
};
const EVERY_ATTRIBUTE = "Female · 45-54 · Executive · Scottish · Direct";
const EXACT_TIME = "07-03-2025, 3:04 PM";

const cellOf = (id) => () => {
  const column = captured.props.columns.find((c) => c.id === id);
  return column.cell({
    row: { original: LONG_PERSONA },
    getValue: () => LONG_PERSONA[column.accessorKey],
  });
};

describe("Personas table, values cut short and times", () => {
  it.each([
    ["name", LONG_PERSONA.name],
    ["description", LONG_PERSONA.description],
  ])(
    "shows a cut %s whole on hover and on keyboard focus",
    async (id, value) => {
      renderList();
      const restore = reportCut(value);
      try {
        await expectInTooltip(cellOf(id), value, value);
      } finally {
        restore();
      }
    },
  );

  // Five attributes: the first two, then how many more there are, in sight
  // without hovering; every attribute on hover of either and when the cell
  // takes keyboard focus.
  it("shows the first two attributes and +3 for the other three, with every attribute on hover and on keyboard focus", async () => {
    renderList();
    const shown = renderCell(cellOf("tags")());
    expect(screen.getByText("+3")).toBeInTheDocument();
    fireEvent.mouseEnter(screen.getByText("+3"));
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      EVERY_ATTRIBUTE,
    );
    shown.unmount();

    await expectInTooltip(cellOf("tags"), "Female · 45-54", EVERY_ATTRIBUTE);
  });

  it("says how long ago a persona changed, whole, with the exact time on hover and on keyboard focus", async () => {
    renderList();
    await expectInTooltip(cellOf("lastUpdated"), /ago$/, EXACT_TIME);
  });
});
