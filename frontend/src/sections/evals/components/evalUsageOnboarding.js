export const shouldPollEvalOnboardingReviewRun = ({
  autoOpenedRunId,
  isOnboarding,
  runId,
  step,
} = {}) =>
  Boolean(
    isOnboarding && step === "review" && runId && autoOpenedRunId !== runId,
  );
