import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen } from "src/utils/test-utils";
import axios from "src/utils/axios";
import { mcpKeys } from "src/api/mcp";
import MCPSetupPage from "../MCPSetupPage";

// The page reads its configuration through the real query hook, so these
// cases see the same loading and error flags the app sees. Only the HTTP
// client is replaced.
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), put: vi.fn() },
  endpoints: {
    mcp: {
      config: "/mcp/config/",
      toolGroups: "/mcp/config/tool-groups/",
      sessions: "/mcp/sessions/",
    },
  },
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-testid="iconify" data-icon={icon} />,
}));

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({
    currentWorkspaceDisplayName: "Acme Research",
    currentWorkspaceName: "acme-research",
  }),
}));

const configResponse = () => ({
  data: {
    status: true,
    result: {
      mcp_url: "http://localhost:8000/mcp",
      tool_config: {
        enabled_groups: ["context"],
        available_groups: [
          {
            slug: "context",
            name: "Context & Navigation",
            description: "User profile and workspace context",
            enabled: true,
          },
        ],
      },
    },
  },
});

let queryClient;

function mount() {
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MCPSetupPage />
    </QueryClientProvider>,
  );
}

// Reads the configuration again while the page is open, as a window focus,
// a reconnect or a tool group save does.
async function readConfigAgain() {
  await act(async () => {
    await queryClient.refetchQueries({ queryKey: mcpKeys.config() });
    // Query observers are notified on a zero-delay timer; let them render
    // before the assertions below read the page.
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

const editorTab = (name) => screen.getByRole("tab", { name });

beforeEach(() => {
  vi.clearAllMocks();
});

describe("Settings > MCP Server editor tabs", () => {
  it("keeps the chosen editor tab when the configuration is read again", async () => {
    axios.get.mockResolvedValue(configResponse());
    mount();

    fireEvent.click(await screen.findByRole("tab", { name: "VS Code" }));
    expect(editorTab("VS Code")).toHaveAttribute("aria-selected", "true");

    await readConfigAgain();

    expect(axios.get).toHaveBeenCalledTimes(2);
    expect(editorTab("VS Code")).toHaveAttribute("aria-selected", "true");
    expect(editorTab("Cursor")).toHaveAttribute("aria-selected", "false");
    expect(
      screen.getByText("One-click install for VS Code"),
    ).toBeInTheDocument();
  });

  it("keeps the guide and the chosen editor tab when a later read fails, and after it recovers", async () => {
    axios.get.mockResolvedValue(configResponse());
    mount();

    fireEvent.click(await screen.findByRole("tab", { name: "Windsurf" }));
    expect(editorTab("Windsurf")).toHaveAttribute("aria-selected", "true");

    // The configuration on screen is still good; a failed refresh must not
    // replace it with the load error.
    axios.get.mockRejectedValueOnce(new Error("Network Error"));
    await readConfigAgain();

    expect(axios.get).toHaveBeenCalledTimes(2);
    expect(
      screen.queryByText("Failed to load MCP server configuration"),
    ).toBeNull();
    expect(editorTab("Windsurf")).toHaveAttribute("aria-selected", "true");
    expect(
      screen.getByText("~/.codeium/windsurf/mcp_config.json"),
    ).toBeInTheDocument();

    await readConfigAgain();

    expect(axios.get).toHaveBeenCalledTimes(3);
    expect(editorTab("Windsurf")).toHaveAttribute("aria-selected", "true");
    expect(editorTab("Cursor")).toHaveAttribute("aria-selected", "false");
  });
});
