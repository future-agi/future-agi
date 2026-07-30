import { describe, it, expect, beforeEach, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen } from "src/utils/test-utils";
import ToolGroupSelector, {
  normalizeMCPEnabledGroups,
  normalizeMCPToolGroups,
} from "../ToolGroupSelector";

const { mockMutate } = vi.hoisted(() => ({
  mockMutate: vi.fn(),
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

  it("saves selected groups using the backend field name", async () => {
    const user = userEvent.setup();
    render(<ToolGroupSelector config={backendConfig} />);

    await user.click(screen.getByRole("button", { name: /Tool Groups/i }));

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
});
