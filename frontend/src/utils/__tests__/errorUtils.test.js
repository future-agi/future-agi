import { describe, expect, it } from "vitest";

import { getSafeActionErrorMessage } from "../errorUtils";

describe("getSafeActionErrorMessage", () => {
  const fallback = "Task could not be saved. Please retry.";

  it("keeps concise client validation feedback", () => {
    expect(
      getSafeActionErrorMessage(
        {
          response: {
            status: 400,
            data: { message: "allow_sampled: Unknown field." },
          },
        },
        fallback,
      ),
    ).toBe("allow_sampled: Unknown field.");
  });

  it.each([
    "Code: 159. DB::Exception: Timeout exceeded",
    "Timeout exceeded\nStack trace: SELECT secret FROM spans",
    "Traceback (most recent call last): internal module",
  ])("hides internal query details: %s", (message) => {
    expect(
      getSafeActionErrorMessage(
        { response: { status: 500, data: { result: message } } },
        fallback,
      ),
    ).toBe(fallback);
  });

  it("does not trust an internal-looking message even on a 400 response", () => {
    expect(
      getSafeActionErrorMessage(
        {
          response: {
            status: 400,
            data: { detail: "Code: 159 DB::Exception: Timeout exceeded" },
          },
        },
        fallback,
      ),
    ).toBe(fallback);
  });
});
