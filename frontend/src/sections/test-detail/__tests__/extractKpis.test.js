import { describe, expect, it } from "vitest";
import { AGENT_TYPES } from "src/sections/agents/constants";
import { extractKpis } from "../common";

describe("extractKpis", () => {
  it("matches normalized choice counts to configured labels", () => {
    const { deterministicEvals } = extractKpis(
      {
        customer_agent_single: {
          Bad: 4,
          choices: ["Good", "Neutral", "Bad"],
        },
      },
      AGENT_TYPES.VOICE,
    );

    expect(deterministicEvals).toEqual([
      {
        id: "customer_agent_single",
        title: "Customer Agent Single",
        data: [{ name: "Bad", value: 100 }],
      },
    ]);
  });
});
