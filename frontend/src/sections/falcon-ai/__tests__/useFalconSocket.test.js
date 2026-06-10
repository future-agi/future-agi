import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook } from "@testing-library/react";
import useFalconStore from "../store/useFalconStore";
import { useFalconSocket } from "../hooks/useFalconSocket";

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { accessToken: "test-token" } }),
}));

vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "ws-1" }),
}));

vi.mock("src/config-global", () => ({
  HOST_API: "http://localhost:8002",
}));

/**
 * Minimal WebSocket double. The hook drives the real API surface
 * (constructor, readyState, send, close, on* handlers); tests drive the
 * server side via open()/receive()/drop().
 */
class MockWebSocket {
  static CONNECTING = 0;

  static OPEN = 1;

  static CLOSING = 2;

  static CLOSED = 3;

  static instances = [];

  constructor(url) {
    this.url = url;
    this.readyState = MockWebSocket.CONNECTING;
    this.sent = [];
    this.onopen = null;
    this.onmessage = null;
    this.onerror = null;
    this.onclose = null;
    MockWebSocket.instances.push(this);
  }

  send(data) {
    this.sent.push(JSON.parse(data));
  }

  close() {
    this.readyState = MockWebSocket.CLOSED;
  }

  // --- test drivers (not part of the WebSocket API) ---

  open() {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.();
  }

  receive(type, data) {
    this.onmessage?.({ data: JSON.stringify({ type, data }) });
  }

  drop(code = 1006) {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.({ code });
  }

  framesOfType(type) {
    return this.sent.filter((f) => f.type === type);
  }
}

function mountSocket() {
  const utils = renderHook(() => useFalconSocket());
  const ws = MockWebSocket.instances[MockWebSocket.instances.length - 1];
  return { ...utils, ws };
}

/**
 * Reproduce what the UI does on send/select: pick the conversation, create
 * the "assistant-<ts>" placeholder, and mark it as the streaming target.
 */
function beginStream(conversationId, placeholderId = "assistant-1") {
  const store = useFalconStore.getState();
  store.setCurrentConversation(conversationId);
  store.addMessage({
    id: placeholderId,
    role: "assistant",
    content: "",
    created_at: new Date().toISOString(),
  });
  store.setStreaming(true, placeholderId);
  return placeholderId;
}

/** Begin a stream and remap the placeholder to a backend id via a first event. */
function liveStream(ws, { convId = "conv-1", backendId = "uuid-1" } = {}) {
  beginStream(convId);
  ws.receive("iteration_start", {
    message_id: backendId,
    iteration: 1,
    max_iterations: 10,
    conversation_id: convId,
  });
  return backendId;
}

