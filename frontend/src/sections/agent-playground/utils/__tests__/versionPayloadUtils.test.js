import { describe, it, expect } from "vitest";
import {
  buildVersionPayload,
  parseVersionResponse,
} from "../versionPayloadUtils";
import { createPromptNode, createAgentNode, createEdge } from "./fixtures";
import { API_NODE_TYPES, NODE_TYPES, VERSION_STATUS } from "../constants";

// ---------------------------------------------------------------------------
// buildVersionPayload
// ---------------------------------------------------------------------------
describe("buildVersionPayload", () => {
  it("maps LLM_PROMPT nodes to ATOMIC type", () => {
    const nodes = [createPromptNode("p1")];
    const result = buildVersionPayload(nodes, []);
    expect(result.nodes[0].type).toBe(API_NODE_TYPES.ATOMIC);
    expect(result.nodes[0].id).toBe("p1");
  });

  it("maps AGENT nodes to SUBGRAPH type with ref_graph_version_id", () => {
    const nodes = [createAgentNode("a1", { ref_graph_version_id: "v-42" })];
    const result = buildVersionPayload(nodes, []);
    expect(result.nodes[0].type).toBe(API_NODE_TYPES.SUBGRAPH);
    expect(result.nodes[0].ref_graph_version_id).toBe("v-42");
  });

  it("uses default DRAFT status", () => {
    const result = buildVersionPayload([], []);
    expect(result.status).toBe(VERSION_STATUS.DRAFT);
  });

  it("accepts status override", () => {
    const result = buildVersionPayload([], [], { status: "active" });
    expect(result.status).toBe("active");
  });

  it("includes commit_message when provided", () => {
    const result = buildVersionPayload([], [], {
      commitMessage: "Initial commit",
    });
    expect(result.commit_message).toBe("Initial commit");
  });

  it("omits commit_message when not provided", () => {
    const result = buildVersionPayload([], []);
    expect(result).not.toHaveProperty("commit_message");
  });

  it("passes through output ports from node data", () => {
    const ports = [
      {
        temp_id: "p1-out",
        key: "response",
        display_name: "response",
        direction: "output",
        data_schema: { type: "string" },
        required: false,
      },
    ];
    const nodes = [
      {
        id: "p1",
        type: "llm_prompt",
        position: { x: 0, y: 0 },
        data: {
          label: "Prompt p1",
          node_template_id: "tpl-prompt",
          ports,
          config: {
            modelConfig: { model: "gpt-4", modelDetail: {} },
            messages: [],
          },
        },
      },
    ];
    const result = buildVersionPayload(nodes, []);
    expect(result.nodes[0].ports).toHaveLength(1);
    expect(result.nodes[0].ports[0].key).toBe("response");
  });

  it("converts edges to node_connections with source_node_id/target_node_id", () => {
    const sourceNode = createPromptNode("s1");
    const targetNode = createPromptNode("t1");
    const edges = [createEdge("s1", "t1")];
    const result = buildVersionPayload([sourceNode, targetNode], edges);
    expect(result.node_connections).toHaveLength(1);
    expect(result.node_connections[0].source_node_id).toBe("s1");
    expect(result.node_connections[0].target_node_id).toBe("t1");
  });

  it("transforms config with model and messages for API", () => {
    const nodes = [createPromptNode("p1")];
    const result = buildVersionPayload(nodes, []);
    const pt = result.nodes[0].prompt_template;
    expect(pt.model).toBe("gpt-4");
    expect(pt.messages).toEqual([
      {
        id: "msg-0",
        role: "user",
        content: [{ type: "text", text: "Hello {{name}}" }],
      },
    ]);
  });

  it("includes node_template_id for atomic nodes", () => {
    const nodes = [createPromptNode("p1")];
    const result = buildVersionPayload(nodes, []);
    expect(result.nodes[0].node_template_id).toBe("tpl-prompt");
  });

  it("serializes code execution nodes with config instead of prompt_template", () => {
    const nodes = [
      {
        id: "code-1",
        type: NODE_TYPES.CODE_EXECUTION,
        position: { x: 10, y: 20 },
        data: {
          label: "Code",
          node_template_id: "tpl-code",
          config: { language: "python", code: "result = inputs" },
          ports: [
            {
              temp_id: "code-in",
              key: "inputs",
              display_name: "inputs",
              direction: "input",
              data_schema: { type: "object" },
              required: false,
            },
            {
              temp_id: "code-out",
              key: "result",
              display_name: "result",
              direction: "output",
              data_schema: { type: "object" },
              required: true,
            },
          ],
        },
      },
    ];

    const result = buildVersionPayload(nodes, []);

    expect(result.nodes[0]).toMatchObject({
      type: API_NODE_TYPES.ATOMIC,
      node_template_id: "tpl-code",
      config: { language: "python", code: "result = inputs" },
    });
    expect(result.nodes[0]).not.toHaveProperty("prompt_template");
    expect(result.nodes[0].ports).toEqual([
      {
        id: "code-in",
        key: "inputs",
        display_name: "inputs",
        direction: "input",
        data_schema: { type: "object" },
        required: false,
      },
      {
        id: "code-out",
        key: "result",
        display_name: "result",
        direction: "output",
        data_schema: { type: "object" },
        required: true,
      },
    ]);
  });

  it("does not include node_template_id for subgraph nodes", () => {
    const nodes = [createAgentNode("a1")];
    const result = buildVersionPayload(nodes, []);
    expect(result.nodes[0]).not.toHaveProperty("node_template_id");
  });

  it("handles empty config (subgraph nodes)", () => {
    const nodes = [createAgentNode("a1")];
    const result = buildVersionPayload(nodes, []);
    expect(result.nodes[0].config).toEqual({});
  });
});

