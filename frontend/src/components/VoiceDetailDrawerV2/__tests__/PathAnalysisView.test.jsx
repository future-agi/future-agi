import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "src/utils/test-utils";
import axios from "src/utils/axios";
import PathAnalysisView from "../PathAnalysisView";

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(),
  },
  endpoints: {
    testExecutions: {
      flowAnalysis: (id) => `/simulate/call-executions/${id}/branch-analysis/`,
      kpis: (id) => `/simulate/test-executions/${id}/kpis/`,
    },
  },
}));

vi.mock("src/components/GraphBuilder/GraphView", () => ({
  default: ({ nodes = [] }) => (
    <div data-testid="path-analysis-graph">
      {nodes.map((node) => node?.data?.name || node?.id).join(", ")}
    </div>
  ),
}));

const renderWithQueryClient = (ui) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
};

const pathAnalysisData = {
  transcript: [
    {
      role: "assistant",
      content: "Hello, welcome. What would you like to order?",
      start: 0,
      end: 3,
    },
  ],
  scenario_graph: {
    nodes: [
      {
        name: "Greeting",
        type: "conversation",
        messagePlan: {
          firstMessage: "Hello, what would you like to order?",
        },
      },
      {
        name: "Confirm order",
        type: "conversation",
        messagePlan: { firstMessage: "Let me confirm your order." },
      },
    ],
    edges: [{ from: "Greeting", to: "Confirm order" }],
  },
};

describe("PathAnalysisView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("uses backend-provided simulation context instead of fetching KPI cache by route", async () => {
    axios.get.mockResolvedValueOnce({
      data: {
        analysis: {
          current_path: ["Greeting"],
          expected_path: ["Greeting", "Confirm order"],
          new_nodes: [],
          new_edges: [],
          analysis_summary: "Greeting was completed; confirmation was missed.",
        },
      },
    });

    renderWithQueryClient(
      <PathAnalysisView
        data={pathAnalysisData}
        scenarioId="scenario-1"
        openedExecutionId="call-1"
        testExecutionId="test-execution-1"
        enabled
        viewMode="checklist"
      />,
    );

    expect(await screen.findByText("Greeting")).toBeInTheDocument();
    expect(screen.getByText("Confirm order")).toBeInTheDocument();
    expect(screen.getByText("1/2 steps")).toBeInTheDocument();
    expect(screen.getByText("1 missed")).toBeInTheDocument();
    expect(
      screen.getByText("Greeting was completed; confirmation was missed."),
    ).toBeInTheDocument();

    await waitFor(() => {
      expect(axios.get).toHaveBeenCalledTimes(1);
      expect(axios.get).toHaveBeenCalledWith(
        "/simulate/call-executions/call-1/branch-analysis/",
      );
    });
  });

  it("does not fall back to transcript similarity when branch analysis fails", async () => {
    axios.get.mockRejectedValueOnce({ detail: "No branch analysis" });

    renderWithQueryClient(
      <PathAnalysisView
        data={pathAnalysisData}
        scenarioId="scenario-1"
        openedExecutionId="call-1"
        testExecutionId="test-execution-1"
        enabled
        viewMode="checklist"
      />,
    );

    expect(
      await screen.findByText(
        "This call does not have enough data to analyze its flow.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("2/2 steps")).not.toBeInTheDocument();
  });
});
