// What Jev answered, as the answers endpoint reports it: a label, or a
// marker for a row it could not answer or had nothing to read for.

export const ERROR_ANSWER = "__error__";
export const SKIPPED_ANSWER = "__skipped__";
// Below this Jev is flagged as unsure of its answer.
export const LOW_CONFIDENCE = 0.6;

export const isFailure = (answer) =>
  answer === ERROR_ANSWER || answer === SKIPPED_ANSWER;

export const answerText = (answer) =>
  answer === ERROR_ANSWER
    ? "Failed"
    : answer === SKIPPED_ANSWER
      ? "Skipped"
      : answer || "";

export const quoted = (text) => `“${text}”`;

// Each label with its count and share of the answers, then failures and skips
// (kept apart from the labels so a category can never be confused with them).
// A label the question no longer lists (an edited evaluator) still counts,
// after the current labels.
function tallyAnswers(labels, answers) {
  const tally = new Map(labels.map((label) => [label, 0]));
  let errors = 0;
  let skipped = 0;
  let total = 0;
  answers.forEach(([answer, count]) => {
    total += count;
    if (answer === ERROR_ANSWER) errors += count;
    else if (answer === SKIPPED_ANSWER) skipped += count;
    else tally.set(answer, (tally.get(answer) || 0) + count);
  });
  const share = (count) => (total ? count / total : 0);
  return {
    total,
    labels: [...tally.entries()].map(([label, count]) => ({
      label,
      count,
      share: share(count),
    })),
    errors: { count: errors, share: share(errors) },
    skipped: { count: skipped, share: share(skipped) },
  };
}

// One question's split from per-label counts, as a past run reports them:
// each label with its count and share, failures and skips apart.
export const summarizeCounts = (counts, item) => ({
  ...tallyAnswers(
    [...(item?.labels || [])],
    Object.entries(counts || {}).filter(([, count]) => count > 0),
  ),
  pending: 0,
  unsure: 0,
});
