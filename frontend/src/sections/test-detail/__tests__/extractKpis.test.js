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

  // `completed_calls` reaches this classifier on the product's run-detail
  // payload as well as the simulate harness's. It has no label, icon or
  // filter mapping of its own, so it must land in none of the three buckets:
  // in `evalMetrics` it paints a phantom eval, and in `callDetails` it paints
  // an iconless card that looks clickable and filters nothing. The simulate
  // side reads the KPI directly and does not come through here.
  it.each([AGENT_TYPES.VOICE, AGENT_TYPES.CHAT])(
    "keeps completed_calls out of every bucket (%s)",
    (agentType) => {
      const { evalMetrics, callDetails, systemMetrics, deterministicEvals } = extractKpis(
        { completed_calls: 16, total_calls: 20, avg_turn_count: 4 },
        agentType,
      );

      expect(evalMetrics).not.toHaveProperty("completed_calls");
      expect(callDetails).not.toHaveProperty("completed_calls");
      expect(systemMetrics).not.toHaveProperty("completed_calls");
      expect(deterministicEvals.map((e) => e.id)).not.toContain("completed_calls");
      // The neighbouring keys still route as they did — this excludes one key,
      // it does not switch the classifier off.
      expect(callDetails).toHaveProperty("total_calls", 20);
      expect(systemMetrics).toHaveProperty("avg_turn_count", 4);
    },
  );
});
