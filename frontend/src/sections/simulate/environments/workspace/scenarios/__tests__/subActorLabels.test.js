import { describe, expect, it } from "vitest";

import { subActorWhen } from "../subActorLabels";

describe("subActorWhen", () => {
  it.each([
    [{ kind: "always" }, "After the agent's first turn"],
    [
      { kind: "probability", p: 0.4 },
      "In about 40% of calls, after the agent's first turn",
    ],
    [{ kind: "mid_journey", turn: 3 }, "After the agent's turn 3"],
    [
      { kind: "on_tool", tool: "place_order", on_call: 1 },
      "When the agent calls place_order, or after the agent's turn 4",
    ],
    [
      { kind: "on_tool", tool: "place_order", on_call: 2 },
      "On call 2 of place_order, or after the agent's turn 4",
    ],
    [
      { kind: "on_keyword", words: ["total", "price"] },
      "When the agent says total, price, or after the agent's turn 4",
    ],
    [undefined, "After the agent's first turn"],
  ])("says when a sub-actor enters for %j", (trigger, said) => {
    expect(subActorWhen(trigger)).toBe(said);
  });

  it("says a phone menu answers the call", () => {
    expect(subActorWhen({ kind: "always" }, "ivr")).toBe("Answers the call");
  });
});
