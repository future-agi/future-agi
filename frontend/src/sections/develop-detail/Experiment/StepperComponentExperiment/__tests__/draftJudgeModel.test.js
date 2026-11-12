// W1-2 (LS-01): the rule-based draft judge from the expected column.
import { describe, expect, it } from "vitest";
import {
  draftFactsLine,
  draftFactsOf,
  draftJudgeOf,
  draftNameOf,
} from "../draftJudgeModel";

const values = [
  "Sorry for the wait. A-3920 is refunded: $42.10 reaches your card in 5-7 business days.",
  "Ticket T-54736 is open; we reply by Monday.",
  "Your order B-1001 ships on Oct 23.",
  "Write to help@acme.ai for a copy.",
  "",
  null,
];

describe("draftJudgeModel", () => {
  it("counts the rows that state each kind of fact, most first", () => {
    const facts = draftFactsOf(values);
    expect(facts.map((f) => [f.key, f.rows, f.total])).toEqual([
      ["ids", 3, 4],
      ["dates", 2, 4],
      ["amounts", 1, 4],
      ["emails", 1, 4],
      ["time_frames", 1, 4],
    ]);
    expect(draftFactsLine(facts.slice(0, 3))).toBe(
      "reference ids in 3 of 4 rows · dates in 2 · amounts in 1",
    );
  });

  it("turns the ticked facts into instructions, labelled a draft", () => {
    const facts = draftFactsOf(values);
    const d = draftJudgeOf({
      column: "expected_reply",
      picked: facts.filter((f) => ["ids", "amounts"].includes(f.key)),
    });
    expect(d.name).toBe("matches_expected_reply");
    expect(d.displayName).toBe("Matches expected_reply");
    expect(d.instructions).toBe(
      "Pass if {{output}} states the same reference ids and amounts as {{expected}}. Fail if it leaves one out or states a different one.",
    );
    expect(d.label).toBe(
      "Rule-based draft from expected_reply · edit before saving",
    );
    expect(draftJudgeOf({ column: "x", picked: [] }).instructions).toMatch(
      /gives the same answer as \{\{expected\}\}/,
    );
    expect(draftNameOf("Expected Answer!")).toBe("matches_expected_answer");
  });
});
