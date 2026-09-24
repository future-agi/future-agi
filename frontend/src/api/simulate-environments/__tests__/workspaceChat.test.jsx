import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { REPLY_MS, mockGuidedQuestion, mockWorkspaceReply, useWorkspaceChat } from "../workspaceChat";
import { setBuilderMode } from "src/sections/simulate/environments/buildEnvironment/console/builderModeBus";
import {
  publishScenarioSelection,
  clearScenarioSelection,
  getScenarioSelection,
} from "src/sections/simulate/environments/buildEnvironment/console/scenarioSelectionBus";

const ENV = { id: "env-1", name: "Support triage" };

const advance = (ms) => act(() => vi.advanceTimersByTime(ms));

describe("useWorkspaceChat", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    // Both are module-level state — reset so a leak from another test can't flip
    // this suite's default-Auto / no-selection expectations.
    setBuilderMode("auto");
    clearScenarioSelection();
  });

  afterEach(() => {
    vi.useRealTimers();
    setBuilderMode("auto");
    clearScenarioSelection();
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

  it("asks a guided question instead of a plain note in Manual mode", () => {
    setBuilderMode("guided");
    const { result } = renderHook(() => useWorkspaceChat(ENV));

    act(() => result.current.send("tighten the refund rule"));
    advance(REPLY_MS);

    const reply = result.current.turns[2];
    expect(reply.role).toBe("builder");
    expect(reply.steps[0]).toMatchObject({ kind: "note" });
    const ask = reply.steps[1];
    expect(ask.kind).toBe("ask");
    expect(ask.question).toEqual(mockGuidedQuestion("tighten the refund rule"));
    expect(typeof ask.onSubmit).toBe("function");
    expect(typeof ask.onSkip).toBe("function");

    // Submitting the card (our AskUserQuestionCard hands back an array of
    // labels) appends a follow-up builder note naming the choice.
    act(() => ask.onSubmit(["Auto-approve up to $500"]));
    expect(result.current.turns).toHaveLength(4);
    expect(result.current.turns[3].steps[0].text).toContain("Auto-approve up to $500");
  });

  it("accepts an attachments arg without dropping the send", () => {
    const { result } = renderHook(() => useWorkspaceChat(ENV));

    act(() => result.current.send("here is data", [{ name: "d.csv" }]));

    expect(result.current.turns).toHaveLength(2);
    expect(result.current.turns[1].text).toBe("here is data");
  });

  it("routes the guided question by verb heuristic", () => {
    expect(mockGuidedQuestion("add an eval").multiSelect).toBe(true);
    expect(mockGuidedQuestion("tighten the refund rule").multiSelect).toBe(false);
    expect(mockGuidedQuestion("something else").prompt).toMatch(/applied/i);
  });

  it("treats a send with rows selected as a bulk edit: clears the selection, names the rows, skips guided", () => {
    // Even in Manual mode, a selection-scoped send skips the guided question.
    setBuilderMode("guided");
    publishScenarioSelection({ ids: ["s1", "s2"], rows: [{ name: "refund-dispute" }, { name: "rushed-caller" }] });
    const { result } = renderHook(() => useWorkspaceChat(ENV));

    act(() => result.current.send("make them more impatient"));
    // The chip's source clears immediately on send.
    expect(getScenarioSelection().ids).toHaveLength(0);

    advance(REPLY_MS);
    const reply = result.current.turns.at(-1);
    expect(reply.role).toBe("builder");
    // A plain bulk-edit note, not an "ask" card.
    expect(reply.steps[0].kind).toBe("note");
    expect(reply.steps.some((s) => s.kind === "ask")).toBe(false);
    expect(reply.steps[0].text).toContain("2 selected scenarios");
    expect(reply.steps[0].text).toContain("refund-dispute");
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
