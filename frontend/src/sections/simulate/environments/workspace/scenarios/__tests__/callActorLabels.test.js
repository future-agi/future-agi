import { describe, expect, it } from "vitest";

import {
  callActorTimeout,
  callActorWhen,
  effectiveTrigger,
} from "../callActorLabels";

describe("callActorWhen", () => {
  it.each([
    [
      { kind: "always" },
      "Present from the start: after the agent's first turn",
    ],
    [
      { kind: "probability", p: 0.4 },
      "In about 40% of calls, present from the start: after the agent's first turn",
    ],
    [{ kind: "mid_journey", turn: 3 }, "After the agent's turn 3"],
    [
      { kind: "on_tool", tool: "place_order", on_call: 1 },
      "When the agent calls place_order",
    ],
    [
      { kind: "on_tool", tool: "place_order", on_call: 2 },
      "On call 2 of place_order",
    ],
    [
      { kind: "on_keyword", words: ["total", "price"] },
      "When the agent says total, price",
    ],
    [undefined, "Present from the start: after the agent's first turn"],
  ])("says when a speaking actor comes in for %j", (trigger, said) => {
    expect(callActorWhen(trigger, "audio_mix")).toBe(said);
  });

  it("never says an actor comes in on a turn the harness does not use", () => {
    expect(
      callActorWhen({ kind: "on_tool", tool: "x" }, "audio_mix"),
    ).not.toMatch(/turn 4/);
  });

  it.each([
    [{ kind: "always" }, "Present from the start"],
    [{ kind: "contacted" }, "When the agent contacts them"],
    [{ kind: "after_seconds", seconds: 30 }, "30 s after the start"],
    [
      {
        kind: "conditional",
        collection: "approvals",
        where: { status: "pending" },
      },
      'Once a approvals record with status "pending" exists',
    ],
  ])("says when an actor with no channel comes in for %j", (trigger, said) => {
    expect(callActorWhen(trigger, undefined)).toBe(said);
  });

  it.each([
    [
      { kind: "always" },
      "Present from the start: after the agent's second turn",
    ],
    [{ kind: "mid_journey", turn: 1 }, "After the agent's turn 2"],
    [{ kind: "mid_journey", turn: 3 }, "After the agent's turn 3"],
  ])(
    "says someone taking the phone waits for the person for %j",
    (trigger, said) => {
      expect(callActorWhen(trigger, "audio_handoff")).toBe(said);
    },
  );
});

describe("effectiveTrigger", () => {
  it("is the scenario's own trigger, else the actor's activation", () => {
    const actor = { activation: { trigger: { kind: "contacted" } } };
    expect(
      effectiveTrigger({ trigger: { kind: "on_tool", tool: "x" } }, actor),
    ).toEqual({
      kind: "on_tool",
      tool: "x",
    });
    expect(effectiveTrigger({}, actor)).toEqual({ kind: "contacted" });
    expect(effectiveTrigger({}, {})).toEqual({ kind: "always" });
  });
});

describe("callActorTimeout", () => {
  it.each([
    [
      { timeout_seconds: 30, fallback: "silent" },
      "Waits 30 s, then nothing is sent",
    ],
    [
      { timeout_seconds: 30, fallback: "decline" },
      "Waits 30 s, then it declines",
    ],
    [
      { timeout_seconds: 30, fallback: "lead" },
      "Waits 30 s, then Lena answers instead",
    ],
    [
      { timeout_seconds: 30, fallback: "crm.reject" },
      "Waits 30 s, then crm.reject is taken for it",
    ],
    [{ timeout_seconds: 0 }, ""],
  ])("says what happens when %j runs out", (activation, said) => {
    expect(callActorTimeout({ activation }, { lead: "Lena" })).toBe(said);
  });
});
