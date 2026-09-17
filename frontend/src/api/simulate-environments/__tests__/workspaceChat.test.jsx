import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { REPLY_MS, mockWorkspaceReply, useWorkspaceChat } from "../workspaceChat";

const ENV = { id: "env-1", name: "Support triage" };

const advance = (ms) => act(() => vi.advanceTimersByTime(ms));

describe("useWorkspaceChat", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("seeds a single greeting turn from the env", () => {
    const { result } = renderHook(() => useWorkspaceChat(ENV));

    expect(result.current.turns).toHaveLength(1);
    const greeting = result.current.turns[0];
    expect(greeting.role).toBe("builder");
    expect(greeting.steps[0].kind).toBe("note");
    expect(greeting.steps[0].text).toContain(ENV.name);
    expect(result.current.running).toBe(false);
  });

  it("appends the user turn and starts running on send", () => {
    const { result } = renderHook(() => useWorkspaceChat(ENV));

    act(() => result.current.send("hi"));

    expect(result.current.turns).toHaveLength(2);
    const userTurn = result.current.turns[1];
    expect(userTurn.role).toBe("user");
    expect(userTurn.text).toBe("hi");
    expect(result.current.running).toBe(true);
  });

  it("appends the mock reply and clears running after the delay", () => {
    const { result } = renderHook(() => useWorkspaceChat(ENV));

    act(() => result.current.send("drop that scenario"));
    advance(REPLY_MS);

    expect(result.current.turns).toHaveLength(3);
    const reply = result.current.turns[2];
    expect(reply.role).toBe("builder");
    expect(reply.steps[0].kind).toBe("note");
    expect(reply.steps[0].text).toBe(mockWorkspaceReply("drop that scenario"));
    expect(result.current.running).toBe(false);
  });

  it("ignores blank sends", () => {
    const { result } = renderHook(() => useWorkspaceChat(ENV));

    act(() => result.current.send("   "));

    expect(result.current.turns).toHaveLength(1);
    expect(result.current.running).toBe(false);
  });

  it("clears the pending timer on unmount", () => {
    const { result, unmount } = renderHook(() => useWorkspaceChat(ENV));

    act(() => result.current.send("hi"));
    expect(vi.getTimerCount()).toBe(1);

    unmount();

    expect(vi.getTimerCount()).toBe(0);
    advance(REPLY_MS);
  });
});
