import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
// The org-config editor's MCP section. Its test lives with the rest of the MCP
// console's tests.
import MCPConfigTab from "../settings/MCPConfigTab";

const STORED_TOKEN = "stored-bearer-value-0001";

const storedMcp = {
  enabled: true,
  servers: {
    github: {
      url: "https://mcp.example.com/github",
      transport: "http",
      auth: { type: "bearer", token: STORED_TOKEN },
    },
    docs: {
      url: "https://mcp.example.com/docs",
      transport: "http",
      auth: { type: "bearer" },
    },
  },
};

describe("MCPConfigTab", () => {
  it("no stdio option", () => {
    render(<MCPConfigTab mcp={storedMcp} onChange={vi.fn()} />);

    expect(screen.queryByLabelText(/Transport/)).toBeNull();
    expect(screen.queryByText(/stdio/i)).toBeNull();
  });

  it("has no tools cache TTL field", () => {
    render(<MCPConfigTab mcp={storedMcp} onChange={vi.fn()} />);

    expect(screen.queryByLabelText(/Cache TTL/)).toBeNull();
  });

  it("adds an HTTP server without a cache TTL", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<MCPConfigTab mcp={{ enabled: true }} onChange={onChange} />);

    await user.type(screen.getByLabelText(/Server ID/), "tools");
    await user.type(screen.getByLabelText(/URL/), "http://tools:3001/mcp");
    await user.click(screen.getByRole("button", { name: "Add Server" }));

    expect(onChange).toHaveBeenCalledWith({
      enabled: true,
      servers: {
        tools: { url: "http://tools:3001/mcp", transport: "http" },
      },
    });
  });

  it("shows whether a stored server's credential is set, never its value", () => {
    render(<MCPConfigTab mcp={storedMcp} onChange={vi.fn()} />);

    expect(screen.getByText("Credential: set")).toBeInTheDocument();
    expect(screen.getByText("Credential: not set")).toBeInTheDocument();
    expect(document.body.innerHTML).not.toContain(STORED_TOKEN);
  });
});
