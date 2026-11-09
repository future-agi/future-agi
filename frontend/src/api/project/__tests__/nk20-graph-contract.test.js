import { describe, expect, it } from "vitest";
import { parseTraceGraphResponse } from "../observe-contracts";

describe("NK-20 quality graph response", () => {
  it("keeps null quality scores and explicit lifecycle counts", () => {
    const point = {
      timestamp: "2026-10-01T00:00:00Z",
      value: null,
      count: 5,
      primary_traffic: 5,
      scored_count: 0,
      unscored_count: 5,
      completed_count: 0,
      error_count: 2,
      pending_count: 1,
      running_count: 1,
      skipped_count: 1,
      failed_count: 0,
      error_rate: 100,
    };
    const payload = {
      status: true,
      result: { metric_name: "quality", data: [point] },
    };
    expect(parseTraceGraphResponse(payload).data[0]).toEqual(point);
  });
  it("keeps genuine zero scores distinct from null", () => {
    const payload = {
      status: true,
      result: {
        metric_name: "quality",
        data: [
          { timestamp: "2026-10-01T00:00:00Z", value: 0, scored_count: 1 },
        ],
      },
    };
    expect(parseTraceGraphResponse(payload).data[0]).toEqual(
      payload.result.data[0],
    );
  });
});
