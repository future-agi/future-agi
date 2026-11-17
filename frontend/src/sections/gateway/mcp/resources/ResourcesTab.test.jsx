import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";

import { renderMcp } from "../testing/renderMcp";
import ResourcesTab from "./ResourcesTab";

vi.mock("ag-grid-react", async () => {
  const { MockAgGrid } = await import("../testing/agGridMock");
  return { AgGridReact: MockAgGrid };
});
const { query } = vi.hoisted(() => ({ query: { current: {} } }));
vi.mock("../hooks/useMCPConfig", () => ({
  useMCPResources: () => query.current,
}));

describe("ResourcesTab", () => {
  it("lists resources on the FA grid", () => {
    query.current = {
      data: [
        {
          uri: "docs://handbook/refunds",
          name: "Refund policy",
          mimeType: "text/markdown",
          server_id: "support-tools",
        },
        {
          uri: "docs://handbook/shipping",
          name: "Shipping policy",
          mimeType: "text/markdown",
          server_id: "support-tools",
        },
      ],
      isLoading: false,
    };
    renderMcp(<ResourcesTab gatewayId="gw-1" />);
    const [first] = screen.getAllByRole("row");
    expect(
      within(first).getByText("docs://handbook/refunds"),
    ).toBeInTheDocument();
    expect(within(first).getByText("support-tools")).toBeInTheDocument();
    fireEvent.change(
      screen.getByRole("textbox", { name: "Search resources" }),
      {
        target: { value: "shipping" },
      },
    );
    expect(screen.getAllByRole("row")).toHaveLength(1);
  });

  it("shows each resource's type and description, and finds one by either", () => {
    query.current = {
      data: [
        {
          uri: "docs://handbook/refunds",
          name: "Refund policy",
          mimeType: "text/markdown",
          description: "How refunds are approved",
          server_id: "support-tools",
        },
        {
          uri: "files://exports/orders.csv",
          name: "Orders export",
          server_id: "support-tools",
        },
      ],
      isLoading: false,
    };
    renderMcp(<ResourcesTab gatewayId="gw-1" />);
    const [first, second] = screen.getAllByRole("row");
    expect(within(first).getByText("text/markdown")).toBeInTheDocument();
    expect(
      within(first).getByText("How refunds are approved"),
    ).toBeInTheDocument();
    expect(within(second).getAllByText("—")).toHaveLength(2);
    const search = screen.getByRole("textbox", { name: "Search resources" });
    fireEvent.change(search, { target: { value: "approved" } });
    expect(screen.getAllByRole("row")).toHaveLength(1);
    fireEvent.change(search, { target: { value: "orders.csv" } });
    const [row] = screen.getAllByRole("row");
    expect(within(row).getByText("Orders export")).toBeInTheDocument();
  });

  it("explains an empty list", () => {
    query.current = { data: [], isLoading: false };
    renderMcp(<ResourcesTab gatewayId="gw-1" />);
    expect(screen.getByText("No resources")).toBeInTheDocument();
  });
});
