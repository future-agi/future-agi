// Display only: full identities and timestamps remain unchanged in write bodies.
export function relativeReviewTime(value, now = Date.now()) {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return '';
  const seconds = (timestamp - now) / 1000;
  const [unit, scale] = Math.abs(seconds) >= 86400 ? ['day', 86400] : Math.abs(seconds) >= 3600 ? ['hour', 3600] : ['minute', 60];
  return new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' }).format(Math.round(seconds / scale), unit);
}

export const reviewPersonName = (name, deleted) => name || (deleted ? 'Former member' : 'Name not available');
export const evidenceTitle = (ref, candidate, baseline) => ref.name || `${ref.source === 'skills_text' ? 'Text comparison' : 'Application verification'}${candidate != null ? ` · v${candidate}` : ''}${baseline != null ? ` vs v${baseline}` : ''}`;
export const outcomeLabel = (value) => ({ works_on_examples: 'Passed on examples', not_observed: 'Not observed', cannot_assess: 'Cannot assess' }[value] || value?.replaceAll('_', ' '));
// StatusBadge props for a recorded outcome. "works_on_examples" is a pass the
// server established on the saved examples, so it reads "Passed on examples".
export const outcomeBadge = (value) => (value === 'works_on_examples' ? { status: 'passed', label: 'Passed on examples' } : { status: value });

const reported = (value) => value ?? 'Not reported';
const counted = (value, one, many) => (value == null ? `Not reported ${many}` : `${value} ${value === 1 ? one : many}`);
/**
 * The one coverage sentence for test evidence, always in runs:
 * "3 of 4 runs graded · 1 regression · 1 human disagreement".
 * Missing values say "Not reported"; nothing is inferred. Accepts either
 * `regressed` or `regressions`, and adds incomplete runs only when reported.
 */
export function coverageText(counts) {
  if (!counts) return 'Not reported';
  const regressions = counts.regressed ?? counts.regressions;
  return [
    `${reported(counts.graded)} of ${reported(counts.planned)} runs graded`,
    ...(counts.incomplete != null && counts.incomplete !== 0 ? [`${counts.incomplete} incomplete`] : []),
    counted(regressions, 'regression', 'regressions'),
    `${counted(counts.disagreements, 'human disagreement', 'human disagreements')}`,
  ].join(' · ');
}

// One reading of a test-evidence result, shared by every screen that shows it
// (request form, share consent, prepared, pending, approved and renewed reviews).
// Native outcomes and the text-comparison decision.state values from
// model_hub/services/skills_hub/v11/grading.py map to one badge each.
const EVIDENCE_OUTCOMES = {
  works_on_examples: { status: 'passed', label: 'Passed on examples' },
  needs_changes: { status: 'failed', label: 'Needs changes' },
  inconclusive: { status: 'incomplete', label: 'Inconclusive' },
  diagnostic: { status: 'diagnostic', role: 'neutral', label: 'Diagnostic only' },
  routing_only: { status: 'routing_only', role: 'neutral', label: 'Routing only' },
  not_observed: { status: 'not_observed', label: 'Not observed' },
  cannot_assess: { status: 'cannot_assess', role: 'neutral', label: 'Cannot assess' },
};
// Per-check verdicts and run states that keep their own status-map entry.
const PASS_THROUGH = new Set(['pass', 'passed', 'fail', 'failed', 'not_applicable', 'not_graded', 'not_started', 'incomplete', 'error',
  'timed_out', 'interrupted', 'cancelled', 'canceled', 'stopped', 'queued', 'pending', 'running', 'cancelling', 'unknown', 'unverifiable',
  'regressed', 'improved', 'unchanged', 'mixed', 'flaky']);
/**
 * StatusBadge props ({ status, label, role? }) for one evidence result, or null
 * when no result is recorded (the caller says "Not recorded"). Text evidence
 * may carry its decision only in `summary.answer`; native evidence only in
 * `outcome`. Unknown values read "Unknown result", never a pass.
 */
export function evidenceOutcome({ source, outcome, summary } = {}) {
  if (outcome && typeof outcome === 'object') return outcome.status ? outcome : null;
  const value = outcome || (source === 'skills_text' ? summary?.answer : summary?.outcome) || null;
  if (!value) return null;
  const key = String(value);
  if (EVIDENCE_OUTCOMES[key]) return { ...EVIDENCE_OUTCOMES[key] };
  if (PASS_THROUGH.has(key)) return { status: key };
  return { status: 'unknown', label: 'Unknown result' };
}

/**
 * Coverage in cases and runs when the server reports a case count:
 * "1 case tested · 2 runs graded · 0 regressions". Case counts are never
 * derived from attempts; without one this is `coverageText(counts)`.
 */
export function evidenceCoverage(counts, cases = counts?.cases) {
  if (cases == null || !Number.isFinite(Number(cases))) return coverageText(counts);
  const regressions = counts?.regressed ?? counts?.regressions;
  const graded = counts?.graded == null
    ? 'Runs graded not reported'
    : counts.planned != null && counts.planned !== counts.graded
      ? `${counts.graded} of ${counts.planned} runs graded`
      : `${counts.graded} ${counts.graded === 1 ? 'run' : 'runs'} graded`;
  return [
    `${cases} ${Number(cases) === 1 ? 'case' : 'cases'} tested`,
    graded,
    ...(counts?.incomplete ? [`${counts.incomplete} incomplete`] : []),
    counted(regressions, 'regression', 'regressions'),
    ...(counts?.disagreements ? [counted(counts.disagreements, 'human disagreement', 'human disagreements')] : []),
  ].join(' · ');
}
