import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import useConnectedNodeVariables from "../useConnectedNodeVariables";
import { useAgentPlaygroundStore } from "../../store";
import { NODE_TYPES } from "../../utils/constants";

let mockDatasetData = null;
let mockEdgeMappingsData = [];
let mockIncomingConnections = [];

vi.mock("react-router-dom", () => ({
  useParams: () => ({ agentId: "graph-1" }),
  useSearchParams: () => [new URLSearchParams("version=version-1")],
}));

vi.mock("@xyflow/react", () => ({
  useNodeConnections: () => mockIncomingConnections,
}));

vi.mock("src/api/agent-playground/agent-playground", () => ({
  useGetGraphDataset: () => ({
    data: mockDatasetData,
    isLoading: false,
  }),
}));

vi.mock("src/api/agent-playground/nodes", () => ({
  useGetPossibleEdgeMappings: () => ({
    data: mockEdgeMappingsData,
    isLoading: false,
  }),
}));

function wrapper({ children }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}

describe("useConnectedNodeVariables", () => {
  beforeEach(() => {
    mockDatasetData = null;
    mockEdgeMappingsData = [];
    mockIncomingConnections = [];
    useAgentPlaygroundStore.getState().reset();
  });

  it("normalizes backend snake_case possible-edge mappings", () => {
    mockIncomingConnections = [{ source: "source-node" }];
    mockEdgeMappingsData = [
      {
        source_node_name: "source_node",
        output_ports: [
          {
            direction: "output",
            display_name: "response",
            data_schema: { type: "object" },
          },
        ],
      },
    ];
    mockDatasetData = {
      columns: [{ id: "col-1", name: "topic" }],
      rows: [{ cells: [{ column_id: "col-1", value: "hello" }] }],
    };
    useAgentPlaygroundStore.setState({
      nodes: [
        {
          id: "source-node",
          type: NODE_TYPES.LLM_PROMPT,
          data: {
            label: "source_node",
            ports: [{ direction: "output", display_name: "response" }],
            config: { modelConfig: { responseFormat: "json" } },
          },
        },
      ],
    });

    const { result } = renderHook(
      () => useConnectedNodeVariables("target-node"),
      { wrapper },
    );

    expect(result.current.dropdownOptions).toEqual([
      { id: "source_node.response", value: "source_node.response" },
      { id: "topic", value: "topic" },
    ]);
    expect(result.current.validateVariable("source_node.response")).toBe(true);
    expect(result.current.validateVariable("source_node.response.value")).toBe(
      true,
    );
    expect(result.current.validateVariable("topic")).toBe(true);
  });
});
