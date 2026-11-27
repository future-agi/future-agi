import { describe, expect, it } from "vitest";

import { costLabel } from "../asks.constants";

describe("costLabel", () => {
  it("gives the price when there is one", () => {
    expect(costLabel({ credits_estimate: 4, refusal: null })).toBe(
      "About 4 credits",
    );
  });

  it("says nothing is charged when the ask is not billed", () => {
    expect(costLabel({ credits_estimate: null, refusal: null })).toBe(
      "No credits charged",
    );
  });

  it("says the cost is unknown when billing could not be read", () => {
    expect(
      costLabel({
        credits_estimate: null,
        refusal: {
          code: "credit_check_unavailable",
          message: "Credits can't be checked right now.",
        },
      }),
    ).toBe("Cost unknown right now");
  });
});
