import { useState } from 'react';
import PropTypes from 'prop-types';
import { Link as RouterLink } from 'react-router-dom';
import { Alert, Button, Checkbox, MenuItem, Pagination, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, TextField, Typography } from '@mui/material';
import { useContextDependents } from 'src/api/skills/context-dependents';
import { useSkillV11Scope } from 'src/api/skills/testing';
import ContentSkeleton from 'src/components/content-skeleton';
import EmptyState from 'src/components/empty-state';
import ErrorState from 'src/components/error-state';
import Section from 'src/components/section';
import StatusBadge from 'src/components/status-badge';
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from 'src/theme/sizes';
import { paths } from 'src/routes/paths';
import ContextFollowerUpdate from './ContextFollowerUpdate';

// Domain: published references, accepted locks, selectors, pins, history.
// Signature: parent release and included child version remain side by side.
// Intent: authors find eligible followers without treating a pin as stale.
// Hierarchy: the exact-version table leads; scope and history are secondary.
// Palette/surfaces: shared neutral paper, divider and semantic status roles.
// Depth: flat rows, no cards or graph; typography m3/s1/s3, 12px spacing.
// Reject decorative graphs, rollout counters and bulk approval shortcuts.
const states = {
  pinned_as_intended: ['unchanged', 'Pinned as intended'],
  unavailable: ['unavailable', 'Upstream unavailable'],
  update_available: ['needs_review', 'Update available'],
  current: ['current', 'Current'],
};
function Dependents({ skillId, state }) {
  const [view, setView] = useState('active');
  const [page, setPage] = useState(1);
  const query = useContextDependents(skillId, { view, page });
  // Followers with an available update are preselected; deliberate pins and current references are shown but never selectable.
  const [chosen, setChosen] = useState(null);
  const rows = query.data?.results || [];
  const eligible = view === 'active' ? rows.filter((row) => row.update_state === 'update_available') : [];
  const selected = eligible.filter((row) => (chosen === null ? true : chosen.includes(row.context_id)));
  const toggle = (row) => setChosen((selected.some((item) => item.context_id === row.context_id) ? selected.filter((item) => item.context_id !== row.context_id) : [...selected, row]).map((item) => item.context_id));
  return <Stack spacing={1.5}>
    <Section title="Dependent contexts" description="Published references in this workspace, not proof of delivery to a running agent." />
    <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
      <TextField select size="small" label="Reference scope" value={view} onChange={(event) => { setView(event.target.value); setPage(1); }}>
        <MenuItem value="active">Active references</MenuItem><MenuItem value="history">All published history</MenuItem>
      </TextField>
      <Button variant="outlined" onClick={() => query.refetch()}>Refresh dependents</Button>
    </Stack>
    <Alert severity="info">Following offers an update; it never changes an approved parent. Preview updates for the selected followers together, accept the exact results, then review and publish each parent. Deliberate pins stay unchanged.</Alert>
    {query.isLoading || state === 'loading' ? <ContentSkeleton /> : query.isError || state === 'error' ? <ErrorState title="Dependents unavailable" detail="No empty-inventory assumption was made. Retry in your authorized workspace." onRetry={query.refetch} /> : !query.data || !query.data.results.length || state === 'empty' ? <EmptyState title="No published references" description="No authorized context references were found in this view. Drafts and unobserved consumers are not included." /> : <>
      <Typography variant="s3" color="text.secondary">{query.data.count} references · observed {query.data.as_of}</Typography>
      <TableContainer><Table size="small" aria-label="Published context dependents">
        <TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>{view === 'active' && <TableCell padding="checkbox"><Typography variant="s3">Update</Typography></TableCell>}<TableCell>Context release</TableCell><TableCell>Labels</TableCell><TableCell>Policy</TableCell><TableCell>Included child</TableCell><TableCell>Update state</TableCell><TableCell>Review</TableCell></TableRow></TableHead>
        <TableBody>{query.data.results.map((row) => <TableRow hover key={`${row.context_id}:${row.context_version}`} sx={{ height: ROW_HEIGHT.default }}>
          {view === 'active' && <TableCell padding="checkbox"><Checkbox size="small" checked={selected.some((item) => item.context_id === row.context_id)} disabled={row.update_state !== 'update_available'} onChange={() => toggle(row)} inputProps={{ 'aria-label': `Select ${row.name} v${row.context_version} for update` }} /></TableCell>}
          <TableCell><Typography variant="s1">{row.name} · v{row.context_version}</Typography></TableCell>
          <TableCell>{row.labels.join(', ') || 'No labels'}</TableCell>
          <TableCell>{row.requested.version ? `Pin v${row.requested.version}` : `Follow ${row.requested.label}`}</TableCell>
          <TableCell>v{row.locked_version}</TableCell>
          <TableCell><StatusBadge status={states[row.update_state]?.[0] || 'unknown'} label={states[row.update_state]?.[1] || 'Unknown'} /></TableCell>
          <TableCell><Button component={RouterLink} to={`${paths.dashboard.skills.contextDetail(row.context_id)}?version=${row.context_version}`} aria-label={`Inspect ${row.name} v${row.context_version}`}>Inspect parent</Button></TableCell>
        </TableRow>)}</TableBody>
      </Table></TableContainer>
      {view === 'active' && eligible.length > 0 && <ContextFollowerUpdate skillId={skillId} targets={selected.map((row) => ({ contextId: row.context_id, name: row.name, version: row.context_version }))} />}
      {query.data.total_pages > 1 && <Pagination page={page} count={query.data.total_pages} onChange={(_, value) => setPage(value)} />}
    </>}
  </Stack>;
}
Dependents.propTypes = { skillId: PropTypes.string.isRequired, state: PropTypes.string };
export default function ContextDependentsPanel(props) {
  const { identity } = useSkillV11Scope();
  return <Dependents key={`${identity}:${props.skillId}`} {...props} />;
}
ContextDependentsPanel.propTypes = Dependents.propTypes;
