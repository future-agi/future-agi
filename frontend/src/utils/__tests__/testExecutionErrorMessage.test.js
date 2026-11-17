import { describe, expect, it } from "vitest";
import * as errors from "../errorUtils";

const APPROVED = [
  "Failed to start test execution. Check the test runs before trying again.",
  "Test execution service is unavailable. Please try again later.",
  "Failed to execute test. Check the test runs before trying again.",
];
const FALLBACK = "Something went wrong";

it("pins the complete immutable execution allowlist to S2 S3 S4", () => {
  expect(errors.TEST_EXECUTION_ERROR_MESSAGES).toEqual(APPROVED);
  expect(Object.isFrozen(errors.TEST_EXECUTION_ERROR_MESSAGES)).toBe(true);
});

describe.each([500, 503, 599])("execution copy for HTTP %i", (status) => {
  it.each(APPROVED)("shows only the exact approved text: %s", (message) => {
    expect(
      errors.getTestExecutionErrorMessage({
        statusCode: status,
        result: message,
      }),
    ).toBe(message);
    expect(
      errors.getTestExecutionErrorMessage({
        response: { status, data: { message } },
      }),
    ).toBe(message);
  });

  it.each([
    "arbitrary id=PRIVATE_ID",
    "Traceback: unexpected failure",
    ` ${APPROVED[0]}`,
    `${APPROVED[0]} `,
    `${APPROVED[0]}\n`,
    `${APPROVED[0]} id=PRIVATE_ID`,
    [APPROVED[0]],
    { message: APPROVED[0] },
  ])(
    "rejects non-exact server text even with a normally user-facing code: %j",
    (message) => {
      expect(
        errors.getTestExecutionErrorMessage({
          statusCode: status,
          code: "permission_denied",
          result: message,
        }),
      ).toBe(FALLBACK);
    },
  );
});

it.each([400, 404, 409, 422])(
  "preserves existing safe validation for HTTP %i",
  (statusCode) => {
    const result = "At least one scenario is required to execute the test.";
    expect(errors.getTestExecutionErrorMessage({ statusCode, result })).toBe(
      result,
    );
  },
);

it("does not normalize approved copy or trust a conflicting message field", () => {
  expect(
    errors.getTestExecutionErrorMessage({
      statusCode: 500,
      message: "private id=PRIVATE_ID",
      result: APPROVED[0],
    }),
  ).toBe(FALLBACK);
});

it.each([undefined, 0, 200, 302, 401])(
  "does not authorize execution copy without a server failure (%s)",
  (statusCode) => {
    expect(
      errors.getTestExecutionErrorMessage({ statusCode, result: APPROVED[0] }),
    ).toBe(FALLBACK);
  },
);

it("leaves ordinary action handling closed to execution 5xx text", () => {
  for (const result of APPROVED) {
    expect(
      errors.getSafeActionErrorMessage({ statusCode: 500, result }, FALLBACK),
    ).toBe(FALLBACK);
  }
});
