import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useEnvironmentsStore, resetEnvironmentsStore } from "src/sections/simulate/environments/store/useEnvironmentsStore";
import { NARRATION_STAGES } from "../_fixtures/builderNarration";
import { STAGE_GAP_MS, STEP_MS, useBuildProgress } from "../buildProgress";

const UNDERSTAND_STEPS = NARRATION_STAGES.understand("acme/support-bot@main").steps.length;

const advance = (ms) => act(() => vi.advanceTimersByTime(ms));

describe("useBuildProgress", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    resetEnvironmentsStore();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("does nothing while disabled", () => {
    const { result } = renderHook(() =>
      useBuildProgress({ envId: "env-1", agentRef: "acme/support-bot@main", enabled: false }),
    );
    advance(STEP_MS * 20);
    expect(result.current.turns).toEqual([]);
    expect(result.current.done).toEqual([]);
  });

  it("narrates the first stage step by step", () => {
    const { result } = renderHook(() =>
      useBuildProgress({ envId: "env-1", agentRef: "acme/support-bot@main", enabled: true }),
    );

    advance(STEP_MS);
    expect(result.current.turns).toHaveLength(1);
    expect(result.current.turns[0].role).toBe("builder");
    expect(result.current.turns[0].steps).toHaveLength(1);
    expect(result.current.running).toBe(true);

    advance(STEP_MS * (UNDERSTAND_STEPS - 1));
    expect(result.current.done).toContain("understand");
    expect(result.current.running).toBe(false);

    advance(STAGE_GAP_MS + STEP_MS);
    expect(result.current.turns).toHaveLength(2);
    expect(result.current.turns[1].title).toBe("Building the world its tools act on");
  });

  it("runs to completion and mirrors the milestones to the store", () => {
    const { result } = renderHook(() =>
      useBuildProgress({ envId: "env-1", agentRef: "acme/support-bot@main", enabled: true }),
    );

    advance(20000);

    expect(result.current.done).toEqual(["understand", "build", "scenarios"]);
    expect(result.current.chips).toEqual(["write 4 more edge cases"]);
    expect(result.current.running).toBe(false);
    expect(useEnvironmentsStore.getState().buildProgress.done).toEqual([
      "understand",
      "build",
      "scenarios",
    ]);
  });

  it("answers a matching ask with the seeded json step", () => {
    const { result } = renderHook(() =>
      useBuildProgress({ envId: "env-1", agentRef: "acme/support-bot@main", enabled: false }),
    );

    act(() => result.current.send("what did you seed?"));
    advance(STEP_MS);

    const [userTurn, builderTurn] = result.current.turns;
    expect(userTurn.role).toBe("user");
    expect(userTurn.text).toBe("what did you seed?");
    expect(builderTurn.role).toBe("builder");
    expect(builderTurn.title).toBeNull();
    expect(builderTurn.steps[0]).toMatchObject({ kind: "json", label: "seeded" });
  });

  it("falls back for an unrecognised ask", () => {
    const { result } = renderHook(() =>
      useBuildProgress({ envId: "env-1", agentRef: "acme/support-bot@main", enabled: false }),
    );

    act(() => result.current.send("tell me a joke"));
    advance(STEP_MS);

    const builderTurn = result.current.turns[1];
    expect(builderTurn.steps[0]).toMatchObject({ kind: "note" });
    expect(builderTurn.steps[0].text).toContain("In this prototype I answer on tools");
  });

  it("stops the run dead when enabled flips back to false mid-run", () => {
    const spy = vi.spyOn(useEnvironmentsStore.getState(), "setBuildProgress");
    const { result, rerender } = renderHook(
      ({ enabled }) =>
        useBuildProgress({ envId: "env-1", agentRef: "acme/support-bot@main", enabled }),
      { initialProps: { enabled: true } },
    );

    advance(STEP_MS * 3);
    expect(result.current.turns).toHaveLength(1);

    // A stale "building" remount that startPreflight() resets to preflight:
    // enabled goes true -> false without unmounting.
    rerender({ enabled: false });
    expect(vi.getTimerCount()).toBe(0);
    // The reducer resets too — no leaked running:true / empty turn into the store.
    expect(result.current.running).toBe(false);
    expect(result.current.turns).toEqual([]);
    expect(useEnvironmentsStore.getState().buildProgress.running).toBe(false);
    const callsAfterFlip = spy.mock.calls.length;
    advance(20000);
    expect(spy.mock.calls.length).toBe(callsAfterFlip);
    expect(result.current.done).not.toContain("build");
  });

  it("stops writing to the store after unmount", () => {
    const spy = vi.spyOn(useEnvironmentsStore.getState(), "setBuildProgress");
    const { result, unmount } = renderHook(() =>
      useBuildProgress({ envId: "env-1", agentRef: "acme/support-bot@main", enabled: true }),
    );

    advance(STEP_MS * 3);
    expect(result.current.turns).toHaveLength(1);

    unmount();
    // The cleanup must clear every scheduled timer, or the run keeps ticking.
    expect(vi.getTimerCount()).toBe(0);
    const callsAtUnmount = spy.mock.calls.length;
    advance(20000);
    expect(spy.mock.calls.length).toBe(callsAtUnmount);
  });
});