// ---------------------------------------------------------------------------
// parseVersionResponse
// ---------------------------------------------------------------------------
describe("parseVersionResponse", () => {
  it("returns empty arrays for null/undefined input", () => {
    expect(parseVersionResponse(null)).toEqual({ nodes: [], edges: [] });
    expect(parseVersionResponse(undefined)).toEqual({ nodes: [], edges: [] });
  });

  it("returns empty arrays when nodes array is empty", () => {
    expect(parseVersionResponse({ nodes: [] })).toEqual({
      nodes: [],
      edges: [],
    });
  });

  it("maps atomic API type to LLM_PROMPT node type", () => {
    const apiData = {
      nodes: [
        {
          id: "n1",
          type: "atomic",
          name: "My Prompt",
          position: { x: 100, y: 200 },
          promptTemplate: {
            model: "gpt-4",
            messages: [{ role: "user", content: "Hi" }],
          },
          ports: [],
        },
      ],
      nodeConnections: [],
    };
    const result = parseVersionResponse(apiData);
    expect(result.nodes[0].type).toBe("llm_prompt");
    expect(result.nodes[0].data.label).toBe("My Prompt");
    expect(result.nodes[0].position).toEqual({ x: 100, y: 200 });
  });

  it("maps subgraph API type to AGENT node type", () => {
    const apiData = {
      nodes: [
        {
          id: "a1",
          type: "subgraph",
          name: "Agent",
          refGraphVersionId: "v-99",
          refGraphId: "g-1",
          config: {},
          ports: [],
        },
      ],
      nodeConnections: [],
    };
    const result = parseVersionResponse(apiData);
    expect(result.nodes[0].type).toBe("agent");
    expect(result.nodes[0].data.ref_graph_version_id).toBe("v-99");
    expect(result.nodes[0].data.graphId).toBe("g-1");
  });

  it("reconstructs ports with display_name on atomic nodes", () => {
    const apiData = {
      nodes: [
        {
          id: "n1",
          type: "atomic",
          name: "Node",
          promptTemplate: { model: "gpt-4", messages: [] },
          ports: [
            {
              id: "port-1",
              key: "response",
              displayName: "response",
              direction: "output",
              dataSchema: { type: "string" },
              required: false,
            },
          ],
        },
      ],
      nodeConnections: [],
    };
    const result = parseVersionResponse(apiData);
    const ports = result.nodes[0].data.ports;
    expect(ports).toHaveLength(1);
    expect(ports[0].id).toBe("port-1");
    expect(ports[0].display_name).toBe("response");
  });

  it("maps nodeConnections to edges with source/target node IDs", () => {
    const apiData = {
      nodes: [
        {
          id: "n1",
          type: "atomic",
          name: "Source",
          promptTemplate: { model: "gpt-4", messages: [] },
          ports: [],
        },
        {
          id: "n2",
          type: "atomic",
          name: "Target",
          promptTemplate: { model: "gpt-4", messages: [] },
          ports: [],
        },
      ],
      nodeConnections: [{ id: "nc1", sourceNodeId: "n1", targetNodeId: "n2" }],
    };
    const result = parseVersionResponse(apiData);
    expect(result.edges).toHaveLength(1);
    expect(result.edges[0].source).toBe("n1");
    expect(result.edges[0].target).toBe("n2");
  });

  it("transforms API promptTemplate to form config (string content → block array)", () => {
    const apiData = {
      nodes: [
        {
          id: "n1",
          type: "atomic",
          name: "Node",
          promptTemplate: {
            model: "gpt-4",
            messages: [{ role: "user", content: "Hello world" }],
          },
          ports: [],
        },
      ],
      nodeConnections: [],
    };
    const result = parseVersionResponse(apiData);
    const config = result.nodes[0].data.config;
    expect(config.modelConfig.model).toBe("gpt-4");
    // System placeholder is prepended when API has no system message
    expect(config.messages[0]).toEqual({
      id: "msg-sys",
      role: "system",
      content: [{ type: "text", text: "" }],
    });
    expect(config.messages[1].content).toEqual([
      { type: "text", text: "Hello world" },
    ]);
    expect(config.messages[1].id).toBe("msg-0");
  });

  it("preserves raw API promptTemplate in promptConfig for model parameters", () => {
    const promptTemplate = {
      model: "gpt-4",
      messages: [],
      temperature: 0.7,
      max_tokens: 100,
    };
    const apiData = {
      nodes: [
        {
          id: "n1",
          type: "atomic",
          name: "Node",
          promptTemplate,
          ports: [],
        },
      ],
      nodeConnections: [],
    };
    const result = parseVersionResponse(apiData);
    const promptConfig = result.nodes[0].data.config.payload.promptConfig;
    // The configuration spreads the promptTemplate plus normalizes responseFormat
    expect(promptConfig[0].configuration.model).toBe("gpt-4");
    expect(promptConfig[0].configuration.temperature).toBe(0.7);
    expect(promptConfig[0].configuration.max_tokens).toBe(100);
  });
});

