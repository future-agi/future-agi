import React from "react";
import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";

import { SavedOverview } from "./OffInventory";
import { renderMcp } from "./testing/renderMcp";

vi.mock("ag-grid-react", async () => ({
  AgGridReact: (await import("./testing/agGridMock")).MockAgGrid,
}));
vi.mock("../providers/hooks/useGatewayConfig", async (importOriginal) => ({
  ...(await importOriginal()),
  useGatewayConfig: () => ({
    data: { mcp: { servers: { alpha: { url: "http://alpha/mcp" } } } },
    isLoading: false,
    isError: false,
  }),
}));
vi.mock("../hooks/useGatewayAdmin", async (importOriginal) => ({
  ...(await importOriginal()),
  useGatewayAdmin: () => ({ canEdit: true }),
}));

// Every MCP tab starts at the page frame's 12px gap under the tab bar (screen
// pass F09): the saved overview shown while MCP is off adds no top padding.
describe("MCP-off overview spacing", () => {
  it("starts at the frame gap, with no top padding of its own", () => {
    const { container } = renderMcp(<SavedOverview gatewayId="gw-off" />);
    expect(screen.getByText("alpha")).toBeInTheDocument();
    expect(container.firstElementChild).not.toHaveStyle({
      paddingTop: "16px",
    });
  });
});
