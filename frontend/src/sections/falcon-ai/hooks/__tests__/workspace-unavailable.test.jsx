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
  useWorkspace: () => ({ currentWorkspaceId: "stale" }),
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
const refusal = {
  type: "error",
  data: {
    code: "workspace_unavailable",
    error:
      "The selected workspace is unavailable. Select an accessible workspace and retry.",
    conversation_id: "conversation",
    client_message_id: "assistant-local",
  },
};

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("WebSocket", OfflineSocket);
  sockets.length = 0;
  useFalconStore.setState({
    currentConversationId: "conversation",
    messages: [{ id: "assistant-local", role: "assistant", content: "" }],
    isStreaming: true,
    streamingMessageId: "assistant-local",
  });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("pre-turn workspace refusal", () => {
  it.each([
    ["current turn", {}],
    ["previous conversation", { conversation_id: "previous" }],
    ["previous turn", { client_message_id: "assistant-old" }],
  ])(
    "dispatches workspace recovery with the socket workspace id for %s",
    (_label, overrides) => {
      const recover = vi.fn();
      window.addEventListener("workspace-unavailable", recover);
      try {
        renderHook(() => useFalconSocket());
        act(() =>
          sockets[0].onmessage({
            data: JSON.stringify({
              ...refusal,
              data: { ...refusal.data, ...overrides },
            }),
          }),
        );
        expect(recover).toHaveBeenCalledTimes(1);
        expect(recover.mock.calls[0][0].detail).toEqual({
          workspaceId: "stale",
        });
      } finally {
        window.removeEventListener("workspace-unavailable", recover);
      }
    },
  );

  it.each([0, 2, 3, null])(
    "shows an error instead of dropping a send in socket state %s",
    (state) => {
      const { result } = renderHook(() => useFalconSocket());
      if (state === null) sockets[0].onclose({ code: 4003 });
      else sockets[0].readyState = state;
      act(() => result.current.sendChat("retry", "conversation", {}));
      expect(sockets[0].send).not.toHaveBeenCalled();
      expect(useFalconStore.getState().messages[0].error).toBe(
        "An error occurred",
      );
      expect(useFalconStore.getState().isStreaming).toBe(false);
    },
  );

  it("sends a client correlation id for the local assistant placeholder", () => {
    const { result } = renderHook(() => useFalconSocket());
    act(() => result.current.sendChat("hello", "conversation", {}));
    expect(JSON.parse(sockets[0].send.mock.calls[0][0])).toMatchObject({
      conversation_id: "conversation",
      client_message_id: "assistant-local",
    });
  });

  it("renders the typed pre-turn error on the correlated assistant placeholder", () => {
    renderHook(() => useFalconSocket());
    act(() => sockets[0].onmessage({ data: JSON.stringify(refusal) }));
    expect(useFalconStore.getState().messages[0].error).toBe(
      refusal.data.error,
    );
    expect(useFalconStore.getState().isStreaming).toBe(false);
  });

  it("does not retry workspace_unavailable close 4003", () => {
    renderHook(() => useFalconSocket());
    act(() => {
      sockets[0].onmessage({ data: JSON.stringify(refusal) });
      sockets[0].onclose({ code: 4003 });
      vi.advanceTimersByTime(60000);
    });
    expect(sockets).toHaveLength(1);
  });

  it("does not apply a late refusal to a different turn or conversation", () => {
    renderHook(() => useFalconSocket());
    for (const data of [
      { ...refusal.data, conversation_id: "different" },
      { ...refusal.data, client_message_id: "assistant-old" },
    ]) {
      act(() =>
        sockets[0].onmessage({ data: JSON.stringify({ type: "error", data }) }),
      );
    }
    expect(useFalconStore.getState().messages[0].error).toBeUndefined();
    expect(useFalconStore.getState().isStreaming).toBe(true);
  });

  it("cancels a reconnect scheduled before reset even without a workspace rerender", () => {
    renderHook(() => useFalconSocket());
    act(() => sockets[0].onclose({ code: 1006 }));
    expect(vi.getTimerCount()).toBe(1);
    act(() => useFalconStore.getState().resetAll());
    expect(vi.getTimerCount()).toBe(0);
    act(() => vi.advanceTimersByTime(1000));
    expect(sockets).toHaveLength(1);
  });

  it("retires heartbeat intervals synchronously on recovery", () => {
    renderHook(() => useFalconSocket());
    act(() => sockets[0].onopen());
    sockets[0].send.mockClear();
    act(() => useFalconStore.getState().resetAll());
    expect(vi.getTimerCount()).toBe(0);
    act(() => vi.advanceTimersByTime(30000));
    expect(sockets[0].send).not.toHaveBeenCalled();
  });

  it("still retries a transient transport close", () => {
    renderHook(() => useFalconSocket());
    act(() => {
      sockets[0].onclose({ code: 1006 });
      vi.advanceTimersByTime(1000);
    });
    expect(sockets).toHaveLength(2);
  });
});
