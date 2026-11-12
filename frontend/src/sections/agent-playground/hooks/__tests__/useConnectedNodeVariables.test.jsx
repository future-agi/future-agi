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

// Connects node "Source" to the node being edited, the way the canvas store
// and the possible-edge-mappings API describe it.
function connectSource({
  responseFormat = "json",
  outputs = ["response"],
} = {}) {
  mockIncomingConnections = [{ source: "source-node" }];
  mockEdgeMappingsData = [
    {
      source_node_name: "Source",
      output_ports: outputs.map((name) => ({
        direction: "output",
        display_name: name,
        data_schema: { type: responseFormat === "json" ? "object" : "string" },
      })),
    },
  ];
  useAgentPlaygroundStore.setState({
    nodes: [
      {
        id: "source-node",
        type: NODE_TYPES.LLM_PROMPT,
        data: {
          label: "Source",
          ports: [{ direction: "output", display_name: outputs[0] }],
          config: { modelConfig: { responseFormat } },
        },
      },
    ],
  });
}

function renderValidator() {
  const { result } = renderHook(
    () => useConnectedNodeVariables("target-node"),
    { wrapper },
  );
  return result.current.validateVariable;
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

  // FA-PLAYGROUND-VARIABLE-RED: "Answer the question: {{question}}" painted
  // {{question}} in error red, although saving makes it an input of this
  // agent and Run asks for its value. A plain name with no value source is
  // not an error: the validator returns null, so the editor leaves it plain.
  describe("a plain variable with no value source", () => {
    it("is left plain (null), not invalid, in a new prompt", () => {
      const validateVariable = renderValidator();

      expect(validateVariable("question")).toBeNull();
      // {{ question }}: the editor passes the text inside the braces as is.
      expect(validateVariable(" question ")).toBeNull();
    });

    it("is left plain (null) while its Variables value is empty", () => {
      mockDatasetData = {
        columns: [{ id: "col-q", name: "question" }],
        rows: [{ cells: [{ column_id: "col-q", value: "" }] }],
      };

      expect(renderValidator()("question")).toBeNull();
    });

    it("is left plain (null) next to a connected node", () => {
      connectSource();

      expect(renderValidator()("question")).toBeNull();
    });

    it("is left plain (null) when it names a node that is not connected", () => {
      // Saving makes {{Other}} an input, and the note's advice (connect that
      // node to this one) is what the user needs.
      connectSource();

      expect(renderValidator()("Other")).toBeNull();
    });
  });

  // PGVAR-UX-R3-1: {{ question }} (spaces inside the braces, usual in Jinja)
  // was plain and listed in the note while it had no value. Once the user
  // filled in the value, as the note asked, it turned red and the note went
  // away. The backend trims the name, so the run uses that value.
  // (Before the value is filled in it is plain: see "in a new prompt" above.)
  describe("{{ question }} with spaces inside the braces", () => {
    it("is valid once its Variables value is filled in, as {{question}} is", () => {
      mockDatasetData = {
        columns: [{ id: "col-q", name: "question" }],
        rows: [{ cells: [{ column_id: "col-q", value: "What is AI?" }] }],
      };
      const validateVariable = renderValidator();

      expect(validateVariable(" question ")).toBe(true);
      expect(validateVariable("question")).toBe(true);
    });
  });

  // Controls: every other name returns what the base returned.
  describe("controls (same result as before)", () => {
    it("keeps a connected node's output valid", () => {
      connectSource();

      expect(renderValidator()("Source.response")).toBe(true);
    });

    it("keeps a misspelt output of a connected node invalid", () => {
      connectSource();

      expect(renderValidator()("Source.reponse")).toBe(false);
    });

    it('keeps Source.response["answer"] invalid', () => {
      connectSource({ responseFormat: "json" });

      expect(renderValidator()('Source.response["answer"]')).toBe(false);
    });

    it("keeps a key read from a connected text output invalid", () => {
      connectSource({ responseFormat: "text" });

      expect(renderValidator()("Source.response.answer")).toBe(false);
    });

    it("keeps a dotted Variables entry with a value valid", () => {
      connectSource();
      mockDatasetData = {
        columns: [{ id: "col-sq", name: "Source.question" }],
        rows: [{ cells: [{ column_id: "col-sq", value: "Why?" }] }],
      };

      expect(renderValidator()("Source.question")).toBe(true);
    });

    it("keeps a spaced dotted Variables entry invalid", () => {
      // Only a plain name ignores the spaces inside the braces.
      mockDatasetData = {
        columns: [{ id: "col-sq", name: "Source.question" }],
        rows: [{ cells: [{ column_id: "col-sq", value: "Why?" }] }],
      };

      expect(renderValidator()(" Source.question ")).toBe(false);
    });

    it("keeps a dotted name with no source invalid", () => {
      expect(renderValidator()("Other.response")).toBe(false);
    });

    it("keeps Jinja expressions invalid", () => {
      const validateVariable = renderValidator();

      expect(validateVariable(" user.profile.name ")).toBe(false);
      expect(validateVariable(" items | length ")).toBe(false);
      expect(validateVariable(" loop.index ")).toBe(false);
      expect(validateVariable(" a + b ")).toBe(false);
    });

    it("does not treat a bare name a connected node supplies as an input", () => {
      // Saving wires {{response}} / {{context}} to the connected node's
      // output of that name, so Run never asks for a value.
      connectSource({ outputs: ["response", "context"] });
      const validateVariable = renderValidator();

      expect(validateVariable("response")).toBe(false);
      expect(validateVariable("context")).toBe(false);
    });

    // PGVAR-UX-R3-2: a connected node's own label, typed bare, most likely
    // means one of its outputs ({{Source.response}}). It is not listed as an
    // input, because "connect that node to this one" cannot help.
    it("keeps a connected node's own label invalid, not an input", () => {
      connectSource();
      const validateVariable = renderValidator();

      expect(validateVariable("Source")).toBe(false);
      expect(validateVariable(" Source ")).toBe(false);
    });

    it("keeps a source node named only by the edge mappings invalid", () => {
      mockEdgeMappingsData = [
        {
          source_node_name: "llm_prompt_1",
          output_ports: [
            {
              direction: "output",
              display_name: "response_1",
              data_schema: { type: "string" },
            },
          ],
        },
      ];

      expect(renderValidator()("llm_prompt_1")).toBe(false);
    });
  });
});
