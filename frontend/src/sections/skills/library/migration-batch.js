// Pure helpers for the retained-migration batch review.
export function importBlockerSummary(item) {
  const reasons = (item.blockers || []).join('; ');
  const name = item.name || item.root || 'This skill';
  if (/file_not_text|binary_or_nonportable_text/.test(reasons)) {
    const reason = (item.blockers || []).find((entry) => /file_not_text|binary_or_nonportable_text/.test(entry));
    const path = reason?.includes(':') ? reason.slice(0, reason.lastIndexOf(':')).trim() : null;
    return `${path ? path.split('/').at(-1) : name} isn't a text file`;
  }
  if (/file_too_large/.test(reasons)) return `${name} exceeds the file size limit`;
  if (/path_invalid/.test(reasons)) return `${name} has an unsupported file path`;
  return `${name} contains unsupported content; open Details to see why`;
}

const comparable = (item) => Array.isArray(item.sourceHubChanges) || (Array.isArray(item.sourceChanges) && Array.isArray(item.hubChanges));

/** Classify each item. Only items whose safe outcome is unambiguous get a
 * preselected decision; every other item is an exception the owner decides. */
export function classifyMigrationItem(item) {
  if (item.blocked) return { kind: 'excluded', note: importBlockerSummary(item) };
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
