import { describe, expect, it } from "vitest";
import { extractKpis } from "../common";
import { getLabel } from "../PerformanceMetrics/common";

const first = "00000000-0000-0000-0000-000000000002";
const second = "3f2a9c1e-7b4d-4e8a-9c2f-5d1e8a7b6c40";

describe("run-detail KPI alias labels", () => {
  it("names an aliased evaluation with a short ID suffix", () => {
    expect(getLabel(`avg_tone__${first}`)).toBe("Avg Tone (...00002)");
    expect(getLabel(`avg_ai_quality__${second.toUpperCase()}`)).toBe(
      "Avg AI Quality (...b6c40)",
    );
    expect(getLabel(`tier___${second}`)).toBe("Tier (...b6c40)");
  });

  it("labels an evaluation alias by its own name, not a system metric's", () => {
    expect(getLabel("avg_score")).toBe("Avg CSAT Score");
    expect(getLabel(`avg_score__${first}`)).toBe("Avg Score (...00002)");
    expect(getLabel(`avg_agent_latency__${second}_`)).toBe(
      "Avg Agent Latency (...b6c40)",
    );
  });

  it("gives two aliases of one name different titles on the chart", () => {
    const { deterministicEvals } = extractKpis(
      {
        agent_type: "voice",
        tier_1: { Warm: 1, choices: ["Warm"] },
        [`tier_1__${first}_`]: { Calm: 1, choices: ["Calm"] },
        [`tier_1__${second}`]: { Fine: 1, choices: ["Fine"] },
      },
      "voice",
    );
    // DeterministicEvaluationChart passes the title through getLabel again.
    const shown = deterministicEvals.map(({ title }) => getLabel(title));
    expect(shown).toEqual(["Tier 1", "Tier 1 (...00002)", "Tier 1 (...b6c40)"]);
  });

  it("keeps every other label unchanged", () => {
    const labels = {
      total_calls: "Total Calls",
      connected_calls: "Connected",
      failed_calls: "Failed",
      avg_agent_latency: "Agent Latency",
      avg_bot_wpm: "Agent WPM",
      agent_talk_percentage: "Agent Talk Percentage",
      customer_talk_percentage: "Customer Talk Percentage",
      avg_stop_time_after_interruption: "Agent Stop Latency",
      avg_toxicity: "Average Toxicity",
      avg_score: "Avg CSAT Score",
      calls_connected_percentage: "Calls Connected(%)",
      avg_accuracy: "Avg Accuracy",
      avg_ai_interruption_rate: "Avg AI Interruption Rate",
      call_mood: "Call Mood",
      tier1: "Tier1",
      // Two spaces in an evaluation name become "__".
      tier__1: "Tier  1",
      call__mood__2: "Call  Mood  2",
      [`tier1_${first}`]: `Tier1 ${first}`,
      [`tier__${first.slice(0, 18)}`]: `Tier  ${first.slice(0, 18)}`,
      [`tier__${first}x`]: `Tier  ${first}x`,
      [`tier__${first}_a`]: `Tier  ${first} A`,
    };
    expect(
      Object.fromEntries(
        Object.keys(labels).map((key) => [key, getLabel(key)]),
      ),
    ).toEqual(labels);
    expect(getLabel(undefined)).toBe("");
  });
});
