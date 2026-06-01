import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import NodeSelectionPanel from "../NodeSelectionPanel";

const mockAddNode = vi.fn();
const mockSetCenter = vi.fn();
const mockSetSearchParams = vi.fn();
const mockRecordActivationEvent = vi.fn();

vi.mock("../hooks/useAddNodeOptimistic", () => ({
  default: () => ({ addNode: mockAddNode }),
}));

vi.mock("@xyflow/react", () => ({
  useReactFlow: () => ({
    getZoom: () => 1,
    setCenter: mockSetCenter,
  }),
}));

vi.mock("react-router-dom", () => ({
  useSearchParams: () => [
    new URLSearchParams(
      "quick_start_goal=build_ai_agent&quick_start_id=agent&quick_start_primary_path=agent",
    ),
    mockSetSearchParams,
  ],
}));

vi.mock("src/sections/onboarding-home/api/onboarding-home-api", () => ({
  recordActivationEvent: (...args) => mockRecordActivationEvent(...args),
}));

const mockTemplateNodes = [
  {
    id: "llm_prompt",
    node_template_id: "tpl-1",
    title: "LLM Prompt",
    description: "Run a prompt against an LLM",
  },
  {
    id: "eval",
    node_template_id: "tpl-2",
    title: "Eval Node",
    description: "Run an evaluation",
  },
];

vi.mock("src/api/agent-playground/agent-playground", () => ({
  useGetNodeTemplates: () => ({ data: mockTemplateNodes, isLoading: false }),
  useGetReferenceableGraphs: () => ({ data: [] }),
}));

vi.mock("../../store", () => ({
  useAgentPlaygroundStoreShallow: () => ({
    currentAgent: { id: "agent-1", version_id: "version-1" },
    nodes: [],
  }),
}));

vi.mock("../../components/NodeCard", () => ({
  default: ({ node }) => (
    <div data-testid={`node-card-${node.id}`}>{node.title}</div>
  ),
}));

describe("NodeSelectionPanel onboarding", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockAddNode.mockResolvedValue({
      nodeId: "node-1",
      position: { x: 100, y: 200 },
    });
    mockRecordActivationEvent.mockResolvedValue({});
  });

  it("renders starter-prompt guidance and advances to run-scenario after adding it", async () => {
    render(
      <NodeSelectionPanel
        width="240px"
        onboardingMode="run-scenario"
        tourAnchor="agent_add_node_button"
      />,
    );

    expect(screen.getByTestId("agent-onboarding-focus")).toBeVisible();
    expect(screen.getByText("Add a starter prompt")).toBeVisible();

    fireEvent.click(
      screen.getByRole("button", { name: /add starter prompt/i }),
    );

    await waitFor(() => {
      expect(mockAddNode).toHaveBeenCalledWith({
        type: "llm_prompt",
        position: undefined,
        node_template_id: "tpl-1",
        waitForApi: true,
        config: expect.objectContaining({
          modelConfig: expect.objectContaining({
            model: "gpt-4o-mini",
            responseFormat: "text",
          }),
          messages: expect.arrayContaining([
            expect.objectContaining({
              role: "user",
              content: expect.arrayContaining([
                expect.objectContaining({
                  text: expect.stringContaining("outdated pricing"),
                }),
              ]),
            }),
          ]),
        }),
      });
    });
    expect(mockRecordActivationEvent).toHaveBeenCalledWith(
      expect.objectContaining({
        artifactId: "node-1",
        artifactType: "agent_node",
        eventName: "agent_node_added",
        primaryPath: "agent",
        stage: "add_agent_node",
        metadata: {
          agent_id: "agent-1",
          node_id: "node-1",
          version_id: "version-1",
        },
        quick_start_goal: "build_ai_agent",
        quick_start_id: "agent",
        quick_start_primary_path: "agent",
      }),
    );
    expect(mockSetSearchParams).toHaveBeenCalledWith(expect.any(Function), {
      replace: true,
    });
    const nextParams = mockSetSearchParams.mock.calls[0][0](
      new URLSearchParams("tour_anchor=agent_add_node_button"),
    );
    expect(nextParams.get("journey_step")).toBe("run_agent_scenario");
    expect(nextParams.get("tour_anchor")).toBeNull();
  });

  it("uses the starter prompt flow when the first LLM Prompt card is clicked", async () => {
    render(
      <NodeSelectionPanel
        width="240px"
        onboardingMode="run-scenario"
        tourAnchor="agent_add_node_button"
      />,
    );

    fireEvent.click(screen.getByTestId("node-card-llm_prompt"));

    await waitFor(() => {
      expect(mockAddNode).toHaveBeenCalledWith(
        expect.objectContaining({
          type: "llm_prompt",
          waitForApi: true,
          config: expect.objectContaining({
            modelConfig: expect.objectContaining({
              model: "gpt-4o-mini",
            }),
          }),
        }),
      );
    });
    expect(mockSetSearchParams).toHaveBeenCalledWith(expect.any(Function), {
      replace: true,
    });
  });

  it("renders eval coverage guidance and adds an eval node from the primary action", async () => {
    render(<NodeSelectionPanel width="240px" onboardingMode="add-eval" />);

    expect(screen.getByTestId("agent-onboarding-focus")).toBeVisible();
    expect(
      screen.getByText("Add coverage from the reviewed run"),
    ).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: /add eval node/i }));

    await waitFor(() => {
      expect(mockAddNode).toHaveBeenCalledWith({
        type: "eval",
        position: undefined,
        node_template_id: "tpl-2",
        waitForApi: true,
      });
    });
    expect(mockRecordActivationEvent).toHaveBeenCalledWith(
      expect.objectContaining({
        artifactId: "node-1",
        artifactType: "agent_eval_node",
        eventName: "agent_scenario_saved_as_eval",
        primaryPath: "agent",
        stage: "save_agent_eval",
        metadata: {
          agent_id: "agent-1",
          eval_node_id: "node-1",
          version_id: "version-1",
        },
        quick_start_goal: "build_ai_agent",
        quick_start_id: "agent",
        quick_start_primary_path: "agent",
      }),
    );
    const nextParams = mockSetSearchParams.mock.calls[0][0](
      new URLSearchParams("journey_step=save_agent_eval"),
    );
    expect(nextParams.get("journey_step")).toBe("agent_create_eval");
    expect(nextParams.get("tour_anchor")).toBe("agent_create_eval_button");
    expect(mockSetCenter).toHaveBeenCalledWith(400, 200, {
      duration: 800,
      zoom: 1,
    });
  });

  it("keeps the panel hidden outside eval coverage onboarding", () => {
    render(<NodeSelectionPanel width="240px" />);

    expect(screen.queryByTestId("agent-onboarding-focus")).toBeNull();
  });
});
