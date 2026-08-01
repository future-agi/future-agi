import { describe, expect, it } from "vitest";
import { buildFilterParams } from "./useRequestLogs";

describe("buildFilterParams", () => {
  it("maps request-log filter drawer values to API query params", () => {
    expect(
      buildFilterParams({
        statusCodeMin: "400",
        statusCodeMax: "499",
        minLatency: "100",
        maxLatency: "900",
        isError: "true",
        guardrailTriggered: "true",
      }),
    ).toEqual({
      min_status_code: "400",
      max_status_code: "499",
      min_latency: "100",
      max_latency: "900",
      is_error: "true",
      guardrail_triggered: "true",
    });
  });
});
