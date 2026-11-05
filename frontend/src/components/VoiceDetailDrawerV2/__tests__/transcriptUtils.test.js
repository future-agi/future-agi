import { describe, expect, it } from "vitest";
import { enrichTurns, otherParties } from "../transcriptUtils";

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
});

describe("otherParties", () => {
  it("keeps two sub-actors who share a name apart by their id, and an older turn by name", () => {
    const turns = enrichTurns([
      {
        speaker_role: "sub_actor",
        sub_actor_id: "kid",
        speaker_name: "Sam",
        content: "a",
      },
      {
        speaker_role: "sub_actor",
        sub_actor_id: "uncle",
        speaker_name: "Sam",
        content: "b",
      },
      {
        speaker_role: "sub_actor",
        sub_actor_id: "kid",
        speaker_name: "Sam",
        content: "c",
      },
      { speaker_role: "sub_actor", speaker_name: "Rowan", content: "d" },
      { speaker_role: "sub_actor", speaker_name: "Rowan", content: "e" },
    ]);
    expect(
      otherParties(turns).map((t) => [t.subActorId, t.speakerName]),
    ).toEqual([
      ["kid", "Sam"],
      ["uncle", "Sam"],
      ["", "Rowan"],
    ]);
  });
});
