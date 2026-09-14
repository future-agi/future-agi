import { describe, expect, it } from "vitest";
import { enrichTurns } from "../transcriptUtils";

describe("enrichTurns", () => {
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
});
