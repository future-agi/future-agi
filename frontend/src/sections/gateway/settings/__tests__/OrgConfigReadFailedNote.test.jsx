// When the gateway's settings report could not be read, the Org config page
// says so once at the top, not only in each row's Why.
import React, { forwardRef } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

import OrgConfigSection from "../OrgConfigSection";

const gatewayConfig = vi.hoisted(() => ({ current: null }));
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function MockGrid() {
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
  useGatewayConfig: () => gatewayConfig.current,
}));
vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gateway-1" }),
}));
vi.mock("../OrgConfigEditor", () => ({ default: () => null }));
vi.mock("../ConfigHistoryDrawer", () => ({ default: () => null }));

const NOTE =
  "The gateway's settings report couldn't be read, so enforcement is shown as unknown.";

beforeEach(() => {
  gatewayConfig.current = { isLoading: false, data: {} };
});

describe("Org config: a page note when the settings report can't be read", () => {
  it("shows one note when the read failed", () => {
    gatewayConfig.current = {
      isLoading: false,
      isError: true,
      data: undefined,
    };
    render(<OrgConfigSection />);
    expect(screen.getAllByText(NOTE)).toHaveLength(1);
  });

  it("shows no note when the gateway answered", () => {
    gatewayConfig.current = {
      isLoading: false,
      isError: false,
      data: { org_sections_enforced: ["budgets"] },
    };
    render(<OrgConfigSection />);
    expect(screen.queryByText(NOTE)).toBeNull();
  });
});
