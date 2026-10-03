import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import NodeSelectionPopper from "../NodeSelectionPopper";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------
const mockAddNode = vi.fn();
vi.mock("../../AgentBuilder/hooks/useAddNodeOptimistic", () => ({
  default: () => ({ addNode: mockAddNode }),
}));

const mockTemplateNodes = [
  {
    id: "llm_prompt",
    node_template_id: "tpl-1",
    title: "LLM Prompt",
    description: "Run a prompt against an LLM",
    iconSrc: "/assets/icons/ic_chat_single.svg",
    color: "orange.500",
  },
  {
    id: "eval",
    node_template_id: "tpl-2",
    title: "Eval Node",
    description: "Run an evaluation",
    iconSrc: "/assets/icons/ic_eval.svg",
    color: "green.500",
  },
];

// Referenceable-graphs state is configurable per test (TH-4549): the Agent node
// must be listed regardless, and insertion is deferred when none are eligible.
const referenceableState = {
  data: [{ id: "other-agent" }],
  isLoading: false,
  isFetching: false,
  isError: false,
  error: null,
};
const mockRefetch = vi.fn();

vi.mock("src/api/agent-playground/agent-playground", () => ({
  useGetNodeTemplates: () => ({ data: mockTemplateNodes }),
  useGetReferenceableGraphs: () => ({
    ...referenceableState,
    refetch: mockRefetch,
  }),
}));

vi.mock("../../store", () => ({
  useAgentPlaygroundStoreShallow: () => ({ currentAgent: { id: "agent-1" } }),
}));

const setReferenceable = (overrides) => {
  Object.assign(referenceableState, {
    data: [{ id: "other-agent" }],
    isLoading: false,
    isFetching: false,
    isError: false,
    error: null,
    ...overrides,
  });
};

vi.mock("../../utils/constants", async () => {
  const actual = await vi.importActual("../../utils/constants");
  return {
    ...actual,
    AGENT_NODE: {
      id: "agent",
      title: "Agent Node",
      description: "Run an agent through LLM",
      iconSrc: "/assets/icons/navbar/ic_agents.svg",
      color: "blue.600",
    },
  };
});

// NodeCard mock: mirrors real behaviour — LLM_PROMPT triggers onExpandClick,
// all others trigger onNodeClick.
vi.mock("../NodeCard", () => ({
  default: ({ node, onNodeClick, onExpandClick, showExpandIcon }) => {
    const isPrompt = node.id === "llm_prompt" && showExpandIcon;
    return (
      <button
        data-testid={`node-card-${node.id}`}
        onClick={(e) => {
          if (isPrompt && onExpandClick) {
            onExpandClick(e);
          } else if (onNodeClick) {
            onNodeClick(node.id, node.node_template_id);
          }
        }}
      >
        {node.title}
      </button>
    );
  },
}));

