import React from "react";
import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";

import { renderMcp } from "../testing/renderMcp";
import PlaygroundTab from "./PlaygroundTab";

vi.mock("../hooks/useMCPConfig", () => ({
  useMCPTools: () => ({
    data: [
      {
        name: "crm-tools__find_account",
        server_id: "crm-tools",
        upstream_name: "find_account",
      },
    ],
    isLoading: false,
    isError: false,
    isFetching: false,
    refetch: vi.fn(),
  }),
  useTestMCPTool: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

// The card that holds `el`: the nearest box with a border.
function cardOf(el) {
  let node = el;
  while (node && window.getComputedStyle(node).borderStyle !== "solid") {
    node = node.parentElement;
  }
  return node;
}

// The "Run a tool" card stopped at 960 px (about 90 px
// short of the right gutter at 1280) with 12 px of padding, while the other
// MCP tab cards run full width with 16 px.
describe("MCP > Playground (layout)", () => {
  it("runs the Run a tool card full width with the MCP cards' 16 px padding", () => {
    const { container } = renderMcp(<PlaygroundTab gatewayId="gw-1" />);
    const capped = Array.from(container.querySelectorAll("*")).filter(
      (el) => window.getComputedStyle(el).maxWidth === "960px",
    );
    expect(capped).toEqual([]);
    const card = cardOf(screen.getByText("Run a tool"));
    expect(window.getComputedStyle(card).padding).toBe("16px");
  });
});
