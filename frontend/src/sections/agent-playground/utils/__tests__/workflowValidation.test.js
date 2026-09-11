import { describe, it, expect } from "vitest";
import {
  detectCycles,
  getConnectedNodes,
  validateNode,
  validateNodeForSave,
  validateWorkflow,
  validateGraphForSave,
  WorkflowValidationError,
} from "../workflowValidation";
import {
  createNode,
  createPromptNode,
  createAgentNode,
  createUnconfiguredNode,
  createEdge,
  createLinearGraph,
  createCyclicGraph,
  createDiamondGraph,
  createDisconnectedGraph,
} from "./fixtures";

// ---------------------------------------------------------------------------
// detectCycles
// ---------------------------------------------------------------------------
describe("detectCycles", () => {
  it("returns false for empty graph", () => {
    expect(detectCycles([], [])).toBe(false);
  });

  it("returns false for a single node", () => {
    expect(detectCycles([createNode("a")], [])).toBe(false);
  });

  it("returns false for a linear chain", () => {
    const { nodes, edges } = createLinearGraph(4);
    expect(detectCycles(nodes, edges)).toBe(false);
  });

  it("returns false for a DAG diamond", () => {
    const { nodes, edges } = createDiamondGraph();
    expect(detectCycles(nodes, edges)).toBe(false);
  });

  it("returns false for disconnected acyclic graph", () => {
    const { nodes, edges } = createDisconnectedGraph();
    expect(detectCycles(nodes, edges)).toBe(false);
  });

  it("returns true for a 2-node cycle", () => {
    const nodes = [createNode("a"), createNode("b")];
    const edges = [createEdge("a", "b"), createEdge("b", "a")];
    expect(detectCycles(nodes, edges)).toBe(true);
  });

  it("returns true for a 3-node cycle", () => {
    const { nodes, edges } = createCyclicGraph();
    expect(detectCycles(nodes, edges)).toBe(true);
  });

  it("returns true for a self-loop", () => {
    const nodes = [createNode("a")];
    const edges = [createEdge("a", "a")];
    expect(detectCycles(nodes, edges)).toBe(true);
  });

  it("returns true when a subgraph has a cycle", () => {
    // A → B (acyclic) + C → D → C (cycle)
    const nodes = [
      createNode("a"),
      createNode("b"),
      createNode("c"),
      createNode("d"),
    ];
    const edges = [
      createEdge("a", "b"),
      createEdge("c", "d"),
      createEdge("d", "c"),
    ];
    expect(detectCycles(nodes, edges)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// getConnectedNodes
// ---------------------------------------------------------------------------
describe("getConnectedNodes", () => {
  it("returns empty array for empty graph", () => {
    expect(getConnectedNodes([], [])).toEqual([]);
  });

  it("returns all nodes when all are roots (no edges)", () => {
    const nodes = [createNode("a"), createNode("b")];
    const result = getConnectedNodes(nodes, []);
    expect(result).toHaveLength(2);
  });

  it("returns all reachable nodes from roots in a linear chain", () => {
    const { nodes, edges } = createLinearGraph(3);
    const result = getConnectedNodes(nodes, edges);
    expect(result).toHaveLength(3);
  });

  it("returns all reachable nodes from roots in a diamond (no duplicates)", () => {
    const { nodes, edges } = createDiamondGraph();
    const result = getConnectedNodes(nodes, edges);
    expect(result).toHaveLength(3);
    const ids = result.map((n) => n.id);
    expect(new Set(ids).size).toBe(ids.length); // no duplicates
  });

  it("excludes disconnected nodes not reachable from any root", () => {
    // A → B, C is isolated but also a root (no incoming edges)
    // Since C has no incoming edges, it IS a root and will be included
    const { nodes, edges } = createDisconnectedGraph();
    const result = getConnectedNodes(nodes, edges);
    // All are reachable — A and C are roots, B is target of A
    expect(result).toHaveLength(3);
  });

  it("returns all nodes when all have incoming edges (fully cyclic)", () => {
    const { nodes, edges } = createCyclicGraph();
    // No root nodes → returns all nodes
    const result = getConnectedNodes(nodes, edges);
    expect(result).toHaveLength(3);
  });
});

// ---------------------------------------------------------------------------
// validateNode
// ---------------------------------------------------------------------------
describe("validateNode", () => {
  it("returns valid for node with non-empty config", () => {
    const node = createPromptNode("a");
    const result = validateNode(node);
    expect(result.valid).toBe(true);
    expect(result.errors).toEqual([]);
  });

  it("returns invalid for node with empty config", () => {
    const node = createUnconfiguredNode("a");
    const result = validateNode(node);
    expect(result.valid).toBe(false);
    expect(result.node_id).toBe("a");
  });

  it("returns invalid for node with null data", () => {
    const node = { id: "a", type: "llm_prompt" };
    const result = validateNode(node);
    expect(result.valid).toBe(false);
  });

  it("returns invalid for node with missing config", () => {
    const node = { id: "a", type: "llm_prompt", data: { label: "test" } };
    const result = validateNode(node);
    expect(result.valid).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// validateNodeForSave
// ---------------------------------------------------------------------------
describe("validateNodeForSave", () => {
  describe("LLM_PROMPT", () => {
    it("valid with model + messages + valid user content (text)", () => {
      const node = createPromptNode("a");
      const result = validateNodeForSave(node);
      expect(result.valid).toBe(true);
    });

    it("valid with image_url content", () => {
      const node = createPromptNode("a");
      node.data.config.messages = [
        {
          role: "user",
          content: [
            {
              type: "image_url",
              image_url: { url: "https://example.com/img.png" },
            },
          ],
        },
      ];
      const result = validateNodeForSave(node);
      expect(result.valid).toBe(true);
    });

    it("valid with pdf_url content", () => {
      const node = createPromptNode("a");
      node.data.config.messages = [
        {
          role: "user",
          content: [
            {
              type: "pdf_url",
              pdf_url: { url: "https://example.com/doc.pdf" },
            },
          ],
        },
      ];
      const result = validateNodeForSave(node);
      expect(result.valid).toBe(true);
    });

    it("valid with audio_url content", () => {
      const node = createPromptNode("a");
      node.data.config.messages = [
        {
          role: "user",
          content: [
            {
              type: "audio_url",
              audio_url: { url: "https://example.com/audio.mp3" },
            },
          ],
        },
      ];
      const result = validateNodeForSave(node);
      expect(result.valid).toBe(true);
    });

    it("invalid when model is missing", () => {
      const node = createPromptNode("a");
      node.data.config.modelConfig.model = "";
      const result = validateNodeForSave(node);
      expect(result.valid).toBe(false);
      expect(result.message).toContain("Model");
    });

    it("invalid when messages are empty", () => {
      const node = createPromptNode("a");
      node.data.config.messages = [];
      const result = validateNodeForSave(node);
      expect(result.valid).toBe(false);
      expect(result.message).toContain("message");
    });

    it("invalid when user message content is empty text", () => {
      const node = createPromptNode("a");
      node.data.config.messages = [
        { role: "user", content: [{ type: "text", text: "   " }] },
      ];
      const result = validateNodeForSave(node);
      expect(result.valid).toBe(false);
    });

    it("valid when system message has empty content", () => {
      const node = createPromptNode("a");
      node.data.config.messages = [
        { role: "system", content: [] },
        {
          role: "user",
          content: [{ type: "text", text: "Hello" }],
        },
      ];
      const result = validateNodeForSave(node);
      expect(result.valid).toBe(true);
    });

    it("invalid with empty config", () => {
      const node = createUnconfiguredNode("a");
      const result = validateNodeForSave(node);
      expect(result.valid).toBe(false);
      expect(result.message).toContain("not configured");
    });
  });

  describe("AGENT", () => {
    it("valid with ref_graph_version_id present", () => {
      const node = createAgentNode("a");
      const result = validateNodeForSave(node);
      expect(result.valid).toBe(true);
    });

    it("invalid when ref_graph_version_id is missing", () => {
      const node = createAgentNode("a", { ref_graph_version_id: "" });
      node.data.ref_graph_version_id = "";
      const result = validateNodeForSave(node);
      expect(result.valid).toBe(false);
      expect(result.message).toContain("Agent");
    });

    it("invalid when ref_graph_version_id is whitespace", () => {
      const node = createAgentNode("a");
      node.data.ref_graph_version_id = "   ";
      const result = validateNodeForSave(node);
      expect(result.valid).toBe(false);
    });
  });

  describe("Unknown type", () => {
    it("always returns valid", () => {
      const node = createNode("a", "custom_type");
      const result = validateNodeForSave(node);
      expect(result.valid).toBe(true);
    });
  });
});

// ---------------------------------------------------------------------------
// validateWorkflow
// ---------------------------------------------------------------------------
describe("validateWorkflow", () => {
  it("invalid when no connected nodes", () => {
    const result = validateWorkflow([], []);
    expect(result.valid).toBe(false);
    expect(result.errors[0].message).toContain("No nodes");
  });

  it("valid when all connected nodes are configured", () => {
    const { nodes, edges } = createLinearGraph(2);
    const result = validateWorkflow(nodes, edges);
    expect(result.valid).toBe(true);
    expect(result.invalidNodeIds).toEqual([]);
  });

  it("invalid listing unconfigured node IDs", () => {
    const configured = createPromptNode("a");
    const unconfigured = createUnconfiguredNode("b");
    const nodes = [configured, unconfigured];
    const edges = [createEdge("a", "b")];
    const result = validateWorkflow(nodes, edges);
    expect(result.valid).toBe(false);
    expect(result.invalidNodeIds).toContain("b");
  });
});

// ---------------------------------------------------------------------------
// validateGraphForSave
// ---------------------------------------------------------------------------
describe("validateGraphForSave", () => {
  it("invalid with hasCycle=true when cycle detected", () => {
    const { nodes, edges } = createCyclicGraph();
    const result = validateGraphForSave(nodes, edges);
    expect(result.valid).toBe(false);
    expect(result.hasCycle).toBe(true);
    expect(result.errors[0].message).toContain("cycle");
  });

  it("cycle takes precedence over node errors", () => {
    // Cyclic graph with unconfigured nodes
    const nodes = [createUnconfiguredNode("a"), createUnconfiguredNode("b")];
    const edges = [createEdge("a", "b"), createEdge("b", "a")];
    const result = validateGraphForSave(nodes, edges);
    expect(result.valid).toBe(false);
    expect(result.hasCycle).toBe(true);
    // Should report cycle, not node errors
    expect(result.invalidNodeIds).toEqual([]);
  });

  it("invalid listing unconfigured nodes when no cycle", () => {
    const nodes = [createPromptNode("a"), createUnconfiguredNode("b")];
    const edges = [createEdge("a", "b")];
    const result = validateGraphForSave(nodes, edges);
    expect(result.valid).toBe(false);
    expect(result.hasCycle).toBe(false);
    expect(result.invalidNodeIds).toContain("b");
  });

  it("valid when all pass and no cycles", () => {
    const { nodes, edges } = createLinearGraph(3);
    const result = validateGraphForSave(nodes, edges);
    expect(result.valid).toBe(true);
    expect(result.hasCycle).toBe(false);
    expect(result.errors).toEqual([]);
  });

  it("validates ALL nodes (not just connected)", () => {
    // A → B (connected), C unconfigured (isolated)
    const nodes = [
      createPromptNode("a"),
      createPromptNode("b"),
      createUnconfiguredNode("c"),
    ];
    const edges = [createEdge("a", "b")];
    const result = validateGraphForSave(nodes, edges);
    expect(result.valid).toBe(false);
    expect(result.invalidNodeIds).toContain("c");
  });
});

// ---------------------------------------------------------------------------
// WorkflowValidationError
// ---------------------------------------------------------------------------
describe("WorkflowValidationError", () => {
  it("uses first error message", () => {
    const validationResult = {
      valid: false,
      errors: [
        { nodeId: "a", message: "First error" },
        { nodeId: "b", message: "Second error" },
      ],
      invalidNodeIds: ["a", "b"],
    };
    const error = new WorkflowValidationError(validationResult);
    expect(error.message).toBe("First error");
    expect(error.name).toBe("WorkflowValidationError");
  });

  it("falls back to default message when no errors", () => {
    const validationResult = {
      valid: false,
      errors: [],
      invalidNodeIds: [],
    };
    const error = new WorkflowValidationError(validationResult);
    expect(error.message).toBe("Workflow validation failed");
  });

  it("exposes validationResult and invalidNodeIds", () => {
    const validationResult = {
      valid: false,
      errors: [{ nodeId: "x", message: "Bad" }],
      invalidNodeIds: ["x"],
    };
    const error = new WorkflowValidationError(validationResult);
    expect(error.validationResult).toBe(validationResult);
    expect(error.invalidNodeIds).toEqual(["x"]);
  });
});

// ---------------------------------------------------------------------------
// Edge cases
// ---------------------------------------------------------------------------
describe("edge cases", () => {
  describe("detectCycles", () => {
    it("handles edge with null source", () => {
      const nodes = [
        { id: "n1", type: "llm_prompt", data: {} },
        { id: "n2", type: "llm_prompt", data: {} },
      ];
      const edges = [{ source: null, target: "n2" }];
      // Should not crash — null source won't match any node
      expect(detectCycles(nodes, edges)).toBe(false);
    });

    it("handles edge with undefined source", () => {
      const nodes = [{ id: "n1", type: "llm_prompt", data: {} }];
      const edges = [{ source: undefined, target: "n1" }];
      expect(detectCycles(nodes, edges)).toBe(false);
    });

    it("handles empty nodes with edges", () => {
      const edges = [{ source: "n1", target: "n2" }];
      expect(detectCycles([], edges)).toBe(false);
    });
  });

  describe("hasValidUserContent", () => {
    it("returns false for content: null", () => {
      const result = validateNodeForSave({
        type: "llm_prompt",
        data: {
          config: {
            modelConfig: { model: "gpt-4" },
            messages: [{ role: "user", content: null }],
          },
        },
      });
      expect(result.valid).toBe(false);
    });

    it("returns false for content: false", () => {
      const result = validateNodeForSave({
        type: "llm_prompt",
        data: {
          config: {
            modelConfig: { model: "gpt-4" },
            messages: [{ role: "user", content: false }],
          },
        },
      });
      expect(result.valid).toBe(false);
    });

    it("returns false for content with only whitespace text", () => {
      const result = validateNodeForSave({
        type: "llm_prompt",
        data: {
          config: {
            modelConfig: { model: "gpt-4" },
            messages: [
              { role: "user", content: [{ type: "text", text: "   " }] },
            ],
          },
        },
      });
      expect(result.valid).toBe(false);
    });

    it("returns false for empty content array", () => {
      const result = validateNodeForSave({
        type: "llm_prompt",
        data: {
          config: {
            modelConfig: { model: "gpt-4" },
            messages: [{ role: "user", content: [] }],
          },
        },
      });
      expect(result.valid).toBe(false);
    });
  });

  describe("validateNodeForSave", () => {
    it("returns invalid when config is an empty object", () => {
      const result = validateNodeForSave({
        type: "llm_prompt",
        data: { config: {} },
      });
      expect(result.valid).toBe(false);
    });

    it("returns invalid when modelConfig exists but model is empty string", () => {
      const result = validateNodeForSave({
        type: "llm_prompt",
        data: {
          config: {
            modelConfig: { model: "" },
            messages: [
              { role: "user", content: [{ type: "text", text: "Hi" }] },
            ],
          },
        },
      });
      expect(result.valid).toBe(false);
    });

    it("returns invalid when messages is null", () => {
      const result = validateNodeForSave({
        type: "llm_prompt",
        data: {
          config: {
            modelConfig: { model: "gpt-4" },
            messages: null,
          },
        },
      });
      expect(result.valid).toBe(false);
    });

    it("skips validation for agent (subgraph) nodes", () => {
      const result = validateNodeForSave({
        type: "agent",
        data: { config: {}, ref_graph_version_id: "v1" },
      });
      expect(result.valid).toBe(true);
    });
  });

  describe("validateGraphForSave", () => {
    it("returns valid for empty graph", () => {
      const result = validateGraphForSave([], []);
      expect(result.valid).toBe(true);
      expect(result.invalidNodeIds).toEqual([]);
    });

    it("detects cycle and returns invalid", () => {
      const nodes = [
        {
          id: "n1",
          type: "llm_prompt",
          data: {
            config: {
              modelConfig: { model: "gpt-4" },
              messages: [
                { role: "user", content: [{ type: "text", text: "Hi" }] },
              ],
            },
          },
        },
        {
          id: "n2",
          type: "llm_prompt",
          data: {
            config: {
              modelConfig: { model: "gpt-4" },
              messages: [
                { role: "user", content: [{ type: "text", text: "Hi" }] },
              ],
            },
          },
        },
      ];
      const edges = [
        { source: "n1", target: "n2" },
        { source: "n2", target: "n1" },
      ];
      const result = validateGraphForSave(nodes, edges);
      expect(result.valid).toBe(false);
      expect(result.hasCycle).toBe(true);
    });
  });
});
