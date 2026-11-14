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

describe("getSafeActionErrorMessage for typed user-facing refusals", () => {
  const fallback = "Something went wrong";
  const retry =
    "Could not verify your plan's dataset limit. Please try again in a moment.";

  it("shows the dataset limit check retry message despite its 503", () => {
    expect(
      getSafeActionErrorMessage(
        {
          response: {
            status: 503,
            data: { code: "dataset_limit_check_failed", message: retry },
          },
        },
        fallback,
      ),
    ).toBe(retry);
  });

  it("reads the code from the interceptor-flattened error too", () => {
    expect(
      getSafeActionErrorMessage(
        { statusCode: 503, code: "dataset_limit_check_failed", result: retry },
        fallback,
      ),
    ).toBe(retry);
  });

  it("still hides a 503 without a user-facing code", () => {
    expect(
      getSafeActionErrorMessage(
        {
          statusCode: 503,
          code: "service_unavailable",
          result: "upstream harness error: connection refused",
        },
        fallback,
      ),
    ).toBe(fallback);
  });

  it("still hides internal details behind a user-facing code", () => {
    expect(
      getSafeActionErrorMessage(
        {
          statusCode: 503,
          code: "dataset_limit_check_failed",
          result: "Code: 159. DB::Exception: Timeout exceeded",
        },
        fallback,
      ),
    ).toBe(fallback);
  });
});

describe("getSafeActionErrorMessage for rate-limit refusals", () => {
  const fallback = "Failed to run evaluation. Please retry.";
  const limit =
    "Evaluation rate limit reached. Wait for the usage window to reset before retrying.";

  it("shows the server's 429 message from the interceptor-flattened error", () => {
    expect(
      getSafeActionErrorMessage(
        {
          status: false,
          type: "rate_limit",
          code: "rate_limited",
          message: limit,
          result: limit,
          statusCode: 429,
        },
        fallback,
      ),
    ).toBe(limit);
  });

  it("shows the server's 429 message from a raw Axios error", () => {
    expect(
      getSafeActionErrorMessage(
        { response: { status: 429, data: { detail: limit } } },
        fallback,
      ),
    ).toBe(limit);
  });

  it.each([
    ["no message", { statusCode: 429 }],
    ["a structured result", { statusCode: 429, result: { code: "x" } }],
    [
      "an internal-looking message",
      { statusCode: 429, result: "Code: 159. DB::Exception: Timeout exceeded" },
    ],
    ["a multiline message", { statusCode: 429, result: "Slow down\nTrace" }],
  ])("never invites an immediate retry for a 429 with %s", (_, error) => {
    const message = getSafeActionErrorMessage(error, fallback);
    expect(message).toBe(
      "Rate limit reached. Please try again in a few minutes.",
    );
    expect(message).not.toContain("DB::");
  });
});
