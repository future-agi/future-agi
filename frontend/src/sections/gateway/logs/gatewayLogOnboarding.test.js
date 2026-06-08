import { describe, expect, it } from "vitest";
import {
  GATEWAY_LOG_ONBOARDING_MODES,
  getGatewayLogOnboardingCopy,
  isGatewayLogOnboardingMode,
} from "./gatewayLogOnboarding";

describe("gatewayLogOnboarding", () => {
  it("recognizes gateway log onboarding modes", () => {
    expect(
      isGatewayLogOnboardingMode(GATEWAY_LOG_ONBOARDING_MODES.REVIEW_REQUEST),
    ).toBe(true);
    expect(
      isGatewayLogOnboardingMode(GATEWAY_LOG_ONBOARDING_MODES.FIX_FAILURE),
    ).toBe(true);
    expect(isGatewayLogOnboardingMode("test-request")).toBe(false);
  });

  it("returns review copy for request review mode", () => {
    expect(
      getGatewayLogOnboardingCopy(GATEWAY_LOG_ONBOARDING_MODES.REVIEW_REQUEST),
    ).toMatchObject({
      currentStep: "Review",
      title: "Review the first gateway request",
      secondaryLabel: "Show all logs",
    });
  });

  it("returns recovery copy for failure-fix mode", () => {
    expect(
      getGatewayLogOnboardingCopy(GATEWAY_LOG_ONBOARDING_MODES.FIX_FAILURE),
    ).toMatchObject({
      currentStep: "Fix",
      title: "Fix the failed gateway request",
      secondaryLabel: "Configure fallback",
    });
  });
});
