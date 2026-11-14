import { describe, expect, it, vi } from "vitest";

const { loadGraphLibrary } = vi.hoisted(() => ({
  loadGraphLibrary: vi.fn(),
}));

vi.mock("@xyflow/react", async (importOriginal) => {
  loadGraphLibrary();
  return importOriginal();
});

import { resetAllUserStores } from "../clearStores";

describe("lazy graph store resets", () => {
  it("does not load the graph library for logout, then resets late-loaded stores synchronously", async () => {
    expect(loadGraphLibrary).not.toHaveBeenCalled();
    expect(resetAllUserStores()).toBeUndefined();
    expect(loadGraphLibrary).not.toHaveBeenCalled();

    const { useAgentPlaygroundStore } = await import(
      "src/sections/agent-playground/store/agent-playground-store"
    );
    const { useGraphStore } = await import(
      "src/components/GraphBuilder/store/graphStore"
    );
    expect(loadGraphLibrary).toHaveBeenCalledOnce();
    expect(useAgentPlaygroundStore.getState().currentAgent).toBeNull();
    expect(useGraphStore.getState().nodes).toHaveLength(1);

    const initialGraphNodeId = useGraphStore.getState().nodes[0].id;
    useAgentPlaygroundStore.setState({
      currentAgent: { id: "previous-agent" },
      nodes: [{ id: "previous-node" }],
      _pendingNodeSelection: "previous-node",
      _isDraftCreating: true,
    });
    useGraphStore.getState().clearGraph();
    useGraphStore.getState().setActiveNode("previous-node");

    const graphResetStates: ReturnType<typeof useGraphStore.getState>[] = [];
    const unsubscribe = useGraphStore.subscribe(
      (state: ReturnType<typeof useGraphStore.getState>) => {
        graphResetStates.push(state);
      },
    );
    try {
      expect(resetAllUserStores()).toBeUndefined();
      expect(useAgentPlaygroundStore.getState()).toEqual(
        useAgentPlaygroundStore.getInitialState(),
      );
      // The generic reset runs first; custom cleanup still creates a fresh node.
      expect(graphResetStates).toHaveLength(2);
      expect(graphResetStates[0]).toBe(useGraphStore.getInitialState());
      expect(graphResetStates[1].nodes).toHaveLength(1);
      expect(graphResetStates[1].nodes[0].id).not.toBe(initialGraphNodeId);
      expect(graphResetStates[1].activeNodeId).toBeNull();
      expect(graphResetStates[1].edges).toEqual([]);

      // Registrations survive logout for another user in the same tab.
      useAgentPlaygroundStore.setState({ currentAgent: { id: "next-agent" } });
      resetAllUserStores();
      expect(useAgentPlaygroundStore.getState().currentAgent).toBeNull();
      expect(useGraphStore.getState().nodes[0].id).not.toBe(
        graphResetStates[1].nodes[0].id,
      );
    } finally {
      unsubscribe();
    }
  });
});