// ---------------------------------------------------------------------------
// Round-trip test
// ---------------------------------------------------------------------------
describe("round-trip: buildVersionPayload → parseVersionResponse", () => {
  it("preserves node types and basic content", () => {
    const sourceNode = createPromptNode("p1", {
      ports: [
        {
          temp_id: "p1-out",
          key: "response",
          display_name: "response",
          direction: "output",
          data_schema: { type: "string" },
          required: false,
        },
      ],
    });
    // Put ports in node.data.ports for buildVersionPayload to pick up
    sourceNode.data.ports = sourceNode.data.config.payload.ports;

    const payload = buildVersionPayload([sourceNode], []);
    // Simulate API response format (camelCased fields)
    const apiResponse = {
      nodes: payload.nodes.map((n) => ({
        id: n.id,
        type: n.type,
        name: n.name,
        promptTemplate: n.prompt_template,
        position: n.position,
        nodeTemplateId: n.node_template_id,
        ports: (n.ports || []).map((p) => ({
          id: p.id,
          key: p.key,
          displayName: p.display_name,
          direction: p.direction,
          dataSchema: p.data_schema,
          required: p.required,
        })),
      })),
      nodeConnections: [],
    };

    const parsed = parseVersionResponse(apiResponse);
    expect(parsed.nodes[0].type).toBe("llm_prompt");
    expect(parsed.nodes[0].data.config.modelConfig.model).toBe("gpt-4");
  });
});

