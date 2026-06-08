import { describe, expect, it, vi } from "vitest";
import { trackPostHogEvent } from "src/utils/PostHog";
import {
  buildSignupEntryProperties,
  SignupEntryEvents,
  trackSignupEntryEvent,
} from "./signup-entry-analytics";

vi.mock("src/utils/PostHog", () => ({
  trackPostHogEvent: vi.fn(),
}));

describe("signup-entry analytics", () => {
  it("builds safe signup properties without identity payloads", () => {
    expect(
      buildSignupEntryProperties({
        authFlow: "email_password",
        hasPassword: "SecurePass123!",
        method: "email",
        onboardingToken: "token",
        returnTo: "/dashboard/home",
        status: "success",
      }),
    ).toEqual({
      method: "email",
      auth_flow: "email_password",
      status: "success",
      has_password: true,
      onboarding_token_present: true,
      return_to_present: true,
    });
  });

  it("keeps actionable non-PII error messages", () => {
    expect(
      buildSignupEntryProperties({
        error: {
          result: {
            code: "business_email_required",
            message:
              "Use a business email to create a workspace, or ask your administrator to invite this address.",
          },
        },
        status: "failed",
      }),
    ).toMatchObject({
      error_code: "business_email_required",
      error_message:
        "Use a business email to create a workspace, or ask your administrator to invite this address.",
      status: "failed",
    });
  });

  it("drops unsafe error text", () => {
    expect(
      buildSignupEntryProperties({
        error: {
          message: "Password for nikhil@example.com is invalid",
        },
        status: "failed",
      }),
    ).toEqual({
      method: "email",
      status: "failed",
      has_password: false,
      onboarding_token_present: false,
      return_to_present: false,
    });
  });

  it("tracks signup entry events through PostHog", () => {
    const tracked = trackSignupEntryEvent(SignupEntryEvents.signupSubmitted, {
      authFlow: "email_password",
      hasPassword: true,
    });

    expect(tracked).toBe(true);
    expect(trackPostHogEvent).toHaveBeenCalledWith("signup_email_submitted", {
      method: "email",
      auth_flow: "email_password",
      has_password: true,
      onboarding_token_present: false,
      return_to_present: false,
    });
  });
});
