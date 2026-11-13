import PropTypes from 'prop-types';
import { Button, Checkbox, Chip, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Tooltip, Typography } from '@mui/material';
import StatusBadge from 'src/components/status-badge';
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from 'src/theme/sizes';
import { relativeReviewTime } from '../utils/review-display';

const kinds = { text_comparison: 'Text comparison', application_verification: 'Application verification', external_pipeline: 'External pipeline' };
const reported = (value) => value ?? 'Not reported';

// Display and controlled selection only. Sharing always belongs to the caller,
// which must explicitly confirm it; neither selection nor Inspect shares data.
export default function EvidenceSummary({ items = [], selectable = false, selected = [], onToggle, onInspect, renderAction, emptyText = 'No matching test evidence. Not tested is not a pass.' }) {
  const selections = selected instanceof Set ? selected : new Set(selected);
  if (!items.length) return <Typography variant="s1" color="text.secondary">{emptyText}</Typography>;
  return <TableContainer sx={{ minWidth: 0, maxWidth: 1 }}>
    <Table size="small" aria-label="Test evidence">
      <TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
        {selectable && <TableCell padding="checkbox">Attach</TableCell>}
        {['Evidence', 'Result', 'Coverage', 'Visibility', 'Expires', 'Inspect'].map((label) => <TableCell key={label}>{label}</TableCell>)}
      </TableRow></TableHead>
      <TableBody>{items.map((item) => {
        const key = `${item.source}:${item.id}`;
        const checked = selections.has(key) || selections.has(item.id);
        const title = item.title || kinds[item.kind] || 'Test result';
        const result = item.outcome || (item.status === 'completed' ? null : item.status);
        const counts = item.counts;
        return <TableRow key={key} hover selected={selectable && checked} sx={{ height: ROW_HEIGHT.default }}>
          {selectable && <TableCell padding="checkbox"><Checkbox size="small" checked={checked} disabled={!onToggle || (!checked && item.visibility === 'private')} onChange={() => onToggle(item)} inputProps={{ 'aria-label': `Attach ${title}` }} /></TableCell>}
          <TableCell sx={{ minWidth: 180 }}><Stack spacing={0.5}>
            <Stack direction="row" gap={1} alignItems="center" flexWrap="wrap"><Typography variant="s1">{title}</Typography>{item.suggested && <Chip size="small" variant="outlined" label="Suggested" />}</Stack>
            {!result && !item.results?.length && <Typography variant="s3" color="text.secondary">Result not recorded; inspect before relying on it.</Typography>}
            {item.targetMatch !== undefined && <Typography variant="s3" color="text.secondary">{item.targetMatch ? 'Matches target' : 'Different target'}</Typography>}
            {item.visibility === 'private' && <Typography variant="s3" color="text.secondary">Only you can see this result. Share it with the workspace to attach it.</Typography>}
            {renderAction?.(item)}
          </Stack></TableCell>
          <TableCell>{item.results?.length ? <Stack spacing={0.5}>{item.results.map((outcome) => <Stack key={outcome.label} direction="row" gap={0.5} alignItems="center"><Typography variant="s3">{outcome.label}</Typography><StatusBadge status={outcome.status || 'not_tested'} label={outcome.status ? undefined : 'Not recorded'} /></Stack>)}</Stack> : <StatusBadge status={result || 'not_tested'} label={result ? undefined : 'Not recorded'} />}</TableCell>
          <TableCell><Typography variant="s3">{counts ? `${reported(counts.graded)} of ${reported(counts.planned)} cases · ${reported(counts.regressed)} ${counts.regressed === 1 ? 'regression' : 'regressions'} · ${reported(counts.disagreements)} human ${counts.disagreements === 1 ? 'disagreement' : 'disagreements'}` : 'Not reported'}</Typography></TableCell>
          <TableCell>{item.visibility === 'private' ? <StatusBadge status="private" /> : item.visibility === 'workspace' ? 'Workspace' : 'Not reported'}</TableCell>
          <TableCell><Tooltip title={item.expiresAt || 'Not reported'}><Typography component="span" variant="s3">{relativeReviewTime(item.expiresAt) || 'Not reported'}</Typography></Tooltip></TableCell>
          <TableCell><Button size="small" disabled={!onInspect} onClick={() => onInspect(item)}>Inspect</Button></TableCell>
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
