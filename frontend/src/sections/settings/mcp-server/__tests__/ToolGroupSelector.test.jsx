import { describe, it, expect, beforeEach, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen } from "src/utils/test-utils";
import ToolGroupSelector, {
  normalizeMCPEnabledGroups,
  normalizeMCPToolGroups,
} from "../ToolGroupSelector";

const { mockMutate, workspace } = vi.hoisted(() => ({
  mockMutate: vi.fn(),
  workspace: { currentWorkspaceDisplayName: null, currentWorkspaceName: null },
}));

vi.mock("src/api/mcp", () => ({
  useUpdateMCPToolGroups: () => ({
    mutate: mockMutate,
    isPending: false,
  }),
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-testid="iconify" data-icon={icon} />,
}));

vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => workspace,
}));

const backendConfig = {
  tool_config: {
    enabled_groups: ["context"],
    available_groups: [
      {
        slug: "context",
        name: "Context & Navigation",
        description: "User profile and workspace context",
        enabled: true,
      },
      {
        slug: "evaluations",
        name: "Evaluations",
        description: "Composite evals and evaluation templates",
        enabled: false,
      },
    ],
  },
};

describe("ToolGroupSelector", () => {
  beforeEach(() => {
    mockMutate.mockReset();
    workspace.currentWorkspaceDisplayName = null;
    workspace.currentWorkspaceName = null;
    mockMutate.mockImplementation((payload, options) => {
      options?.onSuccess?.();
    });
  });

  it("normalizes the backend MCP config contract", () => {
    const groups = normalizeMCPToolGroups(backendConfig);

    expect(groups).toEqual([
      {
        id: "context",
        name: "Context & Navigation",
        description: "User profile and workspace context",
      },
      {
        id: "evaluations",
        name: "Evaluations",
        description: "Composite evals and evaluation templates",
      },
    ]);
    expect(normalizeMCPEnabledGroups(backendConfig, groups)).toEqual([
      "context",
    ]);
  });

  it.each([
    { tool_config: { enabled_groups: ["users", "context", "docs"] } },
    { enabled_groups: ["users", "context", "docs"] },
    { enabled_tool_groups: ["users", "context", "docs"] },
  ])(
    "drops unavailable saved groups from supported config envelopes: %j",
    (config) => {
      expect(
        normalizeMCPEnabledGroups(
          config,
          normalizeMCPToolGroups(backendConfig),
        ),
      ).toEqual(["context"]);
    },
  );

  it.each([{ enabled_groups: [] }, { enabled_groups: ["users", "docs"] }])(
    "keeps an empty selection after cleanup: %j",
    ({ enabled_groups }) => {
      expect(
        normalizeMCPEnabledGroups(
          { enabled_groups },
          normalizeMCPToolGroups(backendConfig),
        ),
      ).toEqual([]);
    },
  );

  it("does not offer retired groups in the fallback catalog", () => {
    const ids = normalizeMCPToolGroups({}).map((group) => group.id);
    expect(ids).not.toContain("users");
    expect(ids).not.toContain("docs");
  });

  it("saves an old selection without retired groups or enabling unselected groups", async () => {
    const user = userEvent.setup();
    const config = {
      tool_config: {
        enabled_groups: [
          "context",
          "users",
          "datasets",
          "docs",
          "observability",
        ],
        available_groups: [
          { slug: "context", name: "Context" },
          { slug: "datasets", name: "Datasets" },
          { slug: "observability", name: "Observability" },
          { slug: "gateway", name: "Gateway" },
        ],
      },
    };
    render(<ToolGroupSelector config={config} />);
    await user.click(
      screen.getByRole("button", { name: /Your editor's tools/i }),
    );
    const switches = screen.getAllByRole("checkbox");
    expect(switches).toHaveLength(4);
    expect(switches[3]).not.toBeChecked();
    await user.click(switches[1]);
    await user.click(screen.getByRole("button", { name: /Save changes/i }));
    expect(mockMutate).toHaveBeenCalledWith(
      { enabled_groups: ["context", "observability"] },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
  });

  it("saves selected groups using the backend field name", async () => {
    const user = userEvent.setup();
    render(<ToolGroupSelector config={backendConfig} />);

    await user.click(
      screen.getByRole("button", { name: /Your editor's tools/i }),
    );

    const switches = screen.getAllByRole("checkbox");
    expect(switches).toHaveLength(2);
    expect(switches[0]).toBeChecked();
    expect(switches[1]).not.toBeChecked();

    await user.click(switches[1]);
    await user.click(screen.getByRole("button", { name: /Save changes/i }));

    expect(mockMutate).toHaveBeenCalledWith(
      { enabled_groups: ["context", "evaluations"] },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
  });

  it("names the section as the signed-in person's own editor in the current workspace", () => {
    workspace.currentWorkspaceDisplayName = "Acme Research";
    render(<ToolGroupSelector config={backendConfig} />);

    const summary = screen.getByRole("button", {
      name: /Your editor's tools in Acme Research/,
    });
    expect(summary).toHaveTextContent(
      "Only affects your own editor. It does not change other members' editors.",
    );
    expect(
      screen.queryByText(/Restrict which tools are available/i),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Tool Groups")).not.toBeInTheDocument();
  });

  it("falls back to the workspace's name, then to 'this workspace'", () => {
    workspace.currentWorkspaceName = "acme-research";
    const { unmount } = render(<ToolGroupSelector config={backendConfig} />);
    expect(
      screen.getByText("Your editor's tools in acme-research"),
    ).toBeInTheDocument();
    unmount();

    workspace.currentWorkspaceName = null;
    render(<ToolGroupSelector config={backendConfig} />);
    expect(
      screen.getByText("Your editor's tools in this workspace"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Only affects your own editor. It does not change other members' editors.",
      ),
    ).toBeInTheDocument();
  });
});
