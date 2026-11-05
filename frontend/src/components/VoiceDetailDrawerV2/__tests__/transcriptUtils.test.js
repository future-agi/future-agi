import { describe, expect, it } from "vitest";
import { computeCallMetrics, enrichTurns } from "../transcriptUtils";

describe("enrichTurns", () => {
  it("preserves structured function calls through normalization", () => {
    const toolCalls = [{ name: "lookup_order", arguments: { id: 12 } }];
    const [turn] = enrichTurns([
      { speaker_role: "tool", content: "Function call", tool_calls: toolCalls },
    ]);
    expect(turn.toolCalls).toEqual(toolCalls);
    expect(turn.role).toBe("tool");
  });

  it("does not infer voice timing or interruptions for chat turns", () => {
    const turns = enrichTurns(
      [
        {
          role: "user",
          content: "first message",
          startTimeSeconds: 1_757_400_000_000,
          duration: 12,
        },
        {
          role: "assistant",
          content: "second message",
          startTimeSeconds: 1_757_400_001_000,
          duration: 10,
        },
      ],
      { enableTemporalFeatures: false },
    );

    expect(turns).toHaveLength(2);
    expect(turns.map((turn) => turn.content)).toEqual([
      "first message",
      "second message",
    ]);
    expect(turns.every((turn) => turn.start == null)).toBe(true);
    expect(turns.every((turn) => turn.end == null)).toBe(true);
    expect(turns.every((turn) => turn.overlapsPrev === false)).toBe(true);
  });

  it("continues to detect genuine overlapping voice turns by default", () => {
    const turns = enrichTurns([
      { role: "user", content: "hello", start: 0, duration: 2 },
      { role: "assistant", content: "hi", start: 1, duration: 2 },
    ]);

    expect(turns[1].overlapsPrev).toBe(true);
  });

  it("flags a long wait after the agent's own filler line", () => {
    const turns = enrichTurns([
      { role: "user", content: "question", start: 0, duration: 2 },
      { role: "assistant", content: "Let me check.", start: 4, duration: 1 },
      { role: "assistant", content: "Thanks for waiting", start: 10, duration: 1 },
      { role: "assistant", content: "Just to recap", start: 21.6, duration: 1 },
    ]);

    expect(turns.map((t) => t.slowResponse)).toEqual([false, false, true, true]);
  });
});

describe("computeCallMetrics worstAgentLatency", () => {
  it("is the longest silence before an agent turn, ignoring longer caller gaps", () => {
    const turns = enrichTurns([
      { role: "user", content: "question", start: 0, duration: 2 },
      { role: "assistant", content: "Let me check.", start: 4, duration: 1 },
      { role: "assistant", content: "Thanks for waiting", start: 10, duration: 1 },
      { role: "assistant", content: "Just to recap", start: 21.6, duration: 1 },
      { role: "user", content: "ok", start: 40, duration: 1 },
    ]);

    expect(computeCallMetrics(turns).worstAgentLatency).toBeCloseTo(10.6, 5);
  });
});
