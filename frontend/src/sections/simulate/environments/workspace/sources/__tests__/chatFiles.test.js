import { describe, expect, it } from "vitest";
import { spliceTurns } from "../chatFiles";

const ids = (turns) => turns.map((turn) => turn.id);
const turn = (id) => ({ id });

describe("spliceTurns", () => {
  it("puts each upload after the turns there when it was sent, in order", () => {
    const chat = ["a", "b", "c", "d"].map(turn);
    const inserts = [
      { at: 1, turns: [turn("f1"), turn("f1r")] },
      { at: 3, turns: [turn("f2")] },
      { at: 9, turns: [turn("f3")] },
    ];
    expect(ids(spliceTurns(chat, inserts))).toEqual([
      "a",
      "f1",
      "f1r",
      "b",
      "c",
      "f2",
      "d",
      "f3",
    ]);
  });
});