// PromptNodePopper mock: renders only when open
vi.mock("../PromptNodePopper", () => ({
  default: ({ open, onNodeSelect, onClose }) =>
    open ? (
      <div data-testid="prompt-node-popper">
        <button
          data-testid="prompt-select-btn"
          onClick={() => onNodeSelect("llm_prompt", "tpl-1", { name: "Test" })}
        >
          Select prompt
        </button>
        <button data-testid="prompt-close-btn" onClick={onClose}>
          Close
        </button>
      </div>
    ) : null,
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe("NodeSelectionPopper", () => {
  const anchorEl = document.createElement("div");
  const defaultProps = {
    open: true,
    anchorEl,
    onClose: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    setReferenceable();
  });

  // -----------------------------------------------------------------------
  // Rendering
  // -----------------------------------------------------------------------
  it("renders template nodes plus the agent node when open", () => {
    render(<NodeSelectionPopper {...defaultProps} />);

    expect(screen.getByTestId("node-card-llm_prompt")).toBeInTheDocument();
    expect(screen.getByTestId("node-card-eval")).toBeInTheDocument();
    expect(screen.getByTestId("node-card-agent")).toBeInTheDocument();
  });

  it("does not render node cards when closed", () => {
    render(<NodeSelectionPopper {...defaultProps} open={false} />);

    expect(
      screen.queryByTestId("node-card-llm_prompt"),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId("node-card-eval")).not.toBeInTheDocument();
    expect(screen.queryByTestId("node-card-agent")).not.toBeInTheDocument();
  });

  // -----------------------------------------------------------------------
  // Non-LLM node click — with onNodeSelect callback
  // -----------------------------------------------------------------------
  it("calls onNodeSelect when provided and a non-LLM node is clicked", () => {
    const onNodeSelect = vi.fn();
    render(
      <NodeSelectionPopper {...defaultProps} onNodeSelect={onNodeSelect} />,
    );

    fireEvent.click(screen.getByTestId("node-card-eval"));

    expect(onNodeSelect).toHaveBeenCalledWith("eval", "tpl-2");
    expect(mockAddNode).not.toHaveBeenCalled();
  });

  // -----------------------------------------------------------------------
  // Non-LLM node click — without onNodeSelect (uses addNode)
  // -----------------------------------------------------------------------
  it("calls addNode when onNodeSelect is not provided and a non-LLM node is clicked", () => {
    render(<NodeSelectionPopper {...defaultProps} />);

    fireEvent.click(screen.getByTestId("node-card-eval"));

    expect(mockAddNode).toHaveBeenCalledWith({
      type: "eval",
      position: undefined,
      node_template_id: "tpl-2",
    });
  });

  it("calls onClose after clicking a non-LLM node", () => {
    render(<NodeSelectionPopper {...defaultProps} />);

    fireEvent.click(screen.getByTestId("node-card-eval"));

    expect(defaultProps.onClose).toHaveBeenCalled();
  });

  // -----------------------------------------------------------------------
  // LLM_PROMPT node click — opens PromptNodePopper
  // -----------------------------------------------------------------------
  it("opens PromptNodePopper when an LLM_PROMPT node is clicked", () => {
    render(<NodeSelectionPopper {...defaultProps} />);

    // PromptNodePopper should not be visible initially
    expect(screen.queryByTestId("prompt-node-popper")).not.toBeInTheDocument();

    // Click the llm_prompt card — triggers onExpandClick, which opens PromptNodePopper
    fireEvent.click(screen.getByTestId("node-card-llm_prompt"));

    expect(screen.getByTestId("prompt-node-popper")).toBeInTheDocument();
  });

  it("does not call addNode or onNodeSelect when LLM_PROMPT node is clicked", () => {
    const onNodeSelect = vi.fn();
    render(
      <NodeSelectionPopper {...defaultProps} onNodeSelect={onNodeSelect} />,
    );

    fireEvent.click(screen.getByTestId("node-card-llm_prompt"));

    expect(mockAddNode).not.toHaveBeenCalled();
    expect(onNodeSelect).not.toHaveBeenCalled();
  });

  // -----------------------------------------------------------------------
  // PromptNodePopper interaction — with onNodeSelect
  // -----------------------------------------------------------------------
  it("delegates to onNodeSelect from PromptNodePopper when onNodeSelect is provided", async () => {
    const onNodeSelect = vi.fn();
    render(
      <NodeSelectionPopper {...defaultProps} onNodeSelect={onNodeSelect} />,
    );

    // Open the prompt popper
    fireEvent.click(screen.getByTestId("node-card-llm_prompt"));

    // Select a prompt node inside the popper
    fireEvent.click(screen.getByTestId("prompt-select-btn"));

    expect(onNodeSelect).toHaveBeenCalledWith("llm_prompt", "tpl-1", {
      name: "Test",
    });
  });

  // -----------------------------------------------------------------------
  // PromptNodePopper interaction — without onNodeSelect (uses addNode)
  // -----------------------------------------------------------------------
  it("calls addNode from PromptNodePopper when onNodeSelect is not provided", () => {
    render(<NodeSelectionPopper {...defaultProps} />);

    // Open the prompt popper
    fireEvent.click(screen.getByTestId("node-card-llm_prompt"));

    // Select a prompt node inside the popper
    fireEvent.click(screen.getByTestId("prompt-select-btn"));

    expect(mockAddNode).toHaveBeenCalledWith({
      type: "llm_prompt",
      position: undefined,
      node_template_id: "tpl-1",
      name: "Test",
      config: { name: "Test" },
    });
  });

  // -----------------------------------------------------------------------
  // Agent node click
  // -----------------------------------------------------------------------
  it("calls addNode for agent node with no node_template_id", () => {
    render(<NodeSelectionPopper {...defaultProps} />);

    fireEvent.click(screen.getByTestId("node-card-agent"));

    expect(mockAddNode).toHaveBeenCalledWith({
      type: "agent",
      position: undefined,
      node_template_id: undefined,
    });
    expect(
      screen.queryByTestId("agent-node-setup-dialog"),
    ).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// TH-4549 — Agent node discoverability when no eligible agents exist
// ---------------------------------------------------------------------------
describe("NodeSelectionPopper — Agent node without eligible agents (TH-4549)", () => {
  // Attached like the real "+" / "Add first node" anchors (isConnected === true).
  const anchorEl = document.body.appendChild(document.createElement("div"));
  const defaultProps = {
    open: true,
    anchorEl,
    onClose: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    setReferenceable({ data: [] });
  });

  it("still lists the Agent node when referenceable graphs are empty (regression)", () => {
    render(<NodeSelectionPopper {...defaultProps} />);

    expect(screen.getByTestId("node-card-agent")).toBeInTheDocument();
    expect(screen.getAllByTestId(/^node-card-/)).toHaveLength(3);
  });

  it("still lists the Agent node while referenceable graphs are loading", () => {
    setReferenceable({ data: undefined, isLoading: true });
    render(<NodeSelectionPopper {...defaultProps} />);

    expect(screen.getByTestId("node-card-agent")).toBeInTheDocument();
  });

  it("opens the non-mutating setup dialog instead of adding a node", () => {
    render(<NodeSelectionPopper {...defaultProps} />);

    fireEvent.click(screen.getByTestId("node-card-agent"));

    expect(mockAddNode).not.toHaveBeenCalled();
    expect(screen.getByTestId("agent-node-setup-dialog")).toBeInTheDocument();
    expect(
      screen.getByText("No eligible agents available here"),
    ).toBeInTheDocument();
    expect(defaultProps.onClose).toHaveBeenCalled();
  });

  it("does not call onNodeSelect either when agents are unavailable", () => {
    const onNodeSelect = vi.fn();
    render(
      <NodeSelectionPopper {...defaultProps} onNodeSelect={onNodeSelect} />,
    );

    fireEvent.click(screen.getByTestId("node-card-agent"));

    expect(onNodeSelect).not.toHaveBeenCalled();
    expect(screen.getByTestId("agent-node-setup-dialog")).toBeInTheDocument();
  });

  it("offers a real same-origin new-tab link to the Agent Playground and never names other agents", () => {
    render(<NodeSelectionPopper {...defaultProps} />);
    fireEvent.click(screen.getByTestId("node-card-agent"));

    const link = screen.getByTestId("agent-node-setup-open-agents");
    expect(link.tagName).toBe("A");
    expect(link).toHaveAttribute("href", "/dashboard/agents");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveTextContent(/new tab/i);
    expect(screen.getByTestId("agent-node-setup-body").textContent).not.toMatch(
      /other-agent|\d+ agents?|organization has no agents|admin/i,
    );
  });

  it("Refresh agents only refetches — it never inserts, even when the result is nonempty", async () => {
    mockRefetch.mockImplementationOnce(async () => {
      setReferenceable({ data: [{ id: "now-published" }] });
      return { data: [{ id: "now-published" }] };
    });
    render(<NodeSelectionPopper {...defaultProps} />);
    fireEvent.click(screen.getByTestId("node-card-agent"));

    fireEvent.click(screen.getByTestId("agent-node-setup-refresh"));

    await waitFor(() => expect(mockRefetch).toHaveBeenCalledTimes(1));
    expect(mockAddNode).not.toHaveBeenCalled();
    expect(screen.getByTestId("agent-node-setup-dialog")).toBeInTheDocument();
  });

  it("shows READY copy and enables Add Agent node once eligibility is confirmed nonempty", async () => {
    const { rerender } = render(<NodeSelectionPopper {...defaultProps} />);
    fireEvent.click(screen.getByTestId("node-card-agent"));
    expect(screen.getByTestId("agent-node-setup-add")).toBeDisabled();

    setReferenceable({ data: [{ id: "now-published" }] });
    rerender(<NodeSelectionPopper {...defaultProps} />);

    expect(
      screen.getByText("Eligible agents are available"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("agent-node-setup-add")).toBeEnabled();
    expect(mockAddNode).not.toHaveBeenCalled();
  });

  it("explicit Add Agent node inserts the retained request exactly once and closes", async () => {
    const { rerender } = render(<NodeSelectionPopper {...defaultProps} />);
    fireEvent.click(screen.getByTestId("node-card-agent"));
    setReferenceable({ data: [{ id: "now-published" }] });
    rerender(<NodeSelectionPopper {...defaultProps} />);

    const add = screen.getByTestId("agent-node-setup-add");
    fireEvent.click(add);
    fireEvent.click(add);

    await waitFor(() =>
      expect(mockAddNode).toHaveBeenCalledWith({
        type: "agent",
        position: undefined,
        node_template_id: undefined,
      }),
    );
    expect(mockAddNode).toHaveBeenCalledTimes(1);
    await waitFor(() =>
      expect(
        screen.queryByTestId("agent-node-setup-dialog"),
      ).not.toBeInTheDocument(),
    );
  });

  it("routes the deferred Add through the parent's *latest* onNodeSelect", async () => {
    const stale = vi.fn();
    const fresh = vi.fn();
    const { rerender } = render(
      <NodeSelectionPopper {...defaultProps} onNodeSelect={stale} />,
    );
    fireEvent.click(screen.getByTestId("node-card-agent"));

    setReferenceable({ data: [{ id: "now-published" }] });
    rerender(<NodeSelectionPopper {...defaultProps} onNodeSelect={fresh} />);
    fireEvent.click(screen.getByTestId("agent-node-setup-add"));

    await waitFor(() => expect(fresh).toHaveBeenCalledWith("agent", undefined));
    expect(stale).not.toHaveBeenCalled();
    expect(mockAddNode).not.toHaveBeenCalled();
  });

  it("cancels the deferred Add when the originating node/edge anchor is gone", async () => {
    const { enqueueSnackbar } = await import("notistack");
    const detached = document.createElement("button"); // never appended: isConnected === false
    const onNodeSelect = vi.fn();
    const { rerender } = render(
      <NodeSelectionPopper
        {...defaultProps}
        anchorEl={detached}
        onNodeSelect={onNodeSelect}
      />,
    );
    fireEvent.click(screen.getByTestId("node-card-agent"));
    setReferenceable({ data: [{ id: "now-published" }] });
    rerender(
      <NodeSelectionPopper
        {...defaultProps}
        anchorEl={detached}
        onNodeSelect={onNodeSelect}
      />,
    );

    fireEvent.click(screen.getByTestId("agent-node-setup-add"));

    await waitFor(() => expect(enqueueSnackbar).toHaveBeenCalled());
    expect(onNodeSelect).not.toHaveBeenCalled();
    expect(mockAddNode).not.toHaveBeenCalled();
  });

  it("keeps the dialog open and adds nothing when a refresh is still empty", async () => {
    mockRefetch.mockResolvedValueOnce({ data: [] });
    render(<NodeSelectionPopper {...defaultProps} />);
    fireEvent.click(screen.getByTestId("node-card-agent"));

    fireEvent.click(screen.getByTestId("agent-node-setup-refresh"));

    await waitFor(() => expect(mockRefetch).toHaveBeenCalledTimes(1));
    expect(mockAddNode).not.toHaveBeenCalled();
    expect(screen.getByTestId("agent-node-setup-dialog")).toBeInTheDocument();
    expect(screen.getByTestId("agent-node-setup-add")).toBeDisabled();
  });

  it("drops the pending insert when the user cancels", async () => {
    render(<NodeSelectionPopper {...defaultProps} />);
    fireEvent.click(screen.getByTestId("node-card-agent"));

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() =>
      expect(
        screen.queryByTestId("agent-node-setup-dialog"),
      ).not.toBeInTheDocument(),
    );
    expect(mockAddNode).not.toHaveBeenCalled();
  });

  it("shows a distinct error state with Retry (not empty-state advice) when the request failed", () => {
    setReferenceable({
      data: undefined,
      isError: true,
      error: { response: { status: 500 } },
    });
    render(<NodeSelectionPopper {...defaultProps} />);

    fireEvent.click(screen.getByTestId("node-card-agent"));

    expect(mockAddNode).not.toHaveBeenCalled();
    expect(
      screen.getByText("Could not load eligible agents"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("agent-node-setup-open-agents"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("agent-node-setup-add"),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("renders 403 as an access failure, never as empty", () => {
    setReferenceable({
      data: undefined,
      isError: true,
      error: { response: { status: 403 } },
    });
    render(<NodeSelectionPopper {...defaultProps} />);

    fireEvent.click(screen.getByTestId("node-card-agent"));

    expect(
      screen.getByText("You cannot access eligible agents in this context"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("No eligible agents available here"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("agent-node-setup-open-agents"),
    ).not.toBeInTheDocument();
  });

  it("renders 404 as current-agent-unavailable without existence details", () => {
    setReferenceable({
      data: undefined,
      isError: true,
      error: { response: { status: 404 } },
    });
    render(<NodeSelectionPopper {...defaultProps} />);

    fireEvent.click(screen.getByTestId("node-card-agent"));

    expect(screen.getByText("Current agent unavailable")).toBeInTheDocument();
    expect(screen.getByTestId("agent-node-setup-body").textContent).not.toMatch(
      /agent-1|exists|other-agent/,
    );
  });

  it("does not insert while eligibility is still loading", () => {
    setReferenceable({ data: undefined, isLoading: true });
    render(<NodeSelectionPopper {...defaultProps} />);

    fireEvent.click(screen.getByTestId("node-card-agent"));

    expect(mockAddNode).not.toHaveBeenCalled();
    expect(screen.getByText("Loading eligible agents...")).toBeInTheDocument();
    expect(
      screen.queryByTestId("agent-node-setup-refresh"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("agent-node-setup-add"),
    ).not.toBeInTheDocument();
  });

  it("leaves non-Agent nodes unaffected by the Agent eligibility gate", () => {
    render(<NodeSelectionPopper {...defaultProps} />);

    fireEvent.click(screen.getByTestId("node-card-eval"));

    expect(mockAddNode).toHaveBeenCalledWith({
      type: "eval",
      position: undefined,
      node_template_id: "tpl-2",
    });
    expect(
      screen.queryByTestId("agent-node-setup-dialog"),
    ).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Edge-case tests
// ---------------------------------------------------------------------------
describe("NodeSelectionPopper — edge cases", () => {
  const anchorEl = document.createElement("div");
  const defaultProps = {
    open: true,
    anchorEl,
    onClose: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("handles rapid successive clicks on a non-LLM node", () => {
    render(<NodeSelectionPopper {...defaultProps} />);

    const card = screen.getByTestId("node-card-eval");

    fireEvent.click(card);
    fireEvent.click(card);

    // First click closes the popper (handleMainClose), so the second click
    // may or may not register depending on render. At minimum the first fires.
    expect(mockAddNode).toHaveBeenCalled();
  });

  it("handles rapid successive clicks on LLM_PROMPT without triggering addNode", () => {
    const onNodeSelect = vi.fn();
    render(
      <NodeSelectionPopper {...defaultProps} onNodeSelect={onNodeSelect} />,
    );

    const card = screen.getByTestId("node-card-llm_prompt");
    fireEvent.click(card);
    fireEvent.click(card);

    expect(mockAddNode).not.toHaveBeenCalled();
    expect(onNodeSelect).not.toHaveBeenCalled();
  });

  it("closes PromptNodePopper via its onClose callback", () => {
    render(<NodeSelectionPopper {...defaultProps} />);

    // Open the prompt popper
    fireEvent.click(screen.getByTestId("node-card-llm_prompt"));
    expect(screen.getByTestId("prompt-node-popper")).toBeInTheDocument();

    // Close it
    fireEvent.click(screen.getByTestId("prompt-close-btn"));
    expect(screen.queryByTestId("prompt-node-popper")).not.toBeInTheDocument();
  });

  it("renders all three nodes in order (templates + agent)", () => {
    render(<NodeSelectionPopper {...defaultProps} />);

    const cards = screen.getAllByTestId(/^node-card-/);
    expect(cards).toHaveLength(3);
    // Templates come first, AGENT_NODE is appended last
    expect(cards[0]).toHaveAttribute("data-testid", "node-card-llm_prompt");
    expect(cards[1]).toHaveAttribute("data-testid", "node-card-eval");
    expect(cards[2]).toHaveAttribute("data-testid", "node-card-agent");
  });
});
