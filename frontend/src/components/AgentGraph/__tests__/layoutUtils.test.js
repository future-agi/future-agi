import { describe, it, expect } from "vitest";
import { buildExecutionGraph, START_ID, END_ID } from "../layoutUtils";

// ---------------------------------------------------------------------------
// Helpers — API execution data factories
// ---------------------------------------------------------------------------

function makeApiNode(
  id,
  { type = "atomic", nodeExecution, subGraph, node_execution } = {},
) {
  return {
    id,
    name: `Node ${id}`,
    type,
    ...(nodeExecution && { nodeExecution }),
    ...(node_execution && { node_execution }),
    ...(subGraph && { subGraph }),
  };
}

function makeConnection(sourceNodeId, targetNodeId) {
  return { source_node_id: sourceNodeId, target_node_id: targetNodeId };
}

function findNode(nodes, id) {
  return nodes.find((n) => n.id === id);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("buildExecutionGraph", () => {
  // -----------------------------------------------------------------------
  // Empty / null input
  // -----------------------------------------------------------------------
  it("returns empty graph for null input", () => {
    expect(buildExecutionGraph(null)).toEqual({ nodes: [], edges: [] });
  });

  it("returns empty graph for undefined input", () => {
    expect(buildExecutionGraph(undefined)).toEqual({ nodes: [], edges: [] });
  });

  it("returns empty graph for empty nodes array", () => {
    expect(buildExecutionGraph({ nodes: [] })).toEqual({
      nodes: [],
      edges: [],
    });
  });

  // -----------------------------------------------------------------------
  // Single node
  // -----------------------------------------------------------------------
  it("builds graph with single atomic node", () => {
    const data = {
      nodes: [makeApiNode("a")],
      node_connections: [],
    };
    const { nodes, edges } = buildExecutionGraph(data);

    // start + node + end = 3
    expect(nodes).toHaveLength(3);
    expect(edges).toHaveLength(2); // start→a, a→end

    const startNode = findNode(nodes, START_ID);
    expect(startNode.type).toBe("startEndNode");
    expect(startNode.data.variant).toBe("start");

    const endNode = findNode(nodes, END_ID);
    expect(endNode.type).toBe("startEndNode");
    expect(endNode.data.variant).toBe("end");

    const nodeA = findNode(nodes, "a");
    expect(nodeA.type).toBe("executionNode");
    expect(nodeA.data.label).toBe("Node a");
    expect(nodeA.data.hasSubGraph).toBe(false);
  });

  // -----------------------------------------------------------------------
  // Linear chain
  // -----------------------------------------------------------------------
  it("builds linear chain A→B correctly", () => {
    const data = {
      nodes: [makeApiNode("a"), makeApiNode("b")],
      node_connections: [makeConnection("a", "b")],
    };
    const { nodes, edges } = buildExecutionGraph(data);

    // start + a + b + end = 4
    expect(nodes).toHaveLength(4);
    // start→a, a→b, b→end = 3
    expect(edges).toHaveLength(3);

    // Verify edge connectivity
    const startEdge = edges.find((e) => e.source === START_ID);
    expect(startEdge.target).toBe("a");

    const midEdge = edges.find((e) => e.source === "a" && e.target === "b");
    expect(midEdge).toBeDefined();

    const endEdge = edges.find((e) => e.target === END_ID);
    expect(endEdge.source).toBe("b");
  });

  // -----------------------------------------------------------------------
  // Diamond graph
  // -----------------------------------------------------------------------
  it("builds diamond graph (A→C, B→C)", () => {
    const data = {
      nodes: [makeApiNode("a"), makeApiNode("b"), makeApiNode("c")],
      node_connections: [makeConnection("a", "c"), makeConnection("b", "c")],
    };
    const { nodes, edges } = buildExecutionGraph(data);

    // start + a + b + c + end = 5
    expect(nodes).toHaveLength(5);

    // start→a, start→b, a→c, b→c, c→end = 5
    const startEdges = edges.filter((e) => e.source === START_ID);
    expect(startEdges).toHaveLength(2);
    expect(startEdges.map((e) => e.target).sort()).toEqual(["a", "b"]);

    const endEdges = edges.filter((e) => e.target === END_ID);
    expect(endEdges).toHaveLength(1);
    expect(endEdges[0].source).toBe("c");
  });

  // -----------------------------------------------------------------------
  // Subgraph node
  // -----------------------------------------------------------------------
  it("builds subgraph node as group with inner children", () => {
    const data = {
      nodes: [
        makeApiNode("sg1", {
          type: "subgraph",
          subGraph: {
            id: "sg-exec-1",
            nodes: [
              {
                id: "inner-a",
                name: "Inner A",
                type: "atomic",
                nodeExecution: { id: "ne-ia", status: "success" },
              },
              {
                id: "inner-b",
                name: "Inner B",
                type: "atomic",
              },
            ],
            node_connections: [makeConnection("inner-a", "inner-b")],
          },
        }),
      ],
      node_connections: [],
    };

    const { nodes, edges } = buildExecutionGraph(data);

    // start + group + 2 inner + end = 5
    expect(nodes).toHaveLength(5);

    const groupNode = findNode(nodes, "sg1");
    expect(groupNode.type).toBe("subgraphGroup");
    expect(groupNode.data.frontendNodeType).toBe("agent");

    // Inner nodes are prefixed
    const innerA = findNode(nodes, "sg1__inner-a");
    expect(innerA).toBeDefined();
    expect(innerA.parentId).toBe("sg1");
    expect(innerA.data.originalId).toBe("inner-a");
    expect(innerA.data.frontendNodeType).toBe("llm_prompt");
    expect(innerA.data.nodeExecution).toEqual({
      id: "ne-ia",
      status: "success",
    });

    const innerB = findNode(nodes, "sg1__inner-b");
    expect(innerB).toBeDefined();
    expect(innerB.parentId).toBe("sg1");

    // Inner edge should be prefixed
    const innerEdge = edges.find(
      (e) => e.source === "sg1__inner-a" && e.target === "sg1__inner-b",
    );
    expect(innerEdge).toBeDefined();
  });

  // -----------------------------------------------------------------------
  // Mixed graph (atomic + subgraph)
  // -----------------------------------------------------------------------
  it("handles mixed atomic and subgraph nodes", () => {
    const data = {
      nodes: [
        makeApiNode("a"),
        makeApiNode("sg", {
          type: "subgraph",
          subGraph: {
            id: "sg-exec",
            nodes: [
              {
                id: "s1",
                name: "S1",
                type: "atomic",
              },
            ],
            node_connections: [],
          },
        }),
      ],
      node_connections: [makeConnection("a", "sg")],
    };

    const { nodes } = buildExecutionGraph(data);

    const atomicNode = findNode(nodes, "a");
    expect(atomicNode.type).toBe("executionNode");

    const groupNode = findNode(nodes, "sg");
    expect(groupNode.type).toBe("subgraphGroup");

    const innerNode = findNode(nodes, "sg__s1");
    expect(innerNode).toBeDefined();
  });

  // -----------------------------------------------------------------------
  // Start/end node structure
  // -----------------------------------------------------------------------
  it("places start node first and end node last", () => {
    const data = { nodes: [makeApiNode("x")], node_connections: [] };
    const { nodes } = buildExecutionGraph(data);

    expect(nodes[0].id).toBe(START_ID);
    expect(nodes[nodes.length - 1].id).toBe(END_ID);
  });

  // -----------------------------------------------------------------------
  // Edge styling
  // -----------------------------------------------------------------------
  it("applies default edge styling to all edges", () => {
    const data = { nodes: [makeApiNode("a")], node_connections: [] };
    const { edges } = buildExecutionGraph(data);

    for (const edge of edges) {
      expect(edge.style).toBeDefined();
      expect(edge.style.stroke).toBeDefined();
      expect(edge.style.strokeWidth).toBeDefined();
      expect(edge.style.strokeDasharray).toBeDefined();
    }
  });

  // -----------------------------------------------------------------------
  // Subgraph inner node data fields
  // -----------------------------------------------------------------------
  it("sets correct data fields on subgraph inner nodes", () => {
    const data = {
      nodes: [
        makeApiNode("sg", {
          type: "subgraph",
          subGraph: {
            id: "sg-exec",
            nodes: [
              {
                id: "c1",
                name: "Child",
                type: "atomic",
                nodeExecution: { id: "ne-c1", status: "success" },
                subGraph: { id: "nested", nodes: [{ id: "deep" }] },
              },
            ],
            node_connections: [],
          },
        }),
      ],
      node_connections: [],
    };

    const { nodes } = buildExecutionGraph(data);
    const child = findNode(nodes, "sg__c1");
    expect(child.data.originalId).toBe("c1");
    expect(child.data.apiNodeType).toBe("atomic");
    expect(child.data.frontendNodeType).toBe("llm_prompt");
    expect(child.data.nodeExecution).toEqual({
      id: "ne-c1",
      status: "success",
    });
    expect(child.data.hasSubGraph).toBe(true);
  });

  it("makes skipped nodes non-selectable while preserving selectable statuses", () => {
    const data = {
      nodes: [
        makeApiNode("skipped", { nodeExecution: { status: "skipped" } }),
        makeApiNode("success", { nodeExecution: { status: "success" } }),
      ],
      node_connections: [],
    };

    const { nodes } = buildExecutionGraph(data);

    expect(findNode(nodes, "skipped")).toMatchObject({
      selectable: false,
      focusable: false,
    });
    expect(findNode(nodes, "success")).toMatchObject({
      selectable: true,
      focusable: true,
    });
  });

  // -----------------------------------------------------------------------
  // snake_case: node_execution
  // -----------------------------------------------------------------------
  it("resolves node_execution snake_case for execution data", () => {
    const data = {
      nodes: [
        makeApiNode("a", { node_execution: { id: "ne-sc", status: "done" } }),
      ],
      node_connections: [],
    };
    const { nodes } = buildExecutionGraph(data);
    const nodeA = findNode(nodes, "a");
    expect(nodeA.data.nodeExecution).toEqual({ id: "ne-sc", status: "done" });
  });

  // -----------------------------------------------------------------------
  // snake_case: sub_graph
  // -----------------------------------------------------------------------
  it("detects sub_graph snake_case and lays out as group", () => {
    const nodeWithSnakeCase = {
      id: "sg-sc",
      name: "SG SC",
      type: "subgraph",
      sub_graph: {
        id: "sg-sc-exec",
        nodes: [
          {
            id: "sc-inner",
            name: "SC Inner",
            type: "atomic",
          },
        ],
        node_connections: [],
      },
    };
    const data = { nodes: [nodeWithSnakeCase], node_connections: [] };
    const { nodes } = buildExecutionGraph(data);

    const group = findNode(nodes, "sg-sc");
    expect(group.type).toBe("subgraphGroup");
  });

  // -----------------------------------------------------------------------
  // node_connections maps to RF edges
  // -----------------------------------------------------------------------
  it("maps node_connections to node-to-node RF edges", () => {
    const data = {
      nodes: [makeApiNode("x"), makeApiNode("y")],
      node_connections: [makeConnection("x", "y")],
    };
    const { edges } = buildExecutionGraph(data);
    const mainEdge = edges.find((e) => e.source === "x" && e.target === "y");
    expect(mainEdge).toBeDefined();
  });

  // -----------------------------------------------------------------------
  // Empty subgraph treated as regular node
  // -----------------------------------------------------------------------
  it("treats node with empty subgraph as regular execution node", () => {
    const data = {
      nodes: [
        makeApiNode("sg-empty", {
          type: "subgraph",
          subGraph: { id: "sg-e", nodes: [], node_connections: [] },
        }),
      ],
      node_connections: [],
    };
    const { nodes } = buildExecutionGraph(data);
    const node = findNode(nodes, "sg-empty");
    expect(node.type).toBe("executionNode");
  });

  // -----------------------------------------------------------------------
  // Multiple subgraphs with same inner IDs — no collisions
  // -----------------------------------------------------------------------
  it("namespaces inner nodes to avoid collisions across subgraphs", () => {
    const makeSubGraph = (parentId) => ({
      id: `exec-${parentId}`,
      nodes: [
        {
          id: "shared-inner",
          name: "Shared",
          type: "atomic",
        },
      ],
      node_connections: [],
    });

    const data = {
      nodes: [
        makeApiNode("sg-a", {
          type: "subgraph",
          subGraph: makeSubGraph("sg-a"),
        }),
        makeApiNode("sg-b", {
          type: "subgraph",
          subGraph: makeSubGraph("sg-b"),
        }),
      ],
      node_connections: [],
    };

    const { nodes } = buildExecutionGraph(data);

    const innerA = findNode(nodes, "sg-a__shared-inner");
    const innerB = findNode(nodes, "sg-b__shared-inner");
    expect(innerA).toBeDefined();
    expect(innerB).toBeDefined();
    expect(innerA.id).not.toBe(innerB.id);
  });

  // -----------------------------------------------------------------------
  // Connections with missing node IDs are filtered
  // -----------------------------------------------------------------------
  it("filters out connections with missing node IDs", () => {
    const data = {
      nodes: [makeApiNode("a")],
      node_connections: [{ source_node_id: null, target_node_id: "a" }],
    };
    const { edges } = buildExecutionGraph(data);
    const mainEdges = edges.filter(
      (e) => e.source !== START_ID && e.target !== END_ID,
    );
    expect(mainEdges).toHaveLength(0);
  });
});
