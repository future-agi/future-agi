import React, { forwardRef } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

import OrgConfigSection from "../OrgConfigSection";

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

const whyColumn = () =>
  grid.props.columnDefs.find((col) => col.field === "reasons");

// The Why column was cut off on every row.
describe("Org config Why column", () => {
  it("wraps to its own height, so no row's reason is cut off", () => {
    render(<OrgConfigSection />);
    const why = whyColumn();
    expect(why.wrapText).toBe(true);
    expect(why.autoHeight).toBe(true);
    // A fixed grid height would clip rows that grow: the grid sizes to them.
    expect(grid.props.domLayout).toBe("autoHeight");
  });

  // At 1024 a two-line clamp cut "couldn't be read" to "The gateway's...":
  // the reason now reads in full, and the tooltip keeps it too.
  it("shows the whole reason, with the full reason in the tooltip too", () => {
    render(<OrgConfigSection />);
    const why = whyColumn();
    const reasons = ["First reason, long enough.", "Second reason."];
    expect(why.tooltipValueGetter({ value: reasons })).toBe(
      "First reason, long enough.\n\nSecond reason.",
    );
    const Cell = why.cellRenderer;
    render(<Cell value={reasons} />);
    const text = screen.getByText("First reason, long enough. Second reason.");
    expect(text).not.toHaveStyle({ WebkitLineClamp: "2" });
    expect(text).not.toHaveStyle({ overflow: "hidden" });
  });

  it("reads the empty mark when a section has no reason", () => {
    render(<OrgConfigSection />);
    const Cell = whyColumn().cellRenderer;
    render(<Cell value={[]} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});
