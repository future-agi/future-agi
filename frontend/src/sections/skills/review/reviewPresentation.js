import { ROW_HEIGHT } from 'src/theme/sizes';

// Display-only language: review authority and release preconditions stay on the server.
// Moving a label back to an older version is a rollback and is titled as one ("Roll back production to v1").
export const releaseTitle = (transition) => transition.expectedVersion == null
  ? `First release to ${transition.label} (v${transition.version})`
  : Number(transition.version) < Number(transition.expectedVersion)
    ? `Roll back ${transition.label} to v${transition.version}`
    : `Move ${transition.label} from v${transition.expectedVersion} to v${transition.version}`;

// Every sentence about the release act is built from the request's own label, never a hard-coded
// "production": a staging request reads "Move staging to v1" and "Staging stays unchanged…".
/** A pending request's status, everywhere it is shown: "review" is only the reviewer's decision (UXF-14). */
export const WAITING_FOR_APPROVAL = 'Waiting for approval';
/** "production" → "Production". */
export const labelTitle = (label) => {
  const name = String(label ?? '').trim();
  return name ? `${name.charAt(0).toUpperCase()}${name.slice(1)}` : name;
};
/** The one verb for the release act, on the footer button and the confirm dialog: "Move staging to v1". */
export const moveLabelTo = (label, version) => `Move ${label} to v${version}`;
/** Where delivery is confirmed after a move: a context's Connected apps tab, a skill's Activity tab. */
export const deliveryTab = (subjectKind) => (subjectKind === 'agent_context' ? 'Connected apps' : 'Activity');
/** "Staging stays unchanged until approval and release." */
export const unchangedUntilRelease = (label) => `${labelTitle(label)} stays unchanged until approval and release.`;
/** The reviewer picker's helper: "An independent reviewer must approve before staging can move." */
export const reviewerBeforeMove = (label) => `An independent reviewer must approve before ${label} can move.`;

export const counted = (count, singular, plural = `${singular}s`) => `${count} ${count === 1 ? singular : plural}`;

// Keep the shared evidence renderer and its controls; stack its cells at phone width.
export const responsiveEvidenceSx = {
  '& table[aria-label="Test evidence"]': { display: { xs: 'block', sm: 'table' } },
  '& table[aria-label="Test evidence"] thead': { display: { xs: 'none', sm: 'table-header-group' } },
  '& table[aria-label="Test evidence"] tbody': { display: { xs: 'block', sm: 'table-row-group' } },
  '& table[aria-label="Test evidence"] tbody tr': { display: { xs: 'grid', sm: 'table-row' }, gridTemplateColumns: 'minmax(0, 1fr)', height: { xs: 'auto', sm: ROW_HEIGHT.default }, py: { xs: 1.5, sm: 0 }, borderBottom: { xs: 1, sm: 0 }, borderColor: 'divider' },
  '& table[aria-label="Test evidence"] td': { minWidth: { xs: 0 }, width: { xs: 'auto' }, maxWidth: 1, overflowWrap: 'anywhere', borderBottom: { xs: 0, sm: 1 }, borderColor: { xs: 'divider', sm: 'divider' }, py: { xs: 0.5, sm: 1 } },
};