// ---------------------------------------------------------------------------
// Edge cases
// ---------------------------------------------------------------------------
describe("edge cases", () => {
  describe("buildVersionPayload", () => {
    it("handles node with data = null without crashing", () => {
      const nodes = [
        { id: "n1", type: "llm_prompt", position: { x: 0, y: 0 }, data: null },
      ];
      const result = buildVersionPayload(nodes, []);
      expect(result.nodes).toHaveLength(1);
      // prompt_template is null when config is empty/missing
      expect(result.nodes[0].prompt_template).toBeNull();
    });

    it("handles node with data = undefined without crashing", () => {
      const nodes = [
        { id: "n1", type: "llm_prompt", position: { x: 0, y: 0 } },
      ];
      const result = buildVersionPayload(nodes, []);
      expect(result.nodes).toHaveLength(1);
      expect(result.nodes[0].prompt_template).toBeNull();
    });

    it("includes all edges in node_connections (no port filtering)", () => {
      const nodes = [createPromptNode("n1")];
      const edges = [
        {
          id: "e1",
          source: "n1",
          target: "non-existent",
          sourceHandle: "response",
          targetHandle: "input",
        },
      ];
      const result = buildVersionPayload(nodes, edges);
      // Current implementation maps all edges to node_connections
      expect(result.node_connections).toHaveLength(1);
      expect(result.node_connections[0].source_node_id).toBe("n1");
      expect(result.node_connections[0].target_node_id).toBe("non-existent");
    });

    it("handles modelConfig: null (not undefined)", () => {
      const nodes = [
        {
          id: "n1",
          type: "llm_prompt",
          position: { x: 0, y: 0 },
          data: { label: "Test", config: { modelConfig: null, messages: [] } },
        },
      ];
      const result = buildVersionPayload(nodes, []);
      // buildPromptTemplateForApi returns null when model is missing
      expect(result.nodes[0].prompt_template).toBeNull();
    });
  });

  describe("parseVersionResponse", () => {
    it("handles ports missing id field", () => {
      const apiData = {
        nodes: [
          {
            id: "n1",
            type: "atomic",
            name: "Node 1",
            promptTemplate: { model: "gpt-4", messages: [] },
            position: { x: 0, y: 0 },
            ports: [
              { key: "response", displayName: "response", direction: "output" },
            ],
          },
        ],
        nodeConnections: [],
      };
      const result = parseVersionResponse(apiData);
      expect(result.nodes).toHaveLength(1);
      // Port should still be reconstructed
    expect(result.nodes[0].data.ports[0].display_name).toBe("response");
  });

  it("parses code execution nodes from canonical snake_case response data", () => {
    const apiData = {
      nodes: [
        {
          id: "code-1",
          type: API_NODE_TYPES.ATOMIC,
          name: "Code",
          node_template_id: "tpl-code",
          node_template_name: NODE_TYPES.CODE_EXECUTION,
          config: { language: "python", code: "result = inputs" },
          position: { x: 1, y: 2 },
          ports: [
            {
              id: "port-1",
              key: "result",
              display_name: "result",
              direction: "output",
              data_schema: { type: "object" },
            },
          ],
        },
      ],
      node_connections: [
        { id: "edge-1", source_node_id: "code-1", target_node_id: "next-1" },
      ],
    };

    const result = parseVersionResponse(apiData);

    expect(result.nodes[0]).toMatchObject({
      id: "code-1",
      type: NODE_TYPES.CODE_EXECUTION,
      data: {
        node_template_id: "tpl-code",
        config: { language: "python", code: "result = inputs" },
      },
    });
    expect(result.edges[0]).toEqual({
      id: "edge-1",
      source: "code-1",
      target: "next-1",
    });
  });

    it("handles orphaned edges (referencing non-existent ports)", () => {
      const apiData = {
        nodes: [
          {
            id: "n1",
            type: "atomic",
            name: "Node 1",
            promptTemplate: { model: "gpt-4", messages: [] },
            position: { x: 0, y: 0 },
            ports: [
              {
                id: "p1",
                key: "response",
                displayName: "response",
                direction: "output",
              },
            ],
          },
        ],
        // nodeConnections with missing source/target are filtered
        nodeConnections: [{ sourceNodeId: null, targetNodeId: null }],
      };
      const result = parseVersionResponse(apiData);
      // Connections with null source/target are filtered out
      expect(result.edges).toHaveLength(0);
    });

    it("handles messages: null in API promptTemplate", () => {
      const apiData = {
        nodes: [
          {
            id: "n1",
            type: "atomic",
            name: "Node 1",
            promptTemplate: { model: "gpt-4", messages: null },
            position: { x: 0, y: 0 },
            ports: [],
          },
        ],
        nodeConnections: [],
      };
      const result = parseVersionResponse(apiData);
      // System placeholder is prepended even when messages is null
      expect(result.nodes[0].data.config.messages).toEqual([
        {
          id: "msg-sys",
          role: "system",
          content: [{ type: "text", text: "" }],
        },
      ]);
    });

    it("handles null apiData", () => {
      expect(parseVersionResponse(null)).toEqual({ nodes: [], edges: [] });
    });

    it("handles undefined apiData", () => {
      expect(parseVersionResponse(undefined)).toEqual({ nodes: [], edges: [] });
    });

    it("handles apiData with null nodes", () => {
      expect(parseVersionResponse({ nodes: null })).toEqual({
        nodes: [],
        edges: [],
      });
    });
  });

  describe("flattenContentToString", () => {
    it("returns empty string for non-standard content blocks (missing .text)", () => {
      const nodes = [
        createPromptNode("p1", {
          config: {
            modelConfig: { model: "gpt-4", modelDetail: {} },
            messages: [
              {
                role: "user",
                content: [{ type: "image", url: "http://example.com" }],
              },
            ],
            payload: { ports: [] },
          },
        }),
      ];
      const result = buildVersionPayload(nodes, []);
      const pt = result.nodes[0].prompt_template;
      // Content blocks are passed through as arrays now
      expect(pt.messages[0].content).toEqual([
        { type: "image", url: "http://example.com" },
      ]);
    });

    it("handles mixed standard and non-standard blocks", () => {
      const nodes = [
        createPromptNode("p1", {
          config: {
            modelConfig: { model: "gpt-4", modelDetail: {} },
            messages: [
              {
                role: "user",
                content: [
                  { type: "text", text: "Hello " },
                  { type: "image" },
                  { type: "text", text: "world" },
                ],
              },
            ],
            payload: { ports: [] },
          },
        }),
      ];
      const result = buildVersionPayload(nodes, []);
      const pt = result.nodes[0].prompt_template;
      // Content blocks are passed through as arrays
      expect(pt.messages[0].content).toEqual([
        { type: "text", text: "Hello " },
        { type: "image" },
        { type: "text", text: "world" },
      ]);
    });

    it("handles null content", () => {
      const nodes = [
        createPromptNode("p1", {
          config: {
            modelConfig: { model: "gpt-4", modelDetail: {} },
            messages: [{ role: "user", content: null }],
            payload: { ports: [] },
          },
        }),
      ];
      const result = buildVersionPayload(nodes, []);
      const pt = result.nodes[0].prompt_template;
      // null content becomes [{ type: "text", text: "" }]
      expect(pt.messages[0].content).toEqual([{ type: "text", text: "" }]);
    });

    it("handles number content (non-string, non-array)", () => {
      const nodes = [
        createPromptNode("p1", {
          config: {
            modelConfig: { model: "gpt-4", modelDetail: {} },
            messages: [{ role: "user", content: 42 }],
            payload: { ports: [] },
          },
        }),
      ];
      const result = buildVersionPayload(nodes, []);
      const pt = result.nodes[0].prompt_template;
      // non-array content gets wrapped with the raw value
      expect(pt.messages[0].content).toEqual([{ type: "text", text: 42 }]);
    });
  });
});
