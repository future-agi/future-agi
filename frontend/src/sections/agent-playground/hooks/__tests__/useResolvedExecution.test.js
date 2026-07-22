import { describe, it, expect } from "vitest";
import { renderHook } from "@testing-library/react";
import useResolvedExecution from "../useResolvedExecution";

const EXEC_ID = "exec-root";

const executionData = {
  nodes: [
    { id: "node-1", nodeExecution: { id: "ne-1" } },
    {
      id: "node-2",
      subGraph: {
        id: "sg-exec-1",
        nodes: [
          { id: "inner-1", nodeExecution: { id: "ne-inner-1" } },
          { id: "inner-2", nodeExecution: { id: "ne-inner-2" } },
        ],
      },
    },
    { id: "node-3" }, // pending — no nodeExecution
  ],
};

describe("useResolvedExecution", () => {
  const run = (selectedNodeId, data = executionData, execId = EXEC_ID) =>
    renderHook(() =>
      useResolvedExecution({
        selectedNodeId,
        executionData: data,
        executionId: execId,
      }),
    ).result.current;

  // -----------------------------------------------------------------------
  // Null / missing inputs
  // -----------------------------------------------------------------------
  it("returns nulls when selectedNodeId is null", () => {
    const { nodeExecutionId, resolvedExecutionId } = run(null);
    expect(nodeExecutionId).toBeNull();
    expect(resolvedExecutionId).toBe(EXEC_ID);
  });

  it("returns nulls when executionData is null", () => {
    const { nodeExecutionId, resolvedExecutionId } = run("node-1", null);
    expect(nodeExecutionId).toBeNull();
    expect(resolvedExecutionId).toBe(EXEC_ID);
  });

  it("returns nulls when executionData.nodes is undefined", () => {
    const { nodeExecutionId, resolvedExecutionId } = run("node-1", {});
    expect(nodeExecutionId).toBeNull();
    expect(resolvedExecutionId).toBe(EXEC_ID);
  });

  // -----------------------------------------------------------------------
  // Top-level nodes
  // -----------------------------------------------------------------------
  it("resolves top-level node with execution", () => {
    const { nodeExecutionId, resolvedExecutionId } = run("node-1");
    expect(nodeExecutionId).toBe("ne-1");
    expect(resolvedExecutionId).toBe(EXEC_ID);
  });

  it("resolves top-level node_execution snake_case variant", () => {
    const snakeCaseData = {
      nodes: [{ id: "node-snake", node_execution: { id: "ne-snake" } }],
    };
    const { nodeExecutionId, resolvedExecutionId } = run(
      "node-snake",
      snakeCaseData,
    );
    expect(nodeExecutionId).toBe("ne-snake");
    expect(resolvedExecutionId).toBe(EXEC_ID);
  });

  it("returns null nodeExecutionId for pending top-level node", () => {
    const { nodeExecutionId, resolvedExecutionId } = run("node-3");
    expect(nodeExecutionId).toBeNull();
    expect(resolvedExecutionId).toBe(EXEC_ID);
  });

  it("returns null nodeExecutionId for nonexistent top-level node", () => {
    const { nodeExecutionId, resolvedExecutionId } = run("nonexistent");
    expect(nodeExecutionId).toBeNull();
    expect(resolvedExecutionId).toBe(EXEC_ID);
  });

  // -----------------------------------------------------------------------
  // Subgraph inner nodes
  // -----------------------------------------------------------------------
  it("resolves first subgraph inner node", () => {
    const { nodeExecutionId, resolvedExecutionId } = run("node-2__inner-1");
    expect(nodeExecutionId).toBe("ne-inner-1");
    expect(resolvedExecutionId).toBe("sg-exec-1");
  });

  it("resolves second subgraph inner node", () => {
    const { nodeExecutionId, resolvedExecutionId } = run("node-2__inner-2");
    expect(nodeExecutionId).toBe("ne-inner-2");
    expect(resolvedExecutionId).toBe("sg-exec-1");
  });

  it("returns null nodeExecutionId when inner node not found", () => {
    const { nodeExecutionId, resolvedExecutionId } = run("node-2__nonexistent");
    expect(nodeExecutionId).toBeNull();
    expect(resolvedExecutionId).toBe("sg-exec-1");
  });

  it("returns null for both when parent node not found", () => {
    const { nodeExecutionId, resolvedExecutionId } = run("bad-id__inner-1");
    expect(nodeExecutionId).toBeNull();
    expect(resolvedExecutionId).toBeNull();
  });

  // -----------------------------------------------------------------------
  // snake_case variant (sub_graph)
  // -----------------------------------------------------------------------
  it("resolves sub_graph snake_case variant for resolvedExecutionId", () => {
    const snakeCaseData = {
      nodes: [
        {
          id: "node-s",
          sub_graph: {
            id: "sg-snake",
            nodes: [{ id: "s-inner", node_execution: { id: "ne-s-inner" } }],
          },
        },
      ],
    };
    const { nodeExecutionId, resolvedExecutionId } = run(
      "node-s__s-inner",
      snakeCaseData,
    );
    expect(resolvedExecutionId).toBe("sg-snake");
    // sub_graph fallback now handled — inner node is found
    expect(nodeExecutionId).toBe("ne-s-inner");
  });

  // -----------------------------------------------------------------------
  // Memoization
  // -----------------------------------------------------------------------
  it("returns stable references when inputs do not change", () => {
    const { result, rerender } = renderHook(() =>
      useResolvedExecution({
        selectedNodeId: "node-1",
        executionData,
        executionId: EXEC_ID,
      }),
    );
    const first = result.current;
    rerender();
    const second = result.current;
    expect(first.nodeExecutionId).toBe(second.nodeExecutionId);
    expect(first.resolvedExecutionId).toBe(second.resolvedExecutionId);
  });
});
