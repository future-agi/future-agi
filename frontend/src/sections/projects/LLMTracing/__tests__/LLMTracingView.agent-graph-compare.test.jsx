import React, { Suspense } from "react";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { mockUseAgentGraph } = vi.hoisted(() => ({
  mockUseAgentGraph: vi.fn(),
}));

vi.mock("src/api/project/agent-graph", () => ({
  useAgentGraph: mockUseAgentGraph,
}));

vi.mock("../GraphSection/AgentGraph", () => ({
  default: ({ data, isLoading, isError, pollingPaused }) => (
    <div
      data-testid="compare-agent-graph"
      data-loading={String(isLoading)}
      data-error={String(isError)}
      data-polling-paused={String(pollingPaused)}
    >
      {data?.nodes?.[0]?.id}
    </div>
  ),
}));

import { CompareAgentGraph } from "../LLMTracingView";
import { canonicalObserveViewMode } from "../viewMode";

const renderCompareAgentGraph = (props) =>
  render(
    <Suspense fallback={<div>Loading graph</div>}>
      <CompareAgentGraph {...props} />
    </Suspense>,
  );

describe("LLMTracingView compare Agent Graph project scope", () => {
  beforeEach(() => {
    mockUseAgentGraph.mockReset();
    mockUseAgentGraph.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
      pollingPaused: false,
    });
  });

  it("shows project guidance, disables the compare request, and mounts no graph without compare scope", () => {
    renderCompareAgentGraph({
      projectId: null,
      filters: [
        {
          column_id: "user_id",
          filter_config: {
            filter_op: "equals",
            filter_value: "user-1",
          },
        },
      ],
    });

    expect(screen.getByRole("status")).toHaveTextContent(
      "Select a project filter to use agent visualizations",
    );
    expect(mockUseAgentGraph).toHaveBeenCalledWith(
      null,
      expect.arrayContaining([
        expect.objectContaining({ column_id: "user_id" }),
      ]),
      { enabled: false },
    );
    expect(screen.queryByTestId("compare-agent-graph")).not.toBeInTheDocument();
    expect(
      screen.queryByText("No agent graph data available"),
    ).not.toBeInTheDocument();
  });

  it("requests and renders the compare graph with its resolved project and user filters", async () => {
    const compareFilters = [
      {
        column_id: "user_id",
        filter_config: {
          filter_op: "equals",
          filter_value: "user-1",
        },
      },
      {
        column_id: "status",
        filter_config: {
          filter_op: "equals",
          filter_value: "error",
        },
      },
    ];
    mockUseAgentGraph.mockReturnValue({
      data: { nodes: [{ id: "compare-node" }], edges: [] },
      isLoading: false,
      isError: false,
      pollingPaused: false,
    });

    renderCompareAgentGraph({
      projectId: "compare-project",
      filters: compareFilters,
    });

    expect(await screen.findByTestId("compare-agent-graph")).toHaveTextContent(
      "compare-node",
    );
    expect(mockUseAgentGraph).toHaveBeenCalled();
    expect(mockUseAgentGraph).toHaveBeenCalledWith(
      "compare-project",
      compareFilters,
      { enabled: true },
    );
  });

  it("keeps pending and error states distinct while Agent Graph remains the active view mode", () => {
    expect(
      canonicalObserveViewMode({
        viewMode: "agentGraph",
        isSimulator: false,
        agentGraphEnabled: true,
      }),
    ).toBe("agentGraph");
    mockUseAgentGraph.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      pollingPaused: false,
    });

    const { rerender } = renderCompareAgentGraph({
      projectId: "compare-project",
      filters: [],
    });
    expect(screen.getByTestId("compare-agent-graph")).toHaveAttribute(
      "data-loading",
      "true",
    );
    expect(screen.getByTestId("compare-agent-graph")).toHaveAttribute(
      "data-error",
      "false",
    );

    mockUseAgentGraph.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      pollingPaused: false,
    });
    rerender(
      <Suspense fallback={<div>Loading graph</div>}>
        <CompareAgentGraph projectId="compare-project" filters={[]} />
      </Suspense>,
    );

    expect(screen.getByTestId("compare-agent-graph")).toHaveAttribute(
      "data-loading",
      "false",
    );
    expect(screen.getByTestId("compare-agent-graph")).toHaveAttribute(
      "data-error",
      "true",
    );
  });
});
