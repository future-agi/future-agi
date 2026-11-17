import React, { forwardRef } from "react";
import { describe, expect, it, vi } from "vitest";
import { render } from "src/utils/test-utils";

import OrgConfigSection from "../OrgConfigSection";
import {
  CELL_PADDING_PX,
  HEADER_PADDING_PX,
  HEADER_SORT_PX,
  allColumns,
  flexWidths,
  minimumGridWidth,
} from "../../logs/__tests__/gridFit";

const grid = vi.hoisted(() => ({ props: null }));
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function MockGrid(props, _ref) {
    grid.props = props;
    return <div data-testid="ag-grid" />;
  }),
}));

vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 15 }),
}));
vi.mock("../../providers/hooks/useOrgConfig", () => ({
  useOrgConfig: () => ({
    data: { id: "cfg-1", version: 3, created_at: "2026-10-20T09:00:00Z" },
    isLoading: false,
    error: null,
  }),
  useCreateOrgConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("../../providers/hooks/useGatewayConfig", () => ({
  useProviderHealth: () => ({ data: { providers: [] } }),
  useGatewayConfig: () => ({ isLoading: false, data: {} }),
}));
vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gateway-1" }),
}));
vi.mock("../OrgConfigEditor", () => ({ default: () => null }));
vi.mock("../ConfigHistoryDrawer", () => ({ default: () => null }));

// The Sections grid's room: the window less the 200 px gateway rail, the page
// frame's two 16 px gutters, the panel's 1 px borders and SettingsPanel's
// 24 px padding each side from 600 px up (742 px at 1024). The Sections
// panel overrides that padding to 16 px until C-04 restores SettingsPanel's,
// so before C-04 it has 758 px; a grid that fits 742 fits both.
const gridAt = (width) => width - 200 - 2 * 16 - 2 - 2 * 24;

// Header labels, Inter 500 14 px advance widths from the bundled font file
// (@fontsource/inter latin 500), no kerning, rounded up plus 1 px.
const HEADER_TEXT = {
  Section: 52,
  Saved: 43,
  "On this gateway": 110,
  Why: 32,
};
const headerNeeds = (col) =>
  HEADER_TEXT[col.headerName] +
  HEADER_PADDING_PX +
  (col.sortable ? HEADER_SORT_PX : 0);

// The widest values: "Routing and fallbacks" at Inter 400 12 px (124 px);
// "12 fallback chains", the widest one-part Saved value, at Inter 400 12 px
// (102 px; a strategy and its chains together were already cut before);
// and the "Not enforced on this gateway" chip, its label at Inter 400 11 px
// (154 px) plus the chip's dot, padding and border (33 px: in the 1024 px
// screenshot the "Partly enforced" chip is 113 px wide around an 80 px
// label).
const VALUE_NEEDS = {
  section: 124 + CELL_PADDING_PX,
  saved: 102 + CELL_PADDING_PX,
  gap: 154 + 33 + CELL_PADDING_PX,
};

// The minimum widths before C-01 (OrgConfigSection at cut 2a). At 1280 and
// 1440 px they laid out with room to spare, and the grid should look the
// same there now.
const BEFORE = [
  { field: "section", minWidth: 170, flex: 1 },
  { field: "saved", minWidth: 160, flex: 1 },
  { field: "gap", minWidth: 230, flex: 1.2 },
  { field: "reasons", minWidth: 260, flex: 2.5 },
];

// C-01: at 1024 px the Sections grid was wider than its card, so the Why
// column ran past the card's edge, cut mid-word with no ellipsis.
describe("Org config Sections grid width", () => {
  it("fits the Sections grid inside its card at 1024 px", () => {
    render(<OrgConfigSection />);
    expect(minimumGridWidth(grid.props)).toBeLessThanOrEqual(gridAt(1024));
  });

  it("keeps each column's header, the longest section name and Saved value, and the widest chip whole at 1024 px", () => {
    render(<OrgConfigSection />);
    const widths = flexWidths(grid.props, gridAt(1024));
    allColumns(grid.props).forEach((col) => {
      const id = col.colId || col.field;
      expect(widths[id], `${id} header`).toBeGreaterThanOrEqual(
        headerNeeds(col),
      );
      if (VALUE_NEEDS[id] !== undefined) {
        expect(widths[id], `${id} value`).toBeGreaterThanOrEqual(
          VALUE_NEEDS[id],
        );
      }
    });
  });

  it("lays the columns out as before at 1280 and 1440 px", () => {
    render(<OrgConfigSection />);
    const before = {
      columnDefs: BEFORE,
      defaultColDef: grid.props.defaultColDef,
    };
    [1280, 1440].forEach((width) => {
      const now = flexWidths(grid.props, gridAt(width));
      const was = flexWidths(before, gridAt(width));
      Object.keys(was).forEach((id) => {
        expect(now[id], `${id} at ${width}`).toBeCloseTo(was[id], 6);
      });
    });
  });
});
