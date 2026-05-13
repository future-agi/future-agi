/**
 * Shared test data factories for agent-playground tests.
 * Root nodes are detected dynamically — nodes with no incoming edges.
 */
import { NODE_TYPES } from "../constants";

// ---------------------------------------------------------------------------
// Node factories
// ---------------------------------------------------------------------------

/** Generic XYFlow node */
export function createNode(id, type = NODE_TYPES.LLM_PROMPT, overrides = {}) {
  return {
    id,
    type,
    position: { x: 0, y: 0 },
    data: {
      label: `Node ${id}`,
      config: { placeholder: true },
      ...overrides.data,
    },
    ...overrides,
  };
}

/** LLM_PROMPT node with valid config (model, messages, ports) */
export function createPromptNode(id, overrides = {}) {
  const ports = overrides.ports || [
    {
      temp_id: `${id}-out`,
      key: "response",
      display_name: "response",
      direction: "output",
      data_schema: { type: "string" },
      required: false,
    },
  ];

  return {
    id,
    type: NODE_TYPES.LLM_PROMPT,
    position: overrides.position || { x: 0, y: 0 },
    data: {
      label: overrides.label || `Prompt ${id}`,
      node_template_id: overrides.node_template_id || "tpl-prompt",
      config: {
        modelConfig: {
          model: "gpt-4",
          modelDetail: {
            modelName: "GPT-4",
            logoUrl: "",
            providers: "openai",
            isAvailable: true,
          },
          responseFormat: "text",
          toolChoice: "auto",
          tools: [],
        },
        messages: [
          {
            id: "msg-0",
            role: "user",
            content: [{ type: "text", text: "Hello {{name}}" }],
          },
        ],
        payload: { ports },
        ...(overrides.config || {}),
      },
    },
  };
}

/** AGENT node with ref_graph_version_id */
export function createAgentNode(id, overrides = {}) {
  const ports = overrides.ports || [
    {
      temp_id: `${id}-in`,
      key: "input",
      display_name: "input",
      direction: "input",
      data_schema: { type: "string" },
      required: false,
    },
    {
      temp_id: `${id}-out`,
      key: "response",
      display_name: "response",
      direction: "output",
      data_schema: { type: "string" },
      required: false,
    },
  ];

  return {
    id,
    type: NODE_TYPES.AGENT,
    position: { x: 0, y: 0 },
    data: {
      label: overrides.label || `Agent ${id}`,
      ref_graph_version_id: overrides.ref_graph_version_id || "ver-123",
      config: {
        graphId: overrides.graphId || "graph-1",
        versionId: overrides.versionId || "ver-123",
        payload: { ports },
      },
    },
  };
}

export function createEvalNode(id, overrides = {}) {
  const ports = overrides.ports || [
    {
      temp_id: `${id}-in`,
      key: "input",
      display_name: "input",
      direction: "input",
      data_schema: {},
      required: true,
    },
    {
      temp_id: `${id}-out`,
      key: "evaluation_result",
      display_name: "evaluation_result",
      direction: "output",
      data_schema: { type: "object" },
      required: true,
    },
  ];

  return {
    id,
    type: NODE_TYPES.EVAL,
    position: { x: 0, y: 0 },
    data: {
      label: overrides.label || `Evaluation ${id}`,
      node_template_id: overrides.node_template_id || "tpl-eval",
      ports,
      evaluators: overrides.evaluators || [{ templateId: "eval-template-1" }],
      config: {
        evaluators: overrides.evaluators || [{ templateId: "eval-template-1" }],
        threshold: overrides.threshold ?? 0.8,
        failAction: overrides.failAction || "route_fallback",
      },
    },
  };
}

/** Node with empty/no config — will fail validation */
export function createUnconfiguredNode(id) {
  return {
    id,
    type: NODE_TYPES.LLM_PROMPT,
    position: { x: 0, y: 0 },
    data: {
      label: `Unconfigured ${id}`,
      config: {},
    },
  };
}

// ---------------------------------------------------------------------------
// Edge factories
// ---------------------------------------------------------------------------

/** Edge between two nodes */
export function createEdge(source, target, overrides = {}) {
  return {
    id: `${source}-${target}`,
    source,
    target,
    sourceHandle: overrides.sourceHandle || "response",
    targetHandle: overrides.targetHandle || "input",
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Graph factories
// ---------------------------------------------------------------------------

/**
 * Linear chain: n nodes connected sequentially.
 * First node is root (no incoming edges).
 */
export function createLinearGraph(n) {
  const nodes = Array.from({ length: n }, (_, i) =>
    createPromptNode(`n${i + 1}`),
  );
  const edges = [];
  for (let i = 0; i < n - 1; i++) {
    edges.push(createEdge(nodes[i].id, nodes[i + 1].id));
  }
  return { nodes, edges };
}

/** Graph with a cycle: A → B → C → A */
export function createCyclicGraph() {
  const nodes = [
    createPromptNode("a"),
    createPromptNode("b"),
    createPromptNode("c"),
  ];
  const edges = [
    createEdge("a", "b"),
    createEdge("b", "c"),
    createEdge("c", "a"),
  ];
  return { nodes, edges };
}

/**
 * Diamond graph: A → C, B → C.
 * A and B are both roots (no incoming edges).
 */
export function createDiamondGraph() {
  const nodes = [
    createPromptNode("a"),
    createPromptNode("b"),
    createPromptNode("c"),
  ];
  const edges = [createEdge("a", "c"), createEdge("b", "c")];
  return { nodes, edges };
}

/**
 * Disconnected graph: A → B (connected), C isolated.
 */
export function createDisconnectedGraph() {
  const nodes = [
    createPromptNode("a"),
    createPromptNode("b"),
    createPromptNode("c"),
  ];
  const edges = [createEdge("a", "b")];
  return { nodes, edges };
}
