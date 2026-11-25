export const ATTEMPT_COPY = {
  label: "Attempt",
  latest: (number) => `Attempt ${number} (latest)`,
  earlier: (number, at) => `Attempt ${number}, re-run at ${at}`,
  // Shown on a tab the server keeps only for the latest attempt.
  latestOnly: "Shows the latest attempt only.",
};
