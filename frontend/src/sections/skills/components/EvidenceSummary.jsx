import PropTypes from 'prop-types';
import { Button, Checkbox, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Tooltip, Typography } from '@mui/material';
import StatusBadge from 'src/components/status-badge';
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from 'src/theme/sizes';

const kinds = { text_comparison: 'Text comparison', application_verification: 'Application verification', external_pipeline: 'External pipeline' };
const reported = (value) => value ?? 'Not reported';
function coverage(counts) {
  if (!counts || Object.values(counts).every((value) => value == null)) return 'Not reported';
  return `${reported(counts.graded)} of ${reported(counts.planned)} cases · ${reported(counts.regressed)} ${counts.regressed === 1 ? 'regression' : 'regressions'} · ${reported(counts.disagreements)} human ${counts.disagreements === 1 ? 'disagreement' : 'disagreements'}`;
}
function timestamp(value) {
  if (!value) return 'Not reported';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Not reported' : date.toLocaleString();
}

// Selection is controlled. Callbacks receive the original item (never a
// shortened ID); selecting a private row does not share it or change visibility.
// Prefer source:id keys when more than one evidence source is shown.
export default function EvidenceSummary({ items = [], selectable = false, selected = [], onToggle, onInspect, emptyText = 'No matching test evidence. Not tested is not a pass.' }) {
  const selections = selected instanceof Set ? selected : new Set(selected);
  if (!items.length) return <Typography variant="s1" color="text.secondary">{emptyText}</Typography>;
  return <TableContainer sx={{ minWidth: 0, maxWidth: 1 }}>
    <Table size="small" aria-label="Test evidence">
      <TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
        {selectable && <TableCell padding="checkbox"><Typography variant="s3">Attach</Typography></TableCell>}
        {['Evidence', 'Result', 'Coverage', 'Visibility', 'Expires', 'Actions'].map((label) => <TableCell key={label}>{label}</TableCell>)}
      </TableRow></TableHead>
      <TableBody>{items.map((item) => {
        const key = `${item.source}:${item.id}`;
        const checked = selections.has(key) || selections.has(item.id);
        return <TableRow key={key} hover selected={selectable && checked} sx={{ height: ROW_HEIGHT.default }}>
          {selectable && <TableCell padding="checkbox"><Checkbox size="small" checked={checked} disabled={!onToggle} onChange={() => onToggle(item)} inputProps={{ 'aria-label': `Attach ${item.title || 'evidence'}` }} /></TableCell>}
          <TableCell><Stack spacing={0.5}>
            <Typography variant="s1" sx={{ fontWeight: 500 }}>{item.title || 'Not reported'}</Typography>
            <Typography variant="s3" color="text.secondary">{kinds[item.kind] || 'Not reported'} · Candidate {item.candidateNumber != null ? `v${item.candidateNumber}` : 'Not reported'} · Baseline {item.baselineNumber != null ? `v${item.baselineNumber}` : 'Not reported'}</Typography>
            <Typography variant="s3" color="text.secondary">Completed: {timestamp(item.completedAt)}</Typography>
            <Typography variant="s3" color="text.secondary">{item.targetMatch === true ? 'Matches target' : item.targetMatch === false ? 'Different target' : 'Target: Not reported'}</Typography>
          </Stack></TableCell>
          <TableCell>{item.status ? <StatusBadge status={item.status} /> : 'Not reported'}</TableCell>
          <TableCell><Typography variant="s1">{coverage(item.counts)}</Typography></TableCell>
          <TableCell>{item.visibility === 'private' ? <StatusBadge status="private" /> : item.visibility === 'workspace' ? 'Workspace' : 'Not reported'}</TableCell>
          <TableCell><Tooltip title={item.expiresAt || 'Not reported'}><Typography component="span" variant="s3">{timestamp(item.expiresAt)}</Typography></Tooltip></TableCell>
          <TableCell><Button size="small" disabled={!onInspect} onClick={() => onInspect(item)}>Inspect</Button></TableCell>
        </TableRow>;
      })}</TableBody>
    </Table>
  </TableContainer>;
}

EvidenceSummary.propTypes = {
  items: PropTypes.arrayOf(PropTypes.shape({
    id: PropTypes.string.isRequired,
    source: PropTypes.string,
    kind: PropTypes.oneOf(['text_comparison', 'application_verification', 'external_pipeline']),
    title: PropTypes.string,
    candidateNumber: PropTypes.number,
    baselineNumber: PropTypes.number,
    status: PropTypes.string,
    counts: PropTypes.shape({ graded: PropTypes.number, planned: PropTypes.number, regressed: PropTypes.number, disagreements: PropTypes.number }),
    completedAt: PropTypes.string,
    expiresAt: PropTypes.string,
    visibility: PropTypes.oneOf(['private', 'workspace']),
    targetMatch: PropTypes.bool,
  })),
  selectable: PropTypes.bool,
  selected: PropTypes.oneOfType([PropTypes.arrayOf(PropTypes.string), PropTypes.instanceOf(Set)]),
  onToggle: PropTypes.func,
  onInspect: PropTypes.func,
  emptyText: PropTypes.string,
};