beforeEach(() => {
  vi.useFakeTimers();
  MockWebSocket.instances = [];
  vi.stubGlobal("WebSocket", MockWebSocket);
  useFalconStore.getState().resetAll();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("useFalconSocket (unit)", () => {
  describe("connection", () => {
    it("opens a websocket with the auth token and workspace id", () => {
      const { ws } = mountSocket();
      expect(ws.url).toContain("ws://localhost:8002/ws/falcon-ai/?");
      expect(ws.url).toContain("token=test-token");
      expect(ws.url).toContain("workspace_id=ws-1");
    });

    it("sends keepalive pings every 30s while open", () => {
      const { ws } = mountSocket();
      ws.open();
      vi.advanceTimersByTime(30000);
      expect(ws.framesOfType("ping")).toHaveLength(1);
      vi.advanceTimersByTime(30000);
      expect(ws.framesOfType("ping")).toHaveLength(2);
    });

    it("does not retry after auth-related close codes", () => {
      const { ws } = mountSocket();
      ws.open();
      ws.drop(4401);
      vi.advanceTimersByTime(60000);
      expect(MockWebSocket.instances).toHaveLength(1);
    });
  });

  describe("reconnect race (regression: dropped answers / stuck spinner)", () => {
    it("fresh send: socket opening after the send flushes the chat but sends NO reconnect frame", () => {
      const { ws, result } = mountSocket();
      // The UI sets isStreaming=true at send time — the exact state that
      // used to trigger a spurious reconnect on the subsequent open.
      beginStream("conv-1");
      result.current.sendChat("hello", "conv-1", { page: "dashboard" });
      expect(ws.sent).toHaveLength(0); // nothing sent while CONNECTING

      ws.open();
      expect(ws.framesOfType("chat")).toHaveLength(1);
      expect(ws.framesOfType("reconnect")).toHaveLength(0);
    });

    it("does not send reconnect on open when streaming started fresh (no prior drop)", () => {
      const { ws } = mountSocket();
      beginStream("conv-1");
      ws.open();
      expect(ws.framesOfType("reconnect")).toHaveLength(0);
    });

    it("an unsolicited reconnect_status 'none' does NOT clear a live stream", () => {
      const { ws } = mountSocket();
      ws.open();
      const placeholderId = beginStream("conv-1");

      ws.receive("reconnect_status", { status: "none" });

      const s = useFalconStore.getState();
      expect(s.isStreaming).toBe(true);
      expect(s.streamingMessageId).toBe(placeholderId);
    });
  });

  describe("genuine mid-stream drop", () => {
    function dropAndReopen(ws) {
      ws.drop();
      vi.advanceTimersByTime(1000); // first retry backoff
      const ws2 = MockWebSocket.instances[1];
      expect(ws2).toBeDefined();
      ws2.open();
      return ws2;
    }

    it("onclose mid-stream makes the next onopen send reconnect with the conversation id", () => {
      const { ws } = mountSocket();
      ws.open();
      beginStream("conv-1");

      const ws2 = dropAndReopen(ws);
      expect(ws2.framesOfType("reconnect")).toEqual([
        { type: "reconnect", conversation_id: "conv-1" },
      ]);
    });

    it("solicited reconnect_status 'running' resets the in-flight message and re-arms streaming", () => {
      const { ws } = mountSocket();
      ws.open();
      const placeholderId = beginStream("conv-1");
      useFalconStore.getState().appendTextDelta(placeholderId, "stale prefix");

      const ws2 = dropAndReopen(ws);
      ws2.receive("reconnect_status", {
        status: "running",
        conversation_id: "conv-1",
      });

      const s = useFalconStore.getState();
      expect(s.isStreaming).toBe(true);
      expect(s.streamingMessageId).toBe(placeholderId);
      // The server replays from stream start; the prefix must be wiped so
      // the replay rebuilds it exactly once (appendTextDelta is not idempotent).
      const msg = s.messages.find((m) => m.id === placeholderId);
      expect(msg.content).toBe("");
      expect(msg.blocks).toEqual([]);
    });

    it("solicited reconnect_status 'none' clears streaming", () => {
      const { ws } = mountSocket();
      ws.open();
      beginStream("conv-1");

      const ws2 = dropAndReopen(ws);
      ws2.receive("reconnect_status", { status: "none" });

      expect(useFalconStore.getState().isStreaming).toBe(false);
      expect(useFalconStore.getState().streamingMessageId).toBeNull();
    });

    it("solicited reconnect_status 'error' clears the spinner", () => {
      const { ws } = mountSocket();
      ws.open();
      beginStream("conv-1");

      const ws2 = dropAndReopen(ws);
      ws2.receive("reconnect_status", { status: "error" });

      expect(useFalconStore.getState().isStreaming).toBe(false);
    });

    it("the reconnect gate is one-shot: a later stray status cannot wipe the re-armed stream", () => {
      const { ws } = mountSocket();
      ws.open();
      beginStream("conv-1");

      const ws2 = dropAndReopen(ws);
      ws2.receive("reconnect_status", {
        status: "running",
        conversation_id: "conv-1",
      });
      expect(useFalconStore.getState().isStreaming).toBe(true);

      ws2.receive("reconnect_status", { status: "none" }); // stray/late
      expect(useFalconStore.getState().isStreaming).toBe(true);
    });
  });

  describe("pending chat queue", () => {
    it("queues a chat sent while CONNECTING and flushes it exactly once on open", () => {
      const { ws, result } = mountSocket();
      result.current.sendChat("hello", "conv-1", { page: "traces" }, ["f1"]);
      expect(ws.sent).toHaveLength(0);

      ws.open();
      expect(ws.framesOfType("chat")).toEqual([
        {
          type: "chat",
          message: "hello",
          conversation_id: "conv-1",
          context: { page: "traces" },
          file_ids: ["f1"],
        },
      ]);

      // A later reconnect cycle must not re-flush the already-sent chat
      ws.drop();
      vi.advanceTimersByTime(1000);
      const ws2 = MockWebSocket.instances[1];
      ws2.open();
      expect(ws2.framesOfType("chat")).toHaveLength(0);
    });

    it("sends immediately when the socket is already OPEN", () => {
      const { ws, result } = mountSocket();
      ws.open();
      result.current.sendChat("hi", "conv-1", {});
      expect(ws.framesOfType("chat")).toHaveLength(1);
    });

    it("overrides context.page with the selected context and attaches the active skill", () => {
      const { ws, result } = mountSocket();
      ws.open();
      useFalconStore.getState().setSelectedContext("datasets");
      useFalconStore.getState().setActiveSkill({ id: "skill-9", name: "X" });

      result.current.sendChat("hi", "conv-1", {
        page: "dashboard",
        path: "/d",
      });

      const [frame] = ws.framesOfType("chat");
      expect(frame.context).toEqual({ page: "datasets", path: "/d" });
      expect(frame.skill_id).toBe("skill-9");
    });
  });

  describe("placeholder remap", () => {
    it("the first event carrying a backend message_id renames the placeholder and re-points streamingMessageId", () => {
      const { ws } = mountSocket();
      ws.open();
      beginStream("conv-1");

      ws.receive("text_delta", {
        message_id: "uuid-1",
        delta: "Hel",
        conversation_id: "conv-1",
      });

      const s = useFalconStore.getState();
      expect(s.messages).toHaveLength(1);
      expect(s.messages[0].id).toBe("uuid-1");
      expect(s.messages[0].content).toBe("Hel");
      expect(s.streamingMessageId).toBe("uuid-1");

      // Subsequent events with the same id keep appending — no second remap
      ws.receive("text_delta", {
        message_id: "uuid-1",
        delta: "lo",
        conversation_id: "conv-1",
      });
      expect(useFalconStore.getState().messages).toHaveLength(1);
      expect(useFalconStore.getState().messages[0].content).toBe("Hello");
    });

    it("resuming a running conversation: solicited replay rebuilds into the placeholder", () => {
      const { ws, result } = mountSocket();
      ws.open();
      // ChatListPanel.handleSelect: placeholder + setStreaming + sendReconnect
      beginStream("conv-1", "assistant-77");
      result.current.sendReconnect("conv-1");
      expect(ws.framesOfType("reconnect")).toEqual([
        { type: "reconnect", conversation_id: "conv-1" },
      ]);

      ws.receive("reconnect_status", {
        status: "running",
        conversation_id: "conv-1",
      });
      // Server replays buffered events from the stream start
      ws.receive("text_delta", {
        message_id: "uuid-5",
        delta: "Partial ",
        conversation_id: "conv-1",
      });
      ws.receive("text_delta", {
        message_id: "uuid-5",
        delta: "answer",
        conversation_id: "conv-1",
      });

      const s = useFalconStore.getState();
      expect(s.isStreaming).toBe(true);
      expect(s.streamingMessageId).toBe("uuid-5");
      expect(s.messages.find((m) => m.id === "uuid-5").content).toBe(
        "Partial answer",
      );
    });

    it("sendReconnect without a conversation id is a no-op", () => {
      const { ws, result } = mountSocket();
      ws.open();
      result.current.sendReconnect(null);
      expect(ws.sent).toHaveLength(0);
    });
  });

  describe("event handling", () => {
    it("text_delta appends to the open text block and starts a new block after a tool call", () => {
      const { ws } = mountSocket();
      ws.open();
      const id = liveStream(ws);

      ws.receive("text_delta", {
        message_id: id,
        delta: "Let me check.",
        conversation_id: "conv-1",
      });
      ws.receive("tool_call_start", {
        message_id: id,
        call_id: "tc_1",
        tool_name: "search_traces",
        params: {},
        step: 1,
        conversation_id: "conv-1",
      });
      ws.receive("text_delta", {
        message_id: id,
        delta: "Found it.",
        conversation_id: "conv-1",
      });

      const msg = useFalconStore.getState().messages[0];
      expect(msg.content).toBe("Let me check.Found it.");
      expect(msg.blocks.map((b) => b.type)).toEqual([
        "text",
        "tool_call",
        "text",
      ]);
    });

    it("tool_call_start adds a running tool call; tool_call_result completes it", () => {
      const { ws } = mountSocket();
      ws.open();
      const id = liveStream(ws);

      ws.receive("tool_call_start", {
        message_id: id,
        call_id: "tc_1",
        tool_name: "search_traces",
        tool_description: "Search traces",
        params: { q: "x" },
        step: 1,
        conversation_id: "conv-1",
      });
      let tc = useFalconStore.getState().messages[0].tool_calls[0];
      expect(tc.status).toBe("running");
      expect(tc.tool_name).toBe("search_traces");

      ws.receive("tool_call_result", {
        message_id: id,
        call_id: "tc_1",
        status: "completed",
        result_summary: "3 traces",
        result_full: "trace-1…",
        conversation_id: "conv-1",
      });
      tc = useFalconStore.getState().messages[0].tool_calls[0];
      expect(tc.status).toBe("completed");
      expect(tc.result_summary).toBe("3 traces");
      // The block view stays in sync with tool_calls
      const block = useFalconStore
        .getState()
        .messages[0].blocks.find((b) => b.id === "tc_1");
      expect(block.toolCall.status).toBe("completed");
    });

    it("iteration_start updates iteration progress on the message", () => {
      const { ws } = mountSocket();
      ws.open();
      liveStream(ws); // sends iteration_start {iteration: 1, max_iterations: 10}
      const msg = useFalconStore.getState().messages[0];
      expect(msg.currentIteration).toBe(1);
      expect(msg.maxIterations).toBe(10);
    });

    it("completion attaches the completion card", () => {
      const { ws } = mountSocket();
      ws.open();
      const id = liveStream(ws);
      ws.receive("completion", {
        message_id: id,
        completion_card: { title: "Done" },
        conversation_id: "conv-1",
      });
      expect(useFalconStore.getState().messages[0].completion_card).toEqual({
        title: "Done",
      });
    });

    it.each(["done", "stopped", "cancelled"])(
      "'%s' clears streaming state",
      (type) => {
        const { ws } = mountSocket();
        ws.open();
        const id = liveStream(ws);
        ws.receive(type, { message_id: id, conversation_id: "conv-1" });
        expect(useFalconStore.getState().isStreaming).toBe(false);
        expect(useFalconStore.getState().streamingMessageId).toBeNull();
      },
    );

    it("skill_activated sets the active skill", () => {
      const { ws } = mountSocket();
      ws.open();
      ws.receive("skill_activated", { skill: { id: "s1", name: "Debug" } });
      expect(useFalconStore.getState().activeSkill).toEqual({
        id: "s1",
        name: "Debug",
      });
    });

    it("navigate sets pendingNavigation", () => {
      const { ws } = mountSocket();
      ws.open();
      ws.receive("navigate", { path: "/dashboard/traces" });
      expect(useFalconStore.getState().pendingNavigation).toBe(
        "/dashboard/traces",
      );
    });

    it("navigate without a path is a no-op", () => {
      const { ws } = mountSocket();
      ws.open();
      ws.receive("navigate", {});
      expect(useFalconStore.getState().pendingNavigation).toBeNull();
    });
  });

  describe("widget_render routing (Phase 4C)", () => {
    const WIDGET = {
      id: "w-1",
      type: "bar_chart",
      title: "Token usage",
      config: { series: [{ name: "Tokens", data: [120, 340, 210] }] },
    };

    async function getImagineStore() {
      const { default: useImagineStore } = await import(
        "src/components/imagine/useImagineStore"
      );
      return useImagineStore;
    }

    it("a chat conversation renders the widget as a message block, not on the Imagine canvas", async () => {
      const useImagineStore = await getImagineStore();
      useImagineStore.setState({ widgets: [], conversationId: null });

      const { ws } = mountSocket();
      ws.open();
      const id = liveStream(ws); // conv-1, not an Imagine conversation

      ws.receive("widget_render", {
        message_id: id,
        action: "add",
        widget: WIDGET,
        conversation_id: "conv-1",
      });
      await vi.dynamicImportSettled();

      const msg = useFalconStore.getState().messages[0];
      const widgetBlocks = msg.blocks.filter((b) => b.type === "widget");
      expect(widgetBlocks).toHaveLength(1);
      expect(widgetBlocks[0].widget).toEqual(WIDGET);
      // The Imagine canvas store stays untouched
      expect(useImagineStore.getState().widgets).toHaveLength(0);
    });

    it("the Imagine conversation drives the canvas store and adds NO chat block", async () => {
      const useImagineStore = await getImagineStore();
      useImagineStore.setState({ widgets: [], conversationId: "conv-1" });

      const { ws } = mountSocket();
      ws.open();
      const id = liveStream(ws); // conv-1 IS the imagine conversation here

      ws.receive("widget_render", {
        message_id: id,
        action: "add",
        widget: WIDGET,
        conversation_id: "conv-1",
      });
      await vi.dynamicImportSettled();

      expect(useImagineStore.getState().widgets).toEqual([WIDGET]);
      const msg = useFalconStore.getState().messages[0];
      expect(msg.blocks.filter((b) => b.type === "widget")).toHaveLength(0);

      useImagineStore.setState({ widgets: [], conversationId: null });
    });

    it("replace_all in chat swaps the message's widget blocks", async () => {
      const useImagineStore = await getImagineStore();
      useImagineStore.setState({ widgets: [], conversationId: null });

      const { ws } = mountSocket();
      ws.open();
      const id = liveStream(ws);

      ws.receive("widget_render", {
        message_id: id,
        action: "add",
        widget: WIDGET,
        conversation_id: "conv-1",
      });
      ws.receive("widget_render", {
        message_id: id,
        action: "replace_all",
        widgets: [{ id: "w-2", type: "metric_card", config: { value: 7 } }],
        conversation_id: "conv-1",
      });
      await vi.dynamicImportSettled();

      const msg = useFalconStore.getState().messages[0];
      const widgetBlocks = msg.blocks.filter((b) => b.type === "widget");
      expect(widgetBlocks).toHaveLength(1);
      expect(widgetBlocks[0].widget.id).toBe("w-2");
    });
  });

  describe("error rendering", () => {
    it("error with message_id renders on that message and clears streaming", () => {
      const { ws } = mountSocket();
      ws.open();
      const id = liveStream(ws);

      ws.receive("error", {
        message_id: id,
        error: "Tool blew up",
        conversation_id: "conv-1",
      });

      const s = useFalconStore.getState();
      expect(s.isStreaming).toBe(false);
      expect(s.messages.find((m) => m.id === id).error).toBe("Tool blew up");
    });

    it("error WITHOUT message_id renders on the in-flight message (usage-limit path)", () => {
      const { ws } = mountSocket();
      ws.open();
      const placeholderId = beginStream("conv-1");

      // Direct consumer error: untagged, no message_id
      ws.receive("error", { error: "Usage limit exceeded" });

      const s = useFalconStore.getState();
      expect(s.isStreaming).toBe(false);
      expect(s.messages.find((m) => m.id === placeholderId).error).toBe(
        "Usage limit exceeded",
      );
    });

    it("error with no message_id and no in-flight message appends a standalone error message", () => {
      const { ws } = mountSocket();
      ws.open();
      useFalconStore.getState().setCurrentConversation("conv-1");

      ws.receive("error", { error: "Boom" });

      const s = useFalconStore.getState();
      expect(s.messages).toHaveLength(1);
      expect(s.messages[0].role).toBe("assistant");
      expect(s.messages[0].error).toBe("Boom");
      expect(s.messages[0].id).toMatch(/^assistant-error-/);
    });

    it("accepts legacy data.message as the error text", () => {
      const { ws } = mountSocket();
      ws.open();
      const id = liveStream(ws);

      ws.receive("error", {
        message_id: id,
        message: "LLM stream exploded",
        conversation_id: "conv-1",
      });

      expect(
        useFalconStore.getState().messages.find((m) => m.id === id).error,
      ).toBe("LLM stream exploded");
    });

    it("falls back to a generic message when the payload carries no text", () => {
      const { ws } = mountSocket();
      ws.open();
      const id = liveStream(ws);
      ws.receive("error", { message_id: id, conversation_id: "conv-1" });
      expect(
        useFalconStore.getState().messages.find((m) => m.id === id).error,
      ).toBe("An error occurred");
    });
  });

  describe("title_generated", () => {
    it("updates the conversation list even for a non-current conversation, without retitling the tab", () => {
      const { ws } = mountSocket();
      ws.open();
      const store = useFalconStore.getState();
      store.setConversations([
        { id: "conv-1", title: "New chat" },
        { id: "conv-2", title: "Other" },
      ]);
      store.setCurrentConversation("conv-2");
      document.title = "Untouched";

      ws.receive("title_generated", {
        conversation_id: "conv-1",
        title: "Trace digging",
      });

      const convs = useFalconStore.getState().conversations;
      expect(convs.find((c) => c.id === "conv-1").title).toBe("Trace digging");
      expect(convs.find((c) => c.id === "conv-2").title).toBe("Other");
      expect(document.title).toBe("Untouched");
    });

    it("retitles the document for the on-screen conversation", () => {
      const { ws } = mountSocket();
      ws.open();
      const store = useFalconStore.getState();
      store.setConversations([{ id: "conv-1", title: "New chat" }]);
      store.setCurrentConversation("conv-1");

      ws.receive("title_generated", {
        conversation_id: "conv-1",
        title: "Trace digging",
      });

      expect(document.title).toBe("Trace digging | Falcon AI");
    });
  });

  describe("cross-conversation filter", () => {
    it("ignores events tagged with a different conversation", () => {
      const { ws } = mountSocket();
      ws.open();
      const placeholderId = beginStream("conv-A");

      ws.receive("text_delta", {
        message_id: "uuid-9",
        delta: "leak",
        conversation_id: "conv-B",
      });

      const s = useFalconStore.getState();
      expect(s.messages).toHaveLength(1);
      expect(s.messages[0].id).toBe(placeholderId); // no remap happened
      expect(s.messages[0].content).toBe("");
      expect(s.streamingMessageId).toBe(placeholderId);
    });

    it("ignores tagged events even when no conversation is selected", () => {
      const { ws } = mountSocket();
      ws.open();
      expect(useFalconStore.getState().currentConversationId).toBeNull();

      ws.receive("text_delta", {
        message_id: "uuid-9",
        delta: "leak",
        conversation_id: "conv-B",
      });
      ws.receive("error", { error: "foreign", conversation_id: "conv-B" });

      expect(useFalconStore.getState().messages).toHaveLength(0);
    });
  });

  describe("stop", () => {
    it("sendStop sends a stop frame and optimistically clears streaming", () => {
      const { ws, result } = mountSocket();
      ws.open();
      beginStream("conv-1");

      result.current.sendStop();

      expect(ws.framesOfType("stop")).toHaveLength(1);
      expect(useFalconStore.getState().isStreaming).toBe(false);
    });
  });

  describe("destructive confirmation flow (Phase 3A)", () => {
    const CONFIRMATION = {
      token: "tok-1",
      tool_name: "delete_dataset",
      args: { dataset_id: "ds-1" },
      preview: "Will delete dataset ds-1",
      expires_at: "2026-06-10T12:15:00Z",
      policy: "destructive",
      undo_note: null,
    };

    /** Stream a destructive phase-1 call up to its confirmation_required result. */
    function streamConfirmationRequired(ws, id) {
      ws.receive("tool_call_start", {
        message_id: id,
        call_id: "tc_1",
        tool_name: "delete_dataset",
        params: { dataset_id: "ds-1" },
        step: 1,
        execution_policy: "destructive",
        conversation_id: "conv-1",
      });
      ws.receive("tool_call_result", {
        message_id: id,
        call_id: "tc_1",
        status: "confirmation_required",
        result_summary: "CONFIRMATION REQUIRED — no action was taken.",
        result_full: null,
        confirmation: CONFIRMATION,
        conversation_id: "conv-1",
      });
      ws.receive("done", { message_id: id, conversation_id: "conv-1" });
    }

    it("tool_call_start carries execution_policy onto the tool call (write badge)", () => {
      const { ws } = mountSocket();
      ws.open();
      const id = liveStream(ws);
      streamConfirmationRequired(ws, id);
      const tc = useFalconStore.getState().messages[0].tool_calls[0];
      expect(tc.execution_policy).toBe("destructive");
    });

    it("a confirmation_required result stores the structured confirmation payload", () => {
      const { ws } = mountSocket();
      ws.open();
      const id = liveStream(ws);
      streamConfirmationRequired(ws, id);

      const tc = useFalconStore.getState().messages[0].tool_calls[0];
      expect(tc.status).toBe("confirmation_required");
      expect(tc.confirmation).toEqual(CONFIRMATION);
      // Block view stays in sync — the card renders from blocks
      const block = useFalconStore
        .getState()
        .messages[0].blocks.find((b) => b.id === "tc_1");
      expect(block.toolCall.confirmation).toEqual(CONFIRMATION);
      // The phase-1 turn ended normally
      expect(useFalconStore.getState().isStreaming).toBe(false);
    });

    it("sendConfirmAction sends the confirm_action frame (button is the only approver)", () => {
      const { ws, result } = mountSocket();
      ws.open();
      result.current.sendConfirmAction("tok-1", "confirm");
      expect(ws.framesOfType("confirm_action")).toEqual([
        { type: "confirm_action", token: "tok-1", decision: "confirm" },
      ]);
    });

    it("sendConfirmAction without a token is a no-op", () => {
      const { ws, result } = mountSocket();
      ws.open();
      result.current.sendConfirmAction(null, "confirm");
      expect(ws.sent).toHaveLength(0);
    });

    it("confirmation_resolved 'confirmed' flips the card and arms a placeholder for the follow-up turn", () => {
      const { ws } = mountSocket();
      ws.open();
      const id = liveStream(ws);
      streamConfirmationRequired(ws, id);

      ws.receive("confirmation_resolved", {
        token: "tok-1",
        decision: "confirmed",
        message_id: id,
        call_id: "tc_1",
        conversation_id: "conv-1",
      });

      const s = useFalconStore.getState();
      // Card state flipped on the ORIGINAL message…
      expect(s.messages[0].id).toBe(id); // no remap onto the old message
      expect(s.messages[0].tool_calls[0].confirmation_status).toBe("confirmed");
      // …and a fresh placeholder is armed so the new agent turn can stream
      expect(s.messages).toHaveLength(2);
      expect(s.messages[1].id).toMatch(/^assistant-/);
      expect(s.isStreaming).toBe(true);
      expect(s.streamingMessageId).toBe(s.messages[1].id);

      // The follow-up turn (phase-2 re-call) remaps into that placeholder
      ws.receive("text_delta", {
        message_id: "uuid-2",
        delta: "Deleting now.",
        conversation_id: "conv-1",
      });
      expect(useFalconStore.getState().messages[1].id).toBe("uuid-2");
      expect(useFalconStore.getState().messages[1].content).toBe(
        "Deleting now.",
      );
    });

    it("confirmation_resolved 'cancelled' flips the card and still starts a turn (agent proposes alternatives)", () => {
      const { ws } = mountSocket();
      ws.open();
      const id = liveStream(ws);
      streamConfirmationRequired(ws, id);

      ws.receive("confirmation_resolved", {
        token: "tok-1",
        decision: "cancelled",
        message_id: id,
        call_id: "tc_1",
        conversation_id: "conv-1",
      });

      const s = useFalconStore.getState();
      expect(s.messages[0].tool_calls[0].confirmation_status).toBe("cancelled");
      expect(s.isStreaming).toBe(true);
      expect(s.messages).toHaveLength(2);
    });

    it("confirmation_resolved 'expired' flips the card but does NOT arm streaming (no turn follows)", () => {
      const { ws } = mountSocket();
      ws.open();
      const id = liveStream(ws);
      streamConfirmationRequired(ws, id);

      ws.receive("confirmation_resolved", {
        token: "tok-1",
        decision: "expired",
        message_id: id,
        call_id: "tc_1",
        conversation_id: "conv-1",
      });

      const s = useFalconStore.getState();
      expect(s.messages[0].tool_calls[0].confirmation_status).toBe("expired");
      expect(s.isStreaming).toBe(false);
      expect(s.messages).toHaveLength(1);
    });

    it("ignores a confirmation_resolved tagged with a different conversation", () => {
      const { ws } = mountSocket();
      ws.open();
      const id = liveStream(ws);
      streamConfirmationRequired(ws, id);

      ws.receive("confirmation_resolved", {
        token: "tok-9",
        decision: "confirmed",
        message_id: id,
        call_id: "tc_1",
        conversation_id: "conv-OTHER",
      });

      const s = useFalconStore.getState();
      expect(s.messages[0].tool_calls[0].confirmation_status).toBeUndefined();
      expect(s.isStreaming).toBe(false);
      expect(s.messages).toHaveLength(1);
    });

    it("an executed phase-2 result passes the undo payload through to the tool call", () => {
      const { ws } = mountSocket();
      ws.open();
      const id = liveStream(ws);

      ws.receive("tool_call_start", {
        message_id: id,
        call_id: "tc_2",
        tool_name: "delete_dataset",
        params: { dataset_id: "ds-1" },
        step: 1,
        execution_policy: "destructive",
        conversation_id: "conv-1",
      });
      ws.receive("tool_call_result", {
        message_id: id,
        call_id: "tc_2",
        status: "completed",
        result_summary: "Dataset deleted",
        result_full: null,
        undo: { note: "Restorable from snapshot", prompt: "Undo the delete" },
        conversation_id: "conv-1",
      });

      const tc = useFalconStore.getState().messages[0].tool_calls[0];
      expect(tc.status).toBe("completed");
      expect(tc.undo).toEqual({
        note: "Restorable from snapshot",
        prompt: "Undo the delete",
      });
    });
  });
});
