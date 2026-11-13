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
export const outcomeLabel = (value) => ({ works_on_examples: 'Works on examples', not_observed: 'Not observed', cannot_assess: 'Cannot assess' }[value] || value?.replaceAll('_', ' '));
