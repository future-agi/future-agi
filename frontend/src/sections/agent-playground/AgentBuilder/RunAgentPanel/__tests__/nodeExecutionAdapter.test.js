import { describe, it, expect } from "vitest";
import {
  mapExecutionNodesToTreeNodes,
  __test__,
} from "../nodeExecutionAdapter";

const { readDurationMs, mapNode } = __test__;

describe("nodeExecutionAdapter", () => {
  describe("mapExecutionNodesToTreeNodes", () => {
    it("returns an empty array when input is undefined", () => {
      expect(mapExecutionNodesToTreeNodes(undefined)).toEqual([]);
    });

    it("returns an empty array when input is null", () => {
      expect(mapExecutionNodesToTreeNodes(null)).toEqual([]);
    });

    it("returns an empty array when input is not an array", () => {
      expect(mapExecutionNodesToTreeNodes({ id: "x" })).toEqual([]);
    });

    it("drops nodes that have no id", () => {
      const out = mapExecutionNodesToTreeNodes([
        { id: "a", name: "A", type: "atomic" },
        { name: "no-id" },
        null,
        undefined,
        { id: 42, name: "numeric" }, // wrong type
      ]);
      expect(out).toHaveLength(1);
      expect(out[0].id).toBe("a");
    });

    it("maps the type field through the API → frontend mapping", () => {
      const out = mapExecutionNodesToTreeNodes([
        { id: "a", type: "atomic", name: "A" },
        { id: "b", type: "subgraph", name: "B" },
        { id: "c", type: "eval", name: "C" },
        { id: "d", type: "agent", name: "D" },
        { id: "e", type: "weird", name: "E" },
        { id: "f", name: "F" }, // no type — defaults to agent
      ]);
      expect(out.map((n) => n.type)).toEqual([
        "prompt",
        "agent",
        "eval",
        "agent",
        "weird",
        "agent",
      ]);
    });

    it("preserves name, fills missing name with id", () => {
      const out = mapExecutionNodesToTreeNodes([
        { id: "a", type: "atomic", name: "Prompt A" },
        { id: "b", type: "atomic" },
      ]);
      expect(out[0].name).toBe("Prompt A");
      expect(out[1].name).toBe("b");
    });

    it("recurses into subGraph.nodes for subgraph nodes", () => {
      const out = mapExecutionNodesToTreeNodes([
        {
          id: "agent-1",
          type: "subgraph",
          name: "Agent 1",
          subGraph: {
            nodes: [
              { id: "inner-1", type: "atomic", name: "Inner 1" },
              { id: "inner-2", type: "eval", name: "Inner 2" },
            ],
          },
        },
      ]);
      expect(out).toHaveLength(1);
      expect(out[0].children).toHaveLength(2);
      expect(out[0].children[0].name).toBe("Inner 1");
      expect(out[0].children[1].type).toBe("eval");
    });

    it("does not set children when subGraph has no nodes", () => {
      const out = mapExecutionNodesToTreeNodes([
        { id: "a", type: "atomic", name: "A", subGraph: { nodes: [] } },
        { id: "b", type: "atomic", name: "B", subGraph: null },
        { id: "c", type: "atomic", name: "C" },
      ]);
      for (const node of out) {
        expect(node.children).toBeUndefined();
      }
    });

    it("defaults duration, cost, tokens to 0 when missing", () => {
      const out = mapExecutionNodesToTreeNodes([
        { id: "a", type: "atomic", name: "A" },
      ]);
      expect(out[0].duration).toBe(0);
      expect(out[0].cost).toBe(0);
      expect(out[0].tokens).toBe(0);
    });
  });

  describe("readDurationMs", () => {
    it("returns 0 for null / undefined / non-object input", () => {
      expect(readDurationMs(null)).toBe(0);
      expect(readDurationMs(undefined)).toBe(0);
      expect(readDurationMs("nope")).toBe(0);
    });

    it("prefers the top-level duration_ms field", () => {
      expect(readDurationMs({ duration_ms: 250 })).toBe(250);
      expect(
        readDurationMs({ duration_ms: 250, nodeExecution: { duration: 999 } }),
      ).toBe(250);
    });

    it("falls back to camelCase durationMs", () => {
      expect(readDurationMs({ durationMs: 320 })).toBe(320);
    });

    it("falls back to top-level duration", () => {
      expect(readDurationMs({ duration: 410 })).toBe(410);
    });

    it("falls back to nodeExecution.duration_ms", () => {
      expect(
        readDurationMs({ nodeExecution: { duration_ms: 500 } }),
      ).toBe(500);
      expect(
        readDurationMs({ node_execution: { duration_ms: 600 } }),
      ).toBe(600);
    });

    it("converts duration_seconds (× 1000) when present", () => {
      expect(readDurationMs({ duration_seconds: 1.5 })).toBe(1500);
      expect(
        readDurationMs({ nodeExecution: { duration_seconds: 2 } }),
      ).toBe(2000);
    });

    it("accepts numeric strings", () => {
      expect(readDurationMs({ duration_ms: "1234" })).toBe(1234);
    });

    it("ignores non-numeric strings", () => {
      expect(readDurationMs({ duration_ms: "abc" })).toBe(0);
    });

    it("ignores NaN / Infinity", () => {
      expect(readDurationMs({ duration_ms: NaN })).toBe(0);
      expect(readDurationMs({ duration_ms: Infinity })).toBe(0);
    });
  });

  describe("mapNode", () => {
    it("returns null for invalid input", () => {
      expect(mapNode(null)).toBeNull();
      expect(mapNode(undefined)).toBeNull();
      expect(mapNode({})).toBeNull();
      expect(mapNode({ id: 42 })).toBeNull();
    });

    it("includes cost and tokens when present on the node", () => {
      const out = mapNode({
        id: "a",
        type: "atomic",
        name: "A",
        cost: 0.0021,
        tokens: 1234,
      });
      expect(out.cost).toBe(0.0021);
      expect(out.tokens).toBe(1234);
    });

    it("falls back to nodeExecution.cost and nodeExecution.tokens", () => {
      const out = mapNode({
        id: "a",
        type: "atomic",
        name: "A",
        nodeExecution: { cost: 0.05, total_tokens: 99 },
      });
      expect(out.cost).toBe(0.05);
      expect(out.tokens).toBe(99);
    });

    it("uses snake_case node_execution alias as well", () => {
      const out = mapNode({
        id: "a",
        type: "atomic",
        name: "A",
        node_execution: { cost: 0.07, tokens: 17 },
      });
      expect(out.cost).toBe(0.07);
      expect(out.tokens).toBe(17);
    });
  });
});