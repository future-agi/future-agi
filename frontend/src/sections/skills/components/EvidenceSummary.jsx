import PropTypes from 'prop-types';
import { Button, Checkbox, Chip, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Typography } from '@mui/material';
import StatusBadge from 'src/components/status-badge';
import { runStatusOf } from 'src/utils/runStatus';
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from 'src/theme/sizes';
import { evidenceCoverage, evidenceOutcome } from '../utils/review-display';
import { stackedTableSx } from './responsiveTable';
import When from './When';

// Missing results are "Not recorded", never a pass. A result the page could not
// load (a renewed or stale review) is "Not loaded", which is not "Not recorded".
const NOT_RECORDED = { status: 'not_recorded', role: 'neutral', label: 'Not recorded' };
const NOT_LOADED = { status: 'not_loaded', role: 'neutral', label: 'Not loaded' };
const resultBadge = (source, status) => {
  const badge = evidenceOutcome({ source, outcome: status }) || NOT_RECORDED;
  return { status: badge.status, role: badge.role, label: badge.label ?? runStatusOf(badge.status).label };
};
// Short cells keep their words whole ("Workspace", never "Workspac / e"); column
// minimums apply to the desktop table only, not the stacked phone list.
const nowrap = (minWidth) => ({ whiteSpace: 'nowrap', ...(minWidth ? { minWidth: { sm: minWidth } } : {}) });
const kinds = { text_comparison: 'Text comparison', application_verification: 'Application verification', external_pipeline: 'External pipeline' };

// Display and controlled selection only. Sharing always belongs to the caller,
// which must explicitly confirm it; neither selection nor Inspect shares data.
export default function EvidenceSummary({ items = [], selectable = false, selected = [], onToggle, onInspect, renderAction, emptyText = 'No matching test evidence. Not tested is not a pass.' }) {
  const selections = selected instanceof Set ? selected : new Set(selected);
  if (!items.length) return <Typography variant="s1" color="text.secondary">{emptyText}</Typography>;
  return <TableContainer sx={{ minWidth: 0, maxWidth: 1 }}>
    <Table size="small" aria-label="Test evidence" sx={stackedTableSx}>
      <TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
        {selectable && <TableCell padding="checkbox">Attach</TableCell>}
        {['Evidence', 'Result', 'Coverage', 'Visibility', 'Expires', 'Inspect'].map((label) => <TableCell key={label} sx={{ whiteSpace: 'nowrap' }}>{label}</TableCell>)}
      </TableRow></TableHead>
      <TableBody>{items.map((item) => {
        const key = `${item.source}:${item.id}`;
        const checked = selections.has(key) || selections.has(item.id);
        const title = item.title || kinds[item.kind] || 'Test result';
        const notLoaded = item.resultState === 'not_loaded';
        const result = notLoaded ? null : item.outcome || (item.status === 'completed' ? null : item.status);
        const counts = item.counts;
        return <TableRow key={key} hover selected={selectable && checked} sx={{ height: ROW_HEIGHT.default }}>
          {selectable && <TableCell padding="checkbox"><Checkbox size="small" checked={checked} disabled={!onToggle || (!checked && item.visibility === 'private')} onChange={() => onToggle(item)} inputProps={{ 'aria-label': `Attach ${title}` }} /></TableCell>}
          <TableCell sx={{ minWidth: 180 }}><Stack spacing={0.5}>
            <Stack direction="row" gap={1} alignItems="center" flexWrap="wrap"><Typography variant="s1">{title}</Typography>{item.origin === 'from_test' ? <Chip size="small" variant="outlined" label="From your test" /> : item.suggested && <Chip size="small" variant="outlined" label="Suggested" />}</Stack>
            {notLoaded
              ? <Typography variant="s3" color="text.secondary">Renew the review to recheck this result.</Typography>
              : !result && !item.results?.length && <Typography variant="s3" color="text.secondary">Result not recorded; inspect before relying on it.</Typography>}
            {item.targetMatch !== undefined && <Typography variant="s3" color="text.secondary">{item.targetMatch ? 'Matches target' : 'Different target'}</Typography>}
            {item.visibility === 'private' && <Typography variant="s3" color="text.secondary">Only you can see this result. Share it with the workspace to attach it.</Typography>}
            {renderAction?.(item)}
          </Stack></TableCell>
          {/* Result is the badge only (per-check results keep their check name beside each badge). */}
          <TableCell sx={nowrap(140)}>{notLoaded ? <StatusBadge {...NOT_LOADED} /> : item.results?.length ? <Stack spacing={0.5}>{item.results.map((outcome) => <Stack key={outcome.label} direction="row" gap={0.5} alignItems="center"><Typography variant="s3">{outcome.label}</Typography><StatusBadge {...resultBadge(item.source, outcome.status)} /></Stack>)}</Stack> : <StatusBadge {...resultBadge(item.source, result)} />}</TableCell>
          <TableCell sx={{ minWidth: { sm: 160 } }}><Typography variant="s3">{evidenceCoverage(counts, item.cases)}</Typography></TableCell>
          <TableCell sx={nowrap(110)}>{item.visibility === 'private' ? <StatusBadge status="private" /> : item.visibility === 'workspace' ? <StatusBadge status="workspace" role="neutral" label="Workspace" /> : <Typography component="span" variant="s3">Not reported</Typography>}</TableCell>
          <TableCell sx={nowrap(120)}><Typography component="span" variant="s3"><When value={item.expiresAt} kind="expiry" /></Typography></TableCell>
          <TableCell data-cell="actions" sx={nowrap()}><Button size="small" disabled={!onInspect} onClick={() => onInspect(item)}>Inspect</Button></TableCell>
        </TableRow>;
      })}</TableBody>
    </Table>
  </TableContainer>;
}

EvidenceSummary.propTypes = {
  items: PropTypes.arrayOf(PropTypes.object),
  selectable: PropTypes.bool,
  selected: PropTypes.oneOfType([PropTypes.arrayOf(PropTypes.string), PropTypes.instanceOf(Set)]),
  onToggle: PropTypes.func,
  onInspect: PropTypes.func,
  renderAction: PropTypes.func,
  emptyText: PropTypes.string,
};
