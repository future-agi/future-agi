// What the console says for a check whose score is fixed. Pure data and
// readers, shared by the Guardrails page and the org config editor.
//
// A check marked `fixedScore` scores 1.0 on a match and 0 otherwise. The
// gateway triggers a check whose score is above the threshold
// (guardrail.go shouldTrigger), so any threshold below 1 runs it the same
// way and a threshold of 1 or more never triggers it. Its forms show no
// threshold slider; a stored threshold is kept as stored, and one that stops
// the check is said so.

/**
 * What a stored threshold does to a fixed-score check, or null when it runs
 * the check as any threshold below 1 does.
 */
export function fixedScoreNote(meta, threshold) {
  if (!meta?.fixedScore) return null;
  const value = Number(threshold);
  if (!Number.isFinite(value) || value < 1) return null;
  const shown = value.toFixed(2);
  return `Stored match threshold ${shown}: this check scores 1.0 on a match, so at ${shown} it never triggers.`;
}
