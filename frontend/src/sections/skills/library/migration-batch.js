// Pure helpers for the retained-migration batch review.
export function importBlockerSummary(item) {
  const reasons = (item.blockers || []).join('; ');
  if (/file_not_text|binary_or_nonportable_text/.test(reasons)) return 'it contains a binary file or text that cannot be imported';
  if (/file_too_large/.test(reasons)) return 'a file exceeds the size limit';
  if (/path_invalid/.test(reasons)) return 'a file path is not supported';
  return 'it contains unsupported content; open Details to see why';
}

const comparable = (item) => Array.isArray(item.sourceHubChanges) || (Array.isArray(item.sourceChanges) && Array.isArray(item.hubChanges));

/** Classify each item. Only items whose safe outcome is unambiguous get a
 * preselected decision; every other item is an exception the owner decides. */
export function classifyMigrationItem(item) {
  if (item.blocked) return { kind: 'excluded', note: (item.blockers || []).join('; ') || 'Unsupported content' };
  if (item.hasDraft) return { kind: 'held', note: 'Your personal draft blocks replacement. Preserve it, then preview again.' };
  if (!comparable(item)) return { kind: 'held', note: 'The exact comparison is unavailable. Preview again before deciding.' };
  if (item.diverged) return { kind: 'choice', note: 'Source and Hub both changed. Choose keep or replace; nothing is merged.' };
  return { kind: 'default', note: item.destinationId ? 'Replace with reviewed source' : 'Import as a new skill' };
}

const identityOf = (item) => [item.sourceOrigin, item.sourceProject, item.sourceResource].join('|');
// A newer preview of the same source identity replaces the older plan; distinct
// sources (an exchange plus a private folder) are reviewed together.
export function retainPlan(plans, next) {
  const identities = new Set(next.items.map(identityOf));
  return [...plans.filter((plan) => plan.id !== next.id && !plan.items.some((item) => identities.has(identityOf(item)))), next];
}
