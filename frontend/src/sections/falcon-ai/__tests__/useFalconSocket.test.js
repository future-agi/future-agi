import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { renderHook } from "@testing-library/react";

let lastWsInstance = null;

class MockWebSocket {
  constructor(url) {
    this.url = url;
    this.readyState = 1; // OPEN
    this.onopen = null;
    this.onmessage = null;
    this.onerror = null;
    this.onclose = null;
    this.send = vi.fn();
    this.close = vi.fn();
    lastWsInstance = this;
  }
}

globalThis.WebSocket = MockWebSocket;

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { accessToken: "mock-token" } }),
}));

vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "mock-ws" }),
}));

vi.mock("src/config-global", () => ({
  HOST_API: "http://localhost:8000",
  CURRENT_ENVIRONMENT: "local",
}));

describe("useFalconSocket error routing", () => {
  let useFalconStore;
  let useFalconSocket;

  beforeEach(async () => {
    lastWsInstance = null;
    const storeMod = await import("../store/useFalconStore");
    useFalconStore = storeMod.default;
    useFalconStore.getState().resetAll();

    const socketMod = await import("../hooks/useFalconSocket");
    useFalconSocket = socketMod.useFalconSocket;
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("surfaces stream errors on active streamingMessageId when message_id is omitted by backend", () => {
    const convId = "conv-123";
    const placeholderId = "assistant-temp-456";

    useFalconStore.setState({
      currentConversationId: convId,
      messages: [{ id: placeholderId, role: "assistant", content: "" }],
      isStreaming: true,
      streamingMessageId: placeholderId,
    });

    renderHook(() => useFalconSocket());

    expect(lastWsInstance).not.toBeNull();

    // Backend sends an error frame with conversation_id and error, but without message_id
    lastWsInstance.onmessage({
      data: JSON.stringify({
        type: "error",
        data: {
          conversation_id: convId,
          error: "Usage limit exceeded",
        },
      }),
    });

    const state = useFalconStore.getState();
    expect(state.isStreaming).toBe(false);
    expect(state.messages[0].error).toBe("Usage limit exceeded");
  });

  it.each([undefined, "server-message"])(
    "shows the backend model message with message_id=%s",
    (messageId) => {
      useFalconStore.setState({
        currentConversationId: "current",
        messages: [{ id: "placeholder", role: "assistant", content: "" }],
        isStreaming: true,
        streamingMessageId: "placeholder",
      });
      renderHook(() => useFalconSocket());

      lastWsInstance.onmessage({
        data: JSON.stringify({
          type: "error",
          data: {
            conversation_id: "current",
            message_id: messageId,
            message: "The selected model is temporarily unavailable.",
          },
        }),
      });

      const state = useFalconStore.getState();
      expect(state.messages[0]).toMatchObject({
        id: messageId || "placeholder",
        error: "The selected model is temporarily unavailable.",
      });
      expect(state.isStreaming).toBe(false);
      expect(state.streamingMessageId).toBeNull();
    },
  );

  it.each([undefined, null])(
    "does not fall back to the active reply without a conversation (%s)",
    (conversationId) => {
      useFalconStore.setState({
        currentConversationId: "current",
        messages: [
          { id: "placeholder", role: "assistant", content: "Partial" },
        ],
        isStreaming: true,
        streamingMessageId: "placeholder",
      });
      renderHook(() => useFalconSocket());
      lastWsInstance.onmessage({
        data: JSON.stringify({
          type: "error",
          data: { conversation_id: conversationId, error: "Unscoped failure" },
        }),
      });
      const state = useFalconStore.getState();
      expect(state.isStreaming).toBe(true);
      expect(state.streamingMessageId).toBe("placeholder");
      expect(state.messages[0]).toEqual({
        id: "placeholder",
        role: "assistant",
        content: "Partial",
      });
    },
  );

  it("does not claim a late error when no conversation is selected", () => {
    useFalconStore.setState({
      currentConversationId: null,
      messages: [{ id: "placeholder", role: "assistant", content: "" }],
      isStreaming: true,
      streamingMessageId: "placeholder",
    });
    renderHook(() => useFalconSocket());
    lastWsInstance.onmessage({
      data: JSON.stringify({
        type: "error",
        data: { conversation_id: "previous", error: "Late failure" },
      }),
    });
    expect(useFalconStore.getState().isStreaming).toBe(true);
    expect(useFalconStore.getState().messages[0].error).toBeUndefined();
  });

  it("ignores the old conversation after a switch, then handles the current error", () => {
    useFalconStore.setState({ currentConversationId: "previous" });
    renderHook(() => useFalconSocket());
    useFalconStore.setState({
      currentConversationId: "current",
      messages: [{ id: "current-placeholder", role: "assistant", content: "" }],
      isStreaming: true,
      streamingMessageId: "current-placeholder",
    });
    for (const messageId of [undefined, "previous-message"]) {
      lastWsInstance.onmessage({
        data: JSON.stringify({
          type: "error",
          data: {
            conversation_id: "previous",
            message_id: messageId,
            error: "Late failure",
          },
        }),
      });
    }
    expect(useFalconStore.getState().isStreaming).toBe(true);
    expect(useFalconStore.getState().streamingMessageId).toBe(
      "current-placeholder",
    );
    expect(useFalconStore.getState().messages[0]).toEqual({
      id: "current-placeholder",
      role: "assistant",
      content: "",
    });

    lastWsInstance.onmessage({
      data: JSON.stringify({
        type: "error",
        data: { conversation_id: "current", message: "Current model failure" },
      }),
    });
    expect(useFalconStore.getState().messages[0].error).toBe(
      "Current model failure",
    );
    expect(useFalconStore.getState().isStreaming).toBe(false);
  });

  it("keeps the generic fallback when a scoped error has no text", () => {
    useFalconStore.setState({
      currentConversationId: "current",
      messages: [{ id: "placeholder", role: "assistant", content: "" }],
      isStreaming: true,
      streamingMessageId: "placeholder",
    });
    renderHook(() => useFalconSocket());
    lastWsInstance.onmessage({
      data: JSON.stringify({
        type: "error",
        data: { conversation_id: "current" },
      }),
    });
    expect(useFalconStore.getState().messages[0].error).toBe(
      "An error occurred",
    );
    expect(useFalconStore.getState().isStreaming).toBe(false);
  });

  it("surfaces stream errors when message_id is explicitly provided", () => {
    const convId = "conv-123";
    const explicitMsgId = "msg-789";

    useFalconStore.setState({
      currentConversationId: convId,
      messages: [{ id: explicitMsgId, role: "assistant", content: "" }],
      isStreaming: true,
      streamingMessageId: explicitMsgId,
    });

    renderHook(() => useFalconSocket());

    lastWsInstance.onmessage({
      data: JSON.stringify({
        type: "error",
        data: {
          conversation_id: convId,
          message_id: explicitMsgId,
          error: "Agent error occurred",
        },
      }),
    });

    const state = useFalconStore.getState();
    expect(state.isStreaming).toBe(false);
    expect(state.messages[0].error).toBe("Agent error occurred");
  });
});
