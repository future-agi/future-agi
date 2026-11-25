export const MIN_TRIALS = 1;
export const MAX_TRIALS = 20;
export const MAX_RUNS = 200;

export const TRIALS_COPY = {
  range: `${MIN_TRIALS} to ${MAX_TRIALS} repeats`,
  notWhole: `Use a whole number from ${MIN_TRIALS} to ${MAX_TRIALS}`,
  tooMany: `Up to ${MAX_TRIALS} repeats`,
  tooManyRuns: (scenarios, trials) =>
    `Up to ${MAX_RUNS} runs at once: ${scenarios} × ${trials} is ${scenarios * trials}`,
};

export const runsProblem = (scenarios, trials) =>
  scenarios > 0 && scenarios * trials > MAX_RUNS
    ? TRIALS_COPY.tooManyRuns(scenarios, trials)
    : null;

export const trialsProblem = (text, scenarioCount = 0) => {
  const typed = String(text ?? "").trim();
  const value = Number(typed);
  if (!typed || !Number.isInteger(value) || value < MIN_TRIALS)
    return TRIALS_COPY.notWhole;
  if (value > MAX_TRIALS) return TRIALS_COPY.tooMany;
  return runsProblem(scenarioCount, value);
};
