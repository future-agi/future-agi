import { describe, expect, it } from "vitest";
import { opsWeightsAvailable, weightsToSend } from "../chooseWinnerOps";

describe("choose winner ops weights (L7c g6)", () => {
  it("offers latency/token weights only when every run records them", () => {
    expect(
      opsWeightsAvailable([{ target_type: "prompt" }, { target_type: "http" }]),
    ).toBe(true);
    expect(
      opsWeightsAvailable([
        { target_type: "prompt" },
        { target_type: "agent" },
      ]),
    ).toBe(false);
    expect(opsWeightsAvailable([{ target_type: "code" }])).toBe(false);
    expect(opsWeightsAvailable([])).toBe(true);
  });
  it("sends 0 for the ops weights when they are not offered", () => {
    const v = {
      response_time: 5,
      completion_tokens: 3,
      total_tokens: 2,
      Tone: 7,
    };
    expect(weightsToSend(v, true)).toEqual(v);
    expect(weightsToSend(v, false)).toEqual({
      response_time: 0,
      completion_tokens: 0,
      total_tokens: 0,
      Tone: 7,
    });
  });
});
