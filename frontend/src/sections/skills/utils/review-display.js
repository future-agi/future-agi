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
