import { describe, expect, it } from "vitest";

import { SimulateV3TestExecutionCallsResponse } from "src/generated/api-contracts/api.zod";

const callMetrics =
  SimulateV3TestExecutionCallsResponse.shape.results.element.pick({
    avg_stop_time_after_interruption: true,
    ai_interruption_count: true,
  });
const groupMetrics =
  SimulateV3TestExecutionCallsResponse.shape.groups.element.shape.aggregates.pick(
    {
      avg_stop_time_after_interruption: true,
      ai_interruptions: true,
    },
  );

describe("generated simulation interruption contracts", () => {
  it.each([
    [1281, 2],
    [0, 0],
    [null, null],
  ])("accepts call latency %s and count %s", (latency, count) => {
    const metrics = {
      avg_stop_time_after_interruption: latency,
      ai_interruption_count: count,
    };
    expect(callMetrics.parse(metrics)).toEqual(metrics);
  });

  it.each([
    [640.5, 1.5],
    [0, 0],
    [null, null],
  ])("accepts group averages %s and %s", (latency, interruptions) => {
    const metrics = {
      avg_stop_time_after_interruption: latency,
      ai_interruptions: interruptions,
    };
    expect(groupMetrics.parse(metrics)).toEqual(metrics);
  });

  it("requires the nullable metric keys in both responses", () => {
    expect(callMetrics.safeParse({}).success).toBe(false);
    expect(groupMetrics.safeParse({}).success).toBe(false);
  });
});
