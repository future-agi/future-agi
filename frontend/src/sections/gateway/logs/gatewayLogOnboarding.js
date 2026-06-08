export const GATEWAY_LOG_ONBOARDING_MODES = {
  REVIEW_REQUEST: "review-request",
  FIX_FAILURE: "fix-failure",
};

export const isGatewayLogOnboardingMode = (mode) =>
  mode === GATEWAY_LOG_ONBOARDING_MODES.REVIEW_REQUEST ||
  mode === GATEWAY_LOG_ONBOARDING_MODES.FIX_FAILURE;

export const getGatewayLogOnboardingCopy = (mode) => {
  if (mode === GATEWAY_LOG_ONBOARDING_MODES.FIX_FAILURE) {
    return {
      currentStep: "Fix",
      title: "Fix the failed gateway request",
      description:
        "Open the failed request, confirm the provider or model issue, then configure fallback behavior if this should recover automatically.",
      primaryLabel: "Open request detail",
      secondaryLabel: "Configure fallback",
    };
  }

  return {
    currentStep: "Review",
    title: "Review the first gateway request",
    description:
      "Inspect status, latency, cost, tokens, provider routing, cache, guardrails, and fallback behavior for the first routed request.",
    primaryLabel: "Open request detail",
    secondaryLabel: "Show all logs",
  };
};
