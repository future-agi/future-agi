// Chat > failed replies (AUD-CHAT-03) and reopened answers (AUD-CHAT-01).
// A failed model call showed only "An error occurred" (the server sends its
// text under `message`), other server failures showed raw exception text,
// and a message sent before the connection opened said nothing useful.
// Reopening a chat whose answer is still being written asks the server to
// replay it, and the replay must not repeat text that already arrived.
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import useFalconSocket from "../useFalconSocket";
import useFalconStore from "../../store/useFalconStore";

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { accessToken: "offline" } }),
}));
vi.mock("src/config-global", () => ({
  HOST_API: "https://offline.invalid",
  CURRENT_ENVIRONMENT: "local",
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "workspace" }),
}));
// The socket address comes from a one-time ticket request that the hook
// awaits before opening the socket. Answer it offline.
vi.mock("src/components/websocket/use-socket", () => ({
  requestWebSocketUrl: () => "wss://offline.invalid/ws/falcon-ai/?ticket=test",
}));

const sockets = [];
class OfflineSocket {
  static OPEN = 1;
  static CONNECTING = 0;
  readyState = 1;
  send = vi.fn();
  close = vi.fn();
  constructor() {
    sockets.push(this);
  }
}

const QUESTION = "Why did run 42 fail?";
const REPLY_FAILED = "Falcon couldn't answer this time.";

beforeEach(() => {
  vi.stubGlobal("WebSocket", OfflineSocket);
  sockets.length = 0;
  useFalconStore.getState().resetAll();
  // A question was just sent; its reply is streaming.
  useFalconStore.setState({
    currentConversationId: "conversation",
    messages: [
      { id: "question", role: "user", content: QUESTION, blocks: [] },
      { id: "reply", role: "assistant", content: "", blocks: [] },
    ],
    isStreaming: true,
    streamingMessageId: "reply",
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function openSocket() {
  const { result } = renderHook(() => useFalconSocket());
  // Lets the awaited ticket request finish, so the socket it opens exists.
  for (let i = 0; i < 3; i += 1) await Promise.resolve();
  expect(sockets).toHaveLength(1);
  return { socket: sockets[0], hook: result };
}

function receive(socket, type, data) {
  act(() => socket.onmessage({ data: JSON.stringify({ type, data }) }));
}

const reply = () =>
  useFalconStore.getState().messages.find((m) => m.id === "reply");

describe("a failed reply", () => {
  it("a failed model call says so in plain words", async () => {
    const { socket } = await openSocket();

    // agent.py sends the model failure under `message`, not `error`.
    receive(socket, "error", {
      message: "LLM error: Server disconnected without sending a response.",
      conversation_id: "conversation",
    });

    expect(reply().error).toBe(REPLY_FAILED);
    expect(useFalconStore.getState().isStreaming).toBe(false);
  });

  it("never shows a raw server exception", async () => {
    const { socket } = await openSocket();

    receive(socket, "error", {
      error: "Agent error: KeyError('trace_id')",
      conversation_id: "conversation",
    });

    expect(reply().error).toBe(REPLY_FAILED);
    expect(reply().error).not.toMatch(/KeyError/);
  });

  it("keeps the server's own words for limits", async () => {
    const { socket } = await openSocket();

    receive(socket, "error", {
      error: "Rate limit exceeded. Please wait before sending more messages.",
      conversation_id: "conversation",
    });

    expect(reply().error).toBe(
      "Rate limit exceeded. Please wait before sending more messages.",
    );
  });

  it("a message sent before the connection is open says it was not sent", async () => {
    const { socket, hook } = await openSocket();
    socket.readyState = OfflineSocket.CONNECTING;

    act(() => hook.current.sendChat(QUESTION, "conversation", {}));

    expect(socket.send).not.toHaveBeenCalled();
    expect(reply().error).toBe(
      "Falcon isn't connected right now. Wait a moment, then ask again.",
    );
    expect(useFalconStore.getState().isStreaming).toBe(false);
  });
});

describe("reopening an answer still being written", () => {
  it("asks the server to replay the reopened chat's answer", async () => {
    const { socket, hook } = await openSocket();

    act(() => hook.current.sendReconnect("conversation"));

    expect(socket.send).toHaveBeenCalledTimes(1);
    expect(JSON.parse(socket.send.mock.calls[0][0])).toEqual({
      type: "reconnect",
      conversation_id: "conversation",
    });
  });

  it("the replay does not repeat text that already arrived live", async () => {
    const { socket } = await openSocket();
    const delta = (text) =>
      receive(socket, "text_delta", {
        message_id: "reply",
        delta: text,
        conversation_id: "conversation",
      });

    // The start of the answer arrives live before the replay begins.
    delta("Run 42 ");
    expect(reply().content).toBe("Run 42 ");

    // The replay sends the answer again from its first event.
    receive(socket, "reconnect_status", { status: "running", event_count: 2 });
    delta("Run 42 ");
    delta("timed out.");

    expect(reply().content).toBe("Run 42 timed out.");
    expect(reply().blocks).toEqual([
      expect.objectContaining({ type: "text", content: "Run 42 timed out." }),
    ]);
    expect(useFalconStore.getState().isStreaming).toBe(true);
    expect(useFalconStore.getState().streamingMessageId).toBe("reply");
  });

  it("an answer that failed meanwhile gives the message box back", async () => {
    const { socket } = await openSocket();

    // A failed run is reported as "error" and sends no "done" frame.
    receive(socket, "reconnect_status", { status: "error", event_count: 1 });

    expect(useFalconStore.getState().isStreaming).toBe(false);
  });

  it("nothing to replay gives the message box back", async () => {
    const { socket } = await openSocket();

    receive(socket, "reconnect_status", { status: "none" });

    expect(useFalconStore.getState().isStreaming).toBe(false);
    expect(reply().content).toBe("");
  });
});
