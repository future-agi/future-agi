import React from "react";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import MCPSetupPage from "../MCPSetupPage";

const { mcpConfig } = vi.hoisted(() => ({
  mcpConfig: { data: null, isLoading: false, isError: false },
}));

vi.mock("src/api/mcp", () => ({
  useMCPConfig: () => mcpConfig,
  useUpdateMCPToolGroups: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-testid="iconify" data-icon={icon} />,
}));

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

// A self-hosted deployment's API base, as config-global resolves it (runtime
// VITE_HOST_API). The trailing slash is how operators often write it.
vi.mock("src/config-global", async (importOriginal) => ({
  ...(await importOriginal()),
  HOST_API: "https://fi.selfhosted.example/",
}));

const toolConfig = {
  enabled_groups: ["context"],
  available_groups: [
    {
      slug: "context",
      name: "Context & Navigation",
      description: "User profile and workspace context",
      enabled: true,
    },
  ],
};

describe("Settings > MCP Server", () => {
  beforeEach(() => {
    mcpConfig.data = null;
    mcpConfig.isLoading = false;
    mcpConfig.isError = false;
  });

  it("shows the endpoint the server reports, in the URL row and every snippet", () => {
    mcpConfig.data = {
      mcp_url: "http://localhost:8100/mcp",
      tool_config: toolConfig,
    };
    const { container } = render(<MCPSetupPage />);
    expect(screen.getByRole("textbox", { name: "MCP server URL" })).toHaveValue(
      "http://localhost:8100/mcp",
    );
    expect(container.textContent).not.toMatch(/futureagi\.com/);
    expect(container.innerHTML).not.toMatch(/api\.futureagi\.com/);
  });

  it("falls back to this deployment's API base plus /mcp when the server reports no URL", () => {
    mcpConfig.data = { tool_config: toolConfig };
    const { container } = render(<MCPSetupPage />);
    expect(screen.getByRole("textbox", { name: "MCP server URL" })).toHaveValue(
      "https://fi.selfhosted.example/mcp",
    );
    expect(screen.getByText("Connect Your IDE")).toBeInTheDocument();
    // The snippet a user copies (the Cursor tab is first) names the same host.
    expect(container.textContent).toContain(
      '"url": "https://fi.selfhosted.example/mcp"',
    );
    expect(container.innerHTML).not.toMatch(/futureagi\.com/);
  });

  it("uses the same fallback for a null mcp_url", () => {
    mcpConfig.data = { mcp_url: null, tool_config: toolConfig };
    render(<MCPSetupPage />);
    expect(screen.getByRole("textbox", { name: "MCP server URL" })).toHaveValue(
      "https://fi.selfhosted.example/mcp",
    );
    expect(screen.queryByTestId("mcp-url-unknown")).toBeNull();
  });

  it("links to the bundled MCP server setup guide, not MCP tracing", () => {
    mcpConfig.data = {
      mcp_url: "http://localhost:8000/mcp",
      tool_config: toolConfig,
    };
    const { container } = render(<MCPSetupPage />);
    const docs = screen.getByRole("link", { name: "View Docs" });
    expect(docs).toHaveAttribute(
      "href",
      `${import.meta.env.BASE_URL}docs/mcp-server.html`,
    );
    expect(docs).toHaveAttribute("target", "_blank");
    expect(docs).toHaveAttribute("rel", "noopener noreferrer");
    expect(container.innerHTML).not.toMatch(/tracing\/auto\/mcp/);
  });
});
