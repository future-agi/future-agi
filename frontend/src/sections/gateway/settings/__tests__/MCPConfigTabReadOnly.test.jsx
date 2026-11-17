import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";

import MCPConfigTab from "../MCPConfigTab";

// The gateway reads an organization's MCP guardrails only for counters: a
// blocked tool, allowed server or per-tool limit set here is not checked.
// So they are not options: what is stored shows read-only beside its chip,
// and a save keeps it. The servers stay editable.

const MCP = {
  enabled: true,
  servers: { github: { url: "https://mcp.example.com", transport: "http" } },
  guardrails: {
    enabled: true,
    blocked_tools: ["shell"],
    allowed_servers: ["github"],
    validate_inputs: true,
    validate_outputs: false,
    tool_rate_limits: { search: 10 },
  },
};

describe("Org config > MCP", () => {
  it("shows stored MCP guardrails read-only beside the chip, with no control for them", () => {
    render(<MCPConfigTab mcp={MCP} onChange={vi.fn()} />);
    const stored = screen.getByTestId("mcp-guardrails-recorded-only");
    expect(
      within(stored).getByTestId("enforcement-mcp.guardrails"),
    ).toHaveTextContent("Not enforced on this gateway");
    for (const text of ["shell", "github", "search: 10 per minute"]) {
      expect(stored).toHaveTextContent(text);
    }
    expect(stored.querySelector("input, textarea, [role=combobox]")).toBeNull();
    for (const label of [
      /Enable MCP guardrails/,
      /Validate Inputs/,
      /Validate Outputs/,
    ]) {
      expect(screen.queryByLabelText(label)).toBeNull();
    }
    expect(screen.queryByPlaceholderText("Tool name")).toBeNull();
  });

  it("keeps the servers editable", () => {
    render(<MCPConfigTab mcp={MCP} onChange={vi.fn()} />);
    expect(screen.getByLabelText("Server ID")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Add Server" }),
    ).toBeInTheDocument();
  });
});
