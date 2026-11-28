import { describe, it, expect, beforeEach } from "vitest";

let useFalconStore;

beforeEach(async () => {
  const mod = await import("../store/useFalconStore");
  useFalconStore = mod.default;
  useFalconStore.getState().resetAll();
});

describe("useFalconStore", () => {
  // --- Sidebar state ---

  it("should start with sidebar closed", () => {
    expect(useFalconStore.getState().isSidebarOpen).toBe(false);
  });

  it("should open sidebar", () => {
    useFalconStore.getState().openSidebar();
    expect(useFalconStore.getState().isSidebarOpen).toBe(true);
  });

  it("should close sidebar", () => {
    useFalconStore.getState().openSidebar();
    useFalconStore.getState().closeSidebar();
    expect(useFalconStore.getState().isSidebarOpen).toBe(false);
  });

  it("should toggle sidebar", () => {
    useFalconStore.getState().toggleSidebar();
    expect(useFalconStore.getState().isSidebarOpen).toBe(true);
    useFalconStore.getState().toggleSidebar();
    expect(useFalconStore.getState().isSidebarOpen).toBe(false);
  });

  // --- Messages ---

  it("should add message", () => {
    useFalconStore
      .getState()
      .addMessage({ id: "1", role: "user", content: "Hello" });
    expect(useFalconStore.getState().messages).toHaveLength(1);
    expect(useFalconStore.getState().messages[0].content).toBe("Hello");
  });

  it("should add multiple messages", () => {
    useFalconStore
      .getState()
      .addMessage({ id: "1", role: "user", content: "Hi" });
    useFalconStore
      .getState()
      .addMessage({ id: "2", role: "assistant", content: "Hey" });
    expect(useFalconStore.getState().messages).toHaveLength(2);
  });

  it("should append text delta", () => {
    useFalconStore
      .getState()
      .addMessage({ id: "msg1", role: "assistant", content: "" });
    useFalconStore.getState().appendTextDelta("msg1", "Hello ");
    useFalconStore.getState().appendTextDelta("msg1", "world!");
    expect(useFalconStore.getState().messages[0].content).toBe("Hello world!");
  });

  it("should append text delta to correct message", () => {
    useFalconStore
      .getState()
      .addMessage({ id: "m1", role: "user", content: "Hi" });
    useFalconStore
      .getState()
      .addMessage({ id: "m2", role: "assistant", content: "" });
    useFalconStore.getState().appendTextDelta("m2", "Response");
    expect(useFalconStore.getState().messages[0].content).toBe("Hi");
    expect(useFalconStore.getState().messages[1].content).toBe("Response");
  });

  it("should update message", () => {
    useFalconStore
      .getState()
      .addMessage({ id: "1", role: "assistant", content: "Hello" });
    useFalconStore.getState().updateMessage("1", { feedback: "thumbs_down" });
    expect(useFalconStore.getState().messages[0].feedback).toBe("thumbs_down");
  });

  it("should set messages array", () => {
    useFalconStore.getState().setMessages([
      { id: "a", role: "user", content: "Q" },
      { id: "b", role: "assistant", content: "A" },
    ]);
    expect(useFalconStore.getState().messages).toHaveLength(2);
  });

  // --- Tool calls (on messages) ---

  it("should add tool call to a message", () => {
    useFalconStore.getState().addMessage({
      id: "msg1",
      role: "assistant",
      content: "",
      tool_calls: [],
    });
    useFalconStore.getState().addToolCall("msg1", {
      call_id: "tc_1",
      tool_name: "search_traces",
      tool_description: "Search traces",
      params: { limit: 10 },
      status: "running",
      step: 1,
    });
    const msg = useFalconStore.getState().messages[0];
    expect(msg.tool_calls).toHaveLength(1);
    expect(msg.tool_calls[0].tool_name).toBe("search_traces");
    expect(msg.tool_calls[0].status).toBe("running");
  });

  it("should add tool call even when tool_calls is undefined", () => {
    useFalconStore
      .getState()
      .addMessage({ id: "msg1", role: "assistant", content: "" });
    useFalconStore.getState().addToolCall("msg1", {
      call_id: "tc_1",
      tool_name: "whoami",
      status: "running",
      step: 1,
    });
    const msg = useFalconStore.getState().messages[0];
    expect(msg.tool_calls).toHaveLength(1);
  });

  it("should update tool call by call_id", () => {
    useFalconStore.getState().addMessage({
      id: "msg1",
      role: "assistant",
      content: "",
      tool_calls: [],
    });
    useFalconStore.getState().addToolCall("msg1", {
      call_id: "tc_1",
      tool_name: "search_traces",
      status: "running",
      step: 1,
    });
    useFalconStore.getState().updateToolCall("msg1", "tc_1", {
      status: "completed",
      result_summary: "Found 5 traces",
      result_full: "trace-1, trace-2, ...",
    });
    const tc = useFalconStore.getState().messages[0].tool_calls[0];
    expect(tc.status).toBe("completed");
    expect(tc.result_summary).toBe("Found 5 traces");
    expect(tc.result_full).toBe("trace-1, trace-2, ...");
  });

  it("should add multiple tool calls to a message", () => {
    useFalconStore.getState().addMessage({
      id: "msg1",
      role: "assistant",
      content: "",
      tool_calls: [],
    });
    useFalconStore.getState().addToolCall("msg1", {
      call_id: "tc_1",
      tool_name: "search_traces",
      status: "running",
      step: 1,
    });
    useFalconStore.getState().addToolCall("msg1", {
      call_id: "tc_2",
      tool_name: "list_datasets",
      status: "running",
      step: 2,
    });
    expect(useFalconStore.getState().messages[0].tool_calls).toHaveLength(2);
  });

  it("should only update the matching tool call", () => {
    useFalconStore.getState().addMessage({
      id: "msg1",
      role: "assistant",
      content: "",
      tool_calls: [],
    });
    useFalconStore.getState().addToolCall("msg1", {
      call_id: "tc_1",
      tool_name: "search_traces",
      status: "running",
      step: 1,
    });
    useFalconStore.getState().addToolCall("msg1", {
      call_id: "tc_2",
      tool_name: "list_datasets",
      status: "running",
      step: 2,
    });
    useFalconStore.getState().updateToolCall("msg1", "tc_1", {
      status: "completed",
      result_summary: "Done",
    });
    const tcs = useFalconStore.getState().messages[0].tool_calls;
    expect(tcs[0].status).toBe("completed");
    expect(tcs[1].status).toBe("running");
  });

  // --- Streaming ---

  it("should set streaming state with message id", () => {
    useFalconStore.getState().setStreaming(true, "msg1");
    expect(useFalconStore.getState().isStreaming).toBe(true);
    expect(useFalconStore.getState().streamingMessageId).toBe("msg1");
  });

  it("should set streaming false with null message id", () => {
    useFalconStore.getState().setStreaming(true, "msg1");
    useFalconStore.getState().setStreaming(false);
    expect(useFalconStore.getState().isStreaming).toBe(false);
    expect(useFalconStore.getState().streamingMessageId).toBeNull();
  });

  // --- Conversations ---

  it("should set conversations", () => {
    useFalconStore.getState().setConversations([{ id: "c1", title: "Chat 1" }]);
    expect(useFalconStore.getState().conversations).toHaveLength(1);
    expect(useFalconStore.getState().conversations[0].title).toBe("Chat 1");
  });

  it("should set current conversation and clear messages", () => {
    useFalconStore
      .getState()
      .addMessage({ id: "1", role: "user", content: "Hi" });
    useFalconStore.getState().setCurrentConversation("c2");
    expect(useFalconStore.getState().currentConversationId).toBe("c2");
    expect(useFalconStore.getState().messages).toHaveLength(0);
  });

  it("should clear streaming state when switching conversations", () => {
    useFalconStore.getState().setCurrentConversation("c1");
    useFalconStore.getState().setStreaming(true, "assistant-1");
    useFalconStore.getState().setCurrentConversation("c2");
    expect(useFalconStore.getState().currentConversationId).toBe("c2");
    expect(useFalconStore.getState().isStreaming).toBe(false);
    expect(useFalconStore.getState().streamingMessageId).toBeNull();
  });

  // --- Reset ---

  it("should reset chat state", () => {
    useFalconStore
      .getState()
      .addMessage({ id: "1", role: "user", content: "Hi" });
    useFalconStore.getState().setStreaming(true, "msg1");
    useFalconStore.getState().resetChat();
    expect(useFalconStore.getState().messages).toHaveLength(0);
    expect(useFalconStore.getState().isStreaming).toBe(false);
    expect(useFalconStore.getState().streamingMessageId).toBeNull();
  });

  it("should reset all state", () => {
    useFalconStore.getState().openSidebar();
    useFalconStore.getState().setConversations([{ id: "c1", title: "C" }]);
    useFalconStore.getState().setCurrentConversation("c1");
    useFalconStore.getState().setStreaming(true);
    useFalconStore.getState().resetAll();
    expect(useFalconStore.getState().isSidebarOpen).toBe(false);
    expect(useFalconStore.getState().conversations).toHaveLength(0);
    expect(useFalconStore.getState().currentConversationId).toBeNull();
    expect(useFalconStore.getState().messages).toHaveLength(0);
    expect(useFalconStore.getState().isStreaming).toBe(false);
  });
});
