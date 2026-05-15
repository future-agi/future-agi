import { describe, it, expect, beforeEach } from "vitest";

import { resetAllUserStores } from "../clearStores";
import useFalconStore from "src/sections/falcon-ai/store/useFalconStore";
import { useAgentPlaygroundStore } from "src/sections/agent-playground/store/agent-playground-store";
import { useWorkflowRunStore } from "src/sections/agent-playground/store/workflow-run-store";
import { useGlobalVariablesDrawerStore } from "src/sections/agent-playground/store/global-variables-drawer-store";
import { useAgentDetailsStore } from "src/sections/agents/store/agentDetailsStore";
import { usePromptStore } from "src/sections/workbench-v2/store/usePromptStore";
import { useGraphStore } from "src/components/GraphBuilder/store/graphStore";

beforeEach(() => {
  resetAllUserStores();
});

describe("resetAllUserStores", () => {
  it("resets Falcon, agent-playground, workflow-run, globals, agent-details, prompt, and graph stores in one call", () => {
    useFalconStore.getState().openSidebar();
    useFalconStore
      .getState()
      .addMessage({ id: "m1", role: "user", content: "leaked" });
    useAgentPlaygroundStore.setState({ currentAgent: { id: "agent-1" } });
    useWorkflowRunStore
      .getState()
      .setRunResults("node-1", { output: "leaked-run" });
    useGlobalVariablesDrawerStore
      .getState()
      .setGlobalVariables({ apiKey: "sk-leaked" });
    useAgentDetailsStore.getState().setAgentName("leaked-agent");
    useAgentDetailsStore.getState().setLatestVersionNumber(42);
    usePromptStore.setState({ searchQuery: "leaked-search" });
    useGraphStore.getState().addNode("conversation", { x: 0, y: 0 }, false);

    const initialGraphNodeCount = 1;
    expect(useFalconStore.getState().isSidebarOpen).toBe(true);
    expect(useFalconStore.getState().messages.length).toBeGreaterThan(0);
    expect(useAgentPlaygroundStore.getState().currentAgent).not.toBeNull();
    expect(
      Object.keys(useWorkflowRunStore.getState().runResults).length,
    ).toBeGreaterThan(0);
    expect(useGlobalVariablesDrawerStore.getState().globalVariables).toEqual({
      apiKey: "sk-leaked",
    });
    expect(useAgentDetailsStore.getState().agentName).toBe("leaked-agent");
    expect(usePromptStore.getState().searchQuery).toBe("leaked-search");
    expect(useGraphStore.getState().nodes.length).toBeGreaterThan(
      initialGraphNodeCount,
    );

    resetAllUserStores();

    expect(useFalconStore.getState().isSidebarOpen).toBe(false);
    expect(useFalconStore.getState().messages).toEqual([]);
    expect(useAgentPlaygroundStore.getState().currentAgent).toBeNull();
    expect(useWorkflowRunStore.getState().runResults).toEqual({});
    expect(useGlobalVariablesDrawerStore.getState().globalVariables).toEqual(
      {},
    );
    expect(useAgentDetailsStore.getState().agentName).toBe("");
    expect(useAgentDetailsStore.getState().latestVersionNumber).toBe(0);
    expect(usePromptStore.getState().searchQuery).toBe("");
    expect(useGraphStore.getState().nodes.length).toBe(initialGraphNodeCount);
    expect(useGraphStore.getState().edges).toEqual([]);
  });

  it("continues resetting remaining stores when one reset throws", () => {
    useFalconStore.getState().openSidebar();
    usePromptStore.setState({ searchQuery: "leaked-after-throw" });

    const original = useAgentDetailsStore.getState().resetAgentDetails;
    useAgentDetailsStore.setState({
      resetAgentDetails: () => {
        throw new Error("simulated reset failure");
      },
    });

    try {
      expect(() => resetAllUserStores()).not.toThrow();
      expect(useFalconStore.getState().isSidebarOpen).toBe(false);
      expect(usePromptStore.getState().searchQuery).toBe("");
    } finally {
      useAgentDetailsStore.setState({ resetAgentDetails: original });
    }
  });

  it("is idempotent", () => {
    expect(() => {
      resetAllUserStores();
      resetAllUserStores();
      resetAllUserStores();
    }).not.toThrow();
    expect(useFalconStore.getState().isSidebarOpen).toBe(false);
  });
});
