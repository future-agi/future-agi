import { describe, expect, it } from "vitest";

import { classifyTaskError } from "./classifyTaskError";

describe("classifyTaskError", () => {
  it("classifies the billing guard's tuple-shaped denial as rate limited", () => {
    const result = classifyTaskError(
      "Error during evaluation: ('API call not allowed : ', 'rate_limited')",
    );

    expect(result).toMatchObject({
      category: "rate_limit",
      title: "Evaluation rate limit reached",
      normalized: "Evaluation rate limit reached",
    });
  });

  it("classifies a provider 429 as rate limited", () => {
    expect(
      classifyTaskError("Error during evaluation: upstream returned 429"),
    ).toMatchObject({
      category: "rate_limit",
    });
  });

  it("keeps a non-rate-limit API denial in the authorization category", () => {
    expect(
      classifyTaskError(
        "Error during evaluation: API call not allowed : invalid credentials",
      ),
    ).toMatchObject({
      category: "api_not_allowed",
    });
  });
});
