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
