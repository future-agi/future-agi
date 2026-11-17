import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import axios from "src/utils/axios";
import MCPClientsTab from "./MCPClientsTab";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), delete: vi.fn() },
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { id: "member-a" } }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "org-a", orgLevel: 3 }),
}));

// Every MCP tab starts at the page frame's 12px gap under the tab bar (screen
// pass F09): the MCP clients tab adds no top padding of its own.
describe("MCP clients tab spacing", () => {
  it("starts at the frame gap, with no top padding of its own", async () => {
    axios.get.mockResolvedValue({ data: { status: true, result: [] } });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { container } = render(
      <QueryClientProvider client={client}>
        <MCPClientsTab />
      </QueryClientProvider>,
    );
    expect(
      await screen.findByRole("heading", { name: "MCP clients" }),
    ).toBeInTheDocument();
    expect(container.firstElementChild).not.toHaveStyle({
      paddingTop: "24px",
    });
  });
});
