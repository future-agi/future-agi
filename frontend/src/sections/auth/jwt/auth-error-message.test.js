import { describe, expect, it } from "vitest";

import { getAuthErrorMessage } from "./auth-error-message";

describe("getAuthErrorMessage", () => {
  it("extracts structured auth block errors as text", () => {
    expect(
      getAuthErrorMessage({
        status: false,
        result: {
          error:
            "IP address temporarily blocked due to multiple failed attempts",
          error_code: "LOGIN_IP_BLOCKED",
          blocked: true,
        },
      }),
    ).toBe("IP address temporarily blocked due to multiple failed attempts");
  });

  it("does not return nested objects to the UI", () => {
    expect(
      getAuthErrorMessage({
        result: {
          blocked: true,
        },
      }),
    ).toBe("Something went wrong. Please try again.");
  });

  it("maps invalid credentials by error code", () => {
    expect(
      getAuthErrorMessage({
        result: { error_code: "LOGIN_INVALID_CREDENTIALS" },
      }),
    ).toBe("Enter a valid Email and password combination");
  });

  it("maps recaptcha failures by error code", () => {
    expect(
      getAuthErrorMessage({
        result: { error_code: "LOGIN_RECAPTCHA_FAILED" },
      }),
    ).toBe("reCAPTCHA verification failed. Please try again.");
  });

  it("maps deactivated accounts and prefers the server message", () => {
    expect(
      getAuthErrorMessage({
        result: {
          error_code: "LOGIN_ACCOUNT_DEACTIVATED",
          message: "Contact admin@acme.com to reactivate.",
        },
      }),
    ).toBe("Contact admin@acme.com to reactivate.");
  });

  it("maps a missing-user detail string to a sign-up prompt", () => {
    expect(getAuthErrorMessage({ detail: "User not found" })).toBe(
      "No account found with this email. Please sign up to create one",
    );
  });

  it("maps legacy invalid credentials strings without a code", () => {
    expect(
      getAuthErrorMessage({ result: { error: "Invalid credentials" } }),
    ).toBe("Enter a valid Email and password combination");
  });

  it("falls back to the provided fallback when nothing matches", () => {
    expect(getAuthErrorMessage({}, "Registration failed")).toBe(
      "Registration failed",
    );
  });
});
