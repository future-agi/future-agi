export const EVAL_REVIEW_RUN_POLL_INTERVAL_MS = 2000;
export const EVAL_REVIEW_RUN_POLL_TIMEOUT_MS = 20000;

export const shouldPollEvalOnboardingReviewRun = ({
  autoOpenedRunId,
  isOnboarding,
  recoveryRunId,
  runId,
  step,
} = {}) =>
  Boolean(
    isOnboarding &&
      step === "review" &&
      runId &&
      autoOpenedRunId !== runId &&
      recoveryRunId !== runId,
  );
