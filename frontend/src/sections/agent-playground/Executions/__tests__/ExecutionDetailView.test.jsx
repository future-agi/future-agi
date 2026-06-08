import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import ExecutionDetailView from "../ExecutionDetailView";

const mockNavigate = vi.fn();
const mockRecordActivationEvent = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));

vi.mock("src/api/agent-playground/agent-playground", () => ({
  useGetExecutionDetail: () => ({
    data: {
      id: "execution-1",
      status: "success",
      nodes: [
        {
          id: "agent-node-1",
          nodeExecution: { id: "node-execution-1", status: "success" },
        },
      ],
    },
    isLoading: false,
    isError: false,
  }),
}));

vi.mock("src/sections/onboarding-home/hooks/useRecordActivationEvent", () => ({
  useRecordActivationEvent: () => ({ mutate: mockRecordActivationEvent }),
}));

vi.mock("../../hooks/useResolvedExecution", () => ({
  default: ({ selectedNodeId, executionId }) => ({
    nodeExecutionId: selectedNodeId ? "node-execution-1" : null,
    resolvedExecutionId: executionId,
  }),
}));

vi.mock("src/components/AgentGraph", () => ({
  AgentGraph: () => <div data-testid="agent-graph" />,
}));

vi.mock("src/components/resizablePanels/ResizablePanels", () => ({
  default: ({ leftPanel, rightPanel }) => (
    <div>
      <div data-testid="left-panel">{leftPanel}</div>
      <div data-testid="right-panel">{rightPanel}</div>
    </div>
  ),
}));

vi.mock("../../AgentBuilder/RunAgentPanel/NodeOutputDetail", () => ({
  default: ({ executionId, nodeExecutionId }) => (
    <div data-testid="node-output-detail">
      {executionId}:{nodeExecutionId}
    </div>
  ),
}));

describe("ExecutionDetailView onboarding", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("hands a reviewed agent run to eval coverage mode", async () => {
    render(
      <MemoryRouter
        initialEntries={[
          "/dashboard/agents/playground/agent-1/executions?version=version-1&onboarding=review-run&quick_start_goal=build_ai_agent&quick_start_id=agent&quick_start_primary_path=agent",
        ]}
      >
        <ExecutionDetailView graphId="agent-1" executionId="execution-1" />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(
        screen.getByText("Turn this run into agent coverage"),
      ).toBeVisible();
    });

    expect(mockRecordActivationEvent).toHaveBeenCalledWith(
      expect.objectContaining({
        eventName: "agent_trace_reviewed",
        quick_start_goal: "build_ai_agent",
        quick_start_id: "agent",
        quick_start_primary_path: "agent",
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: /add eval node/i }));

    expect(mockNavigate).toHaveBeenCalledWith(
      "/dashboard/agents/playground/agent-1/build?version=version-1&onboarding=add-eval&tour_anchor=agent_save_eval_button&journey_step=save_agent_eval&quick_start_goal=build_ai_agent&quick_start_id=agent&quick_start_primary_path=agent",
    );
  });
});
