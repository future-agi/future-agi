import { describe, expect, it } from "vitest";

import {
  SimulateV3CallExecutionDetailResponse,
  SimulateV3TestExecutionCallsResponse,
} from "src/generated/api-contracts/api.zod";

describe.each([
  ["call detail", SimulateV3CallExecutionDetailResponse.shape.sub_goal_results],
  [
    "call list",
    SimulateV3TestExecutionCallsResponse.shape.results.element.shape
      .sub_goal_results,
  ],
])("generated %s sub-goal contract", (_name, schema) => {
  it.each([true, false, null])("preserves verdict %s", (passed) => {
    const results = [{ name: "Verify the caller's PIN", passed }];

    expect(schema.parse(results)).toEqual(results);
  });

  it.each(["false", 0, undefined])("rejects invalid verdict %s", (passed) => {
    expect(
      schema.safeParse([{ name: "Verify the caller's PIN", passed }]).success,
    ).toBe(false);
  });
});
