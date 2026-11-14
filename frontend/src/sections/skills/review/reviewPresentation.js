import { ROW_HEIGHT } from 'src/theme/sizes';

// Display-only language: review authority and release preconditions stay on the server.
export const releaseTitle = (transition) => transition.expectedVersion == null
  ? `First release to ${transition.label} (v${transition.version})`
  : `Move ${transition.label} from v${transition.expectedVersion} to v${transition.version}`;

export const counted = (count, singular, plural = `${singular}s`) => `${count} ${count === 1 ? singular : plural}`;

// Keep the shared evidence renderer and its controls; stack its cells at phone width.
export const responsiveEvidenceSx = {
  '& table[aria-label="Test evidence"]': { display: { xs: 'block', sm: 'table' } },
  '& table[aria-label="Test evidence"] thead': { display: { xs: 'none', sm: 'table-header-group' } },
  '& table[aria-label="Test evidence"] tbody': { display: { xs: 'block', sm: 'table-row-group' } },
  '& table[aria-label="Test evidence"] tbody tr': { display: { xs: 'grid', sm: 'table-row' }, gridTemplateColumns: 'minmax(0, 1fr)', height: { xs: 'auto', sm: ROW_HEIGHT.default }, py: { xs: 1.5, sm: 0 }, borderBottom: { xs: 1, sm: 0 }, borderColor: 'divider' },
  '& table[aria-label="Test evidence"] td': { minWidth: { xs: 0 }, width: { xs: 'auto' }, maxWidth: 1, overflowWrap: 'anywhere', borderBottom: { xs: 0, sm: 1 }, borderColor: { xs: 'divider', sm: 'divider' }, py: { xs: 0.5, sm: 1 } },
};
