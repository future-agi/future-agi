import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import NodeSelectionPanel from "../NodeSelectionPanel";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------
const mockAddNode = vi.fn();
vi.mock("../hooks/useAddNodeOptimistic", () => ({
  default: () => ({ addNode: mockAddNode }),
}));

const mockSetCenter = vi.fn();
vi.mock("@xyflow/react", () => ({
  useReactFlow: () => ({ setCenter: mockSetCenter, getZoom: () => 1 }),
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
];

const referenceableState = {
  data: [],
  isLoading: false,
  isFetching: false,
  isError: false,
};
const mockRefetch = vi.fn();

vi.mock("src/api/agent-playground/agent-playground", () => ({
  useGetNodeTemplates: () => ({ data: mockTemplateNodes, isLoading: false }),
  useGetReferenceableGraphs: () => ({
    ...referenceableState,
    refetch: mockRefetch,
  }),
}));

vi.mock("../../store", () => ({
  useAgentPlaygroundStoreShallow: () => ({ currentAgent: { id: "agent-1" } }),
}));

vi.mock("../../components/NodeCard", () => ({
  default: ({ node }) => <span>{node.title}</span>,
}));

const setReferenceable = (overrides) => {
  Object.assign(referenceableState, {
    data: [],
    isLoading: false,
    isFetching: false,
    isError: false,
    ...overrides,
  });
};

// ---------------------------------------------------------------------------
// Tests — TH-4549: Agent node discoverable in the sidebar regardless of
// whether eligible agents exist; insertion stays non-mutating until they do.
// ---------------------------------------------------------------------------
describe("NodeSelectionPanel — Agent node discoverability (TH-4549)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setReferenceable();
  });

  it("lists the Agent node even when no eligible agents exist (regression)", () => {
    render(<NodeSelectionPanel width={240} />);

    expect(screen.getByTestId("sidebar-node-llm_prompt")).toBeInTheDocument();
    expect(screen.getByTestId("sidebar-node-agent")).toBeInTheDocument();
    expect(screen.getByText("Agent Node")).toBeInTheDocument();
  });

  it("lists the Agent node while eligibility is still loading", () => {
    setReferenceable({ data: undefined, isLoading: true });
    render(<NodeSelectionPanel width={240} />);

    expect(screen.getByTestId("sidebar-node-agent")).toBeInTheDocument();
  });

  it("clicking Agent with no eligible agents opens setup without adding a node", () => {
    render(<NodeSelectionPanel width={240} />);

    fireEvent.click(screen.getByTestId("sidebar-node-agent"));

    expect(mockAddNode).not.toHaveBeenCalled();
    expect(screen.getByTestId("agent-node-setup-dialog")).toBeInTheDocument();
    expect(
      screen.getByText("No agents available to reference"),
    ).toBeInTheDocument();
  });

  it("is not draggable onto the canvas while no eligible agents exist", () => {
    render(<NodeSelectionPanel width={240} />);

    expect(screen.getByTestId("sidebar-node-agent")).toHaveAttribute(
      "draggable",
      "false",
    );
    expect(screen.getByTestId("sidebar-node-llm_prompt")).toHaveAttribute(
      "draggable",
      "true",
    );
  });

  it("becomes draggable and inserts directly once eligible agents exist", () => {
    setReferenceable({ data: [{ id: "other-agent" }] });
    render(<NodeSelectionPanel width={240} />);

    expect(screen.getByTestId("sidebar-node-agent")).toHaveAttribute(
      "draggable",
      "true",
    );

    fireEvent.click(screen.getByTestId("sidebar-node-agent"));

    expect(mockAddNode).toHaveBeenCalledWith({
      type: "agent",
      position: undefined,
      node_template_id: undefined,
    });
    expect(
      screen.queryByTestId("agent-node-setup-dialog"),
    ).not.toBeInTheDocument();
  });

  it("inserts the pending Agent node after a refresh finds eligible agents", async () => {
    mockRefetch.mockResolvedValueOnce({ data: [{ id: "now-published" }] });
    mockAddNode.mockResolvedValueOnce({ position: { x: 10, y: 20 } });
    render(<NodeSelectionPanel width={240} />);
    fireEvent.click(screen.getByTestId("sidebar-node-agent"));

    fireEvent.click(screen.getByTestId("agent-node-setup-refresh"));

    await waitFor(() =>
      expect(mockAddNode).toHaveBeenCalledWith({
        type: "agent",
        position: undefined,
        node_template_id: undefined,
      }),
    );
    await waitFor(() =>
      expect(mockSetCenter).toHaveBeenCalledWith(310, 20, {
        duration: 800,
        zoom: 1,
      }),
    );
  });

  it("keeps the dialog open and adds nothing when a refresh is still empty", async () => {
    mockRefetch.mockResolvedValueOnce({ data: [] });
    render(<NodeSelectionPanel width={240} />);
    fireEvent.click(screen.getByTestId("sidebar-node-agent"));

    fireEvent.click(screen.getByTestId("agent-node-setup-refresh"));

    await waitFor(() => expect(mockRefetch).toHaveBeenCalledTimes(1));
    expect(mockAddNode).not.toHaveBeenCalled();
    expect(screen.getByTestId("agent-node-setup-dialog")).toBeInTheDocument();
  });

  it("does nothing when the panel is disabled", () => {
    render(<NodeSelectionPanel width={240} disabled />);

    fireEvent.click(screen.getByTestId("sidebar-node-agent"));

    expect(mockAddNode).not.toHaveBeenCalled();
    expect(
      screen.queryByTestId("agent-node-setup-dialog"),
    ).not.toBeInTheDocument();
  });

  it("other node types insert immediately regardless of Agent eligibility", () => {
    render(<NodeSelectionPanel width={240} />);

    fireEvent.click(screen.getByTestId("sidebar-node-llm_prompt"));

    expect(mockAddNode).toHaveBeenCalledWith({
      type: "llm_prompt",
      position: undefined,
      node_template_id: "tpl-1",
    });
  });
});
