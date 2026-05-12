import { describe, expect, it, beforeEach } from "vitest";
import { openFixWithFalcon } from "../helpers/openFixWithFalcon";
import useFalconStore from "../store/useFalconStore";

beforeEach(() => {
  useFalconStore.getState().resetAll();
});

describe("openFixWithFalcon", () => {
  it("opens a fresh sidebar chat with the fix prompt", () => {
    const store = useFalconStore.getState();
    store.setCurrentConversation("old-conversation");
    store.addMessage({ id: "m1", role: "user", content: "old chat" });

    openFixWithFalcon({
      level: "trace",
      context: { trace_id: "trace-123", project_id: "project-456" },
    });

    const next = useFalconStore.getState();
    expect(next.isSidebarOpen).toBe(true);
    expect(next.currentConversationId).toBeNull();
    expect(next.messages).toEqual([]);
    expect(next.pendingPrompt).toContain("/fix-with-falcon");
    expect(next.pendingPrompt).toContain("trace-123");
    expect(next.pendingPrompt).toContain("project-456");
  });
});
