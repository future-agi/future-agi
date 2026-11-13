import PropTypes from 'prop-types';
import { Box, Button, Checkbox, Chip, Stack, Tooltip, Typography } from '@mui/material';
import StatusBadge from 'src/components/status-badge';
import IdentityChip from './IdentityChip';
import { relativeReviewTime } from '../utils/review-display';

const kinds = { text_comparison: 'Text comparison', application_verification: 'Application verification', external_pipeline: 'External pipeline' };

// Selection always uses full source:id keys. Completion alone is not a pass.
export default function EvidenceSummary({ items = [], selectable = false, selected = [], onToggle, onInspect, emptyText = 'No matching test evidence. Not tested is not a pass.' }) {
  const selections = selected instanceof Set ? selected : new Set(selected);
  if (!items.length) return <Typography variant="s1" color="text.secondary">{emptyText}</Typography>;
  return <Stack spacing={1} aria-label="Test evidence">
    {items.map((item) => {
      const key = `${item.source}:${item.id}`;
      const checked = selections.has(key) || selections.has(item.id);
      const title = item.title || kinds[item.kind] || 'Test result';
      const counts = item.counts;
      const result = item.outcome || (item.status === 'completed' ? null : item.status);
      return <Box key={key} sx={{ border: 1, borderColor: 'divider', borderRadius: 1, p: 1.5, minWidth: 0 }}>
        <Stack direction="row" spacing={1} alignItems="flex-start">
          {selectable && <Checkbox size="small" sx={{ p: 0.5 }} checked={checked} disabled={!onToggle} onChange={() => onToggle(item)} inputProps={{ 'aria-label': `Attach ${title}` }} />}
          <Stack spacing={1} sx={{ minWidth: 0, flex: 1 }}>
            <Stack direction="row" gap={1} flexWrap="wrap" alignItems="center">
              <Typography variant="m3">{title}</Typography>
              {item.suggested && <Chip size="small" variant="outlined" label="Suggested" />}
              {result ? <StatusBadge status={result} /> : <Typography variant="s3" color="text.secondary">Outcome not reported</Typography>}
            </Stack>
            <Typography variant="s3" color="text.secondary">
              {[item.baselineNumber != null && `Baseline v${item.baselineNumber}`, item.completedAt && `Completed ${relativeReviewTime(item.completedAt)}`, item.targetMatch === true ? 'Matches target' : item.targetMatch === false ? 'Different target' : null].filter(Boolean).join(' · ') || 'Details not reported'}
            </Typography>
            {counts && <Typography variant="s3">{counts.graded ?? 'Not reported'} of {counts.planned ?? 'Not reported'} cases · {counts.regressed ?? 'Not reported'} {counts.regressed === 1 ? 'regression' : 'regressions'} · {counts.disagreements ?? 'Not reported'} human {counts.disagreements === 1 ? 'disagreement' : 'disagreements'}</Typography>}
            <Stack direction={{ xs: 'column', sm: 'row' }} gap={1} justifyContent="space-between" alignItems={{ sm: 'center' }}>
              <Typography variant="s3" color="text.secondary">{item.visibility === 'workspace' ? 'Workspace' : item.visibility === 'private' ? 'Private' : 'Visibility not reported'}{item.expiresAt && <> · <Tooltip title={item.expiresAt}><span>Expires {relativeReviewTime(item.expiresAt)}</span></Tooltip></>}</Typography>
              <Stack direction="row" gap={1} alignItems="center"><IdentityChip label="Result" kind="id" value={item.id} /><Button size="small" disabled={!onInspect} onClick={() => onInspect(item)}>Inspect</Button></Stack>
            </Stack>
          </Stack>
        </Stack>
      </Box>;
    })}
  </Stack>;
}

EvidenceSummary.propTypes = {
  items: PropTypes.arrayOf(PropTypes.object),
  selectable: PropTypes.bool,
  selected: PropTypes.oneOfType([PropTypes.arrayOf(PropTypes.string), PropTypes.instanceOf(Set)]),
  onToggle: PropTypes.func,
  onInspect: PropTypes.func,
  emptyText: PropTypes.string,
};
