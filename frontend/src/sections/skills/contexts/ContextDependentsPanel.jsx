import { useState } from 'react';
import PropTypes from 'prop-types';
import { Link as RouterLink } from 'react-router-dom';
import { Button, Checkbox, IconButton, Pagination, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Tooltip, Typography } from '@mui/material';
import { useContextDependents } from 'src/api/skills/context-dependents';
import { useSkill } from 'src/api/skills';
import { useSkillV11Scope } from 'src/api/skills/testing';
import ContentSkeleton from 'src/components/content-skeleton';
import EmptyState from 'src/components/empty-state';
import ErrorState from 'src/components/error-state';
import Iconify from 'src/components/iconify';
import StatusBadge from 'src/components/status-badge';
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from 'src/theme/sizes';
import { paths } from 'src/routes/paths';
import { relativeTime } from 'src/utils/format-time';
import TechnicalDetails from '../components/TechnicalDetails';
import ContextFollowerUpdate from './ContextFollowerUpdate';

// Domain: published references, accepted locks, selectors, pins, history.
// Signature: parent release and included child version remain side by side.
// Intent: authors find eligible followers without treating a pin as stale.
// Hierarchy: the exact-version table leads; scope and history are secondary.
// Palette/surfaces: shared neutral paper, divider and semantic status roles.
// Depth: flat rows, no cards or graph; typography m3/s1/s3, 12px spacing.
// Reject decorative graphs, rollout counters and bulk approval shortcuts.
const states = {
  pinned_as_intended: ['pinned', 'Pinned on purpose'],
  unavailable: ['unavailable', 'Upstream unavailable'],
  update_available: ['update_available', 'Update available'],
  current: ['current', 'Current'],
};
// One reference table per view. Active references carry the follower selection; history is read-only.
function ReferenceTable({ query, state, view, selected, onToggle, page, onPage, empty, done = [], selectable = [] }) {
  if (query.isLoading || state === 'loading') return <ContentSkeleton />;
  if (query.isError || state === 'error') return <ErrorState title="Dependents unavailable" detail="No empty-inventory assumption was made. Retry in your authorized workspace." onRetry={query.refetch} />;
  if (!query.data || !query.data.results.length || state === 'empty') return <EmptyState title={empty.title} description={empty.description} />;
  const active = view === 'active';
  return <>
    <Typography variant="s3" color="text.secondary">{query.data.count} {query.data.count === 1 ? 'reference' : 'references'} · <Tooltip title={query.data.as_of || 'Time unavailable'}><span>Checked {relativeTime(query.data.as_of)}</span></Tooltip></Typography>
    <TableContainer><Table size="small" aria-label={active ? 'Published context dependents' : 'Historical context references'} sx={{ '& thead': { display: { xs: 'none', sm: 'table-header-group' } }, '& tbody': { display: { xs: 'block', sm: 'table-row-group' } } }}>
      <TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>{active && <TableCell padding="checkbox"><Typography variant="s3">Update</Typography></TableCell>}<TableCell>Context release</TableCell><TableCell>Labels</TableCell><TableCell>Policy</TableCell><TableCell>Locked skill version</TableCell><TableCell>Update state</TableCell><TableCell>Review</TableCell></TableRow></TableHead>
      <TableBody>{query.data.results.map((row) => <TableRow hover key={`${row.context_id}:${row.context_version}`} sx={{ height: { sm: ROW_HEIGHT.default }, display: { xs: 'grid', sm: 'table-row' }, gridTemplateColumns: active ? '64px minmax(0, 1fr)' : 'minmax(0, 1fr)', py: { xs: 1.5, sm: 0 }, '& > td': { display: { xs: 'block', sm: 'table-cell' }, borderBottom: { xs: 0 }, whiteSpace: 'normal', overflowWrap: 'anywhere', py: { xs: 0.5 }, px: { xs: 0, sm: 1.5 }, gridColumn: active ? '2' : '1' }, ...(active ? { '& > td:first-of-type': { gridColumn: '1', gridRow: '1 / 7' } } : {}) }}>
        {active && <TableCell padding="checkbox"><Tooltip describeChild title={row.requested.version ? `Pinned to v${row.requested.version} on purpose. Change the pin in the context to update.` : row.update_state === 'pinned_as_intended' ? `Pinned to v${row.locked_version} on purpose. Change the pin in the context to update.` : ''}><span tabIndex={row.requested.version || row.update_state === 'pinned_as_intended' ? 0 : undefined}><Checkbox size="small" checked={selected.some((item) => item.context_id === row.context_id && item.context_version === row.context_version)} disabled={Boolean(row.requested.version) || row.update_state !== 'update_available' || !selectable.some((item) => item.context_id === row.context_id && item.context_version === row.context_version)} onChange={() => onToggle(row)} inputProps={{ 'aria-label': `Select ${row.name} v${row.context_version} for update` }} /></span></Tooltip></TableCell>}
        <TableCell><Typography variant="s1">{row.name} · v{row.context_version}</Typography></TableCell>
        <TableCell>{row.labels.join(', ') || 'No labels'}</TableCell>
        <TableCell>{row.requested.version ? `Pin v${row.requested.version}` : `Follow ${row.requested.label}`}</TableCell>
        <TableCell><Typography variant="s3">Locked skill version: v{row.locked_version}</Typography></TableCell>
        <TableCell><StatusBadge status={done.includes(row.context_id) ? 'draft' : states[row.update_state]?.[0] || 'unknown'} label={done.includes(row.context_id) ? 'Draft ready' : states[row.update_state]?.[1] || 'Unknown'} /></TableCell>
        <TableCell><Button variant="outlined" size="small" component={RouterLink} to={`${paths.dashboard.skills.contextDetail(row.context_id)}?version=${row.context_version}`} aria-label={`Inspect ${row.name} v${row.context_version}`}>Open context</Button></TableCell>
      </TableRow>)}</TableBody>
    </Table></TableContainer>
    {query.data.total_pages > 1 && <Pagination page={page} count={query.data.total_pages} onChange={(_, value) => onPage(value)} />}
  </>;
}
ReferenceTable.propTypes = { done: PropTypes.array, selectable: PropTypes.array, query: PropTypes.object.isRequired, state: PropTypes.string, view: PropTypes.string.isRequired, selected: PropTypes.array, onToggle: PropTypes.func, page: PropTypes.number.isRequired, onPage: PropTypes.func.isRequired, empty: PropTypes.shape({ title: PropTypes.string, description: PropTypes.string }).isRequired };
function Dependents({ skillId, state }) {
  const [page, setPage] = useState(1);
  const [historyPage, setHistoryPage] = useState(1);
  const [done, setDone] = useState([]);
  // The complete inventory is one view: active references first, every historical reference below it, no scope to switch.
  const query = useContextDependents(skillId, { view: 'active', page });
  const skill = useSkill(skillId);
  const sourceRelease = JSON.stringify([skill.data?.latestVersion, ...(skill.data?.labels || []).map((label) => [label.name, label.versionId, label.version]).sort((a, b) => a[0].localeCompare(b[0]))]);
  const history = useContextDependents(skillId, { view: 'history', page: historyPage });
  // Followers with an available update are preselected; deliberate pins and current references are shown but never selectable.
  const [chosen, setChosen] = useState(null);
  const rows = query.data?.results || [];
  // A context can have both a production and a newer published reference.
  // One explicit source per context prevents preparing two conflicting drafts.
  const contexts = [...new Set(rows.map((row) => row.context_id))].map((id) => {
    const references = rows.filter((row) => row.context_id === id);
    return references.find((row) => row.labels.includes('production')) || references.reduce((latest, row) => row.context_version > latest.context_version ? row : latest);
  });
  const eligible = contexts.filter((row) => !row.requested.version && row.update_state === 'update_available');
  const selected = eligible.filter((row) => (chosen === null ? true : chosen.includes(row.context_id)));
  const toggle = (row) => setChosen((selected.some((item) => item.context_id === row.context_id) ? selected.filter((item) => item.context_id !== row.context_id) : [...selected, row]).map((item) => item.context_id));
  return <Stack spacing={1.5}>
    <Stack direction="row" spacing={1} alignItems="flex-start" justifyContent="space-between">
      <Stack spacing={0.5}><Typography variant="m3" component="h2">Contexts using this skill</Typography>
        {query.data && !query.isError && <Typography variant="s1">{contexts.length} {contexts.length === 1 ? 'agent context uses' : 'agent contexts use'} {skill.data?.name || 'this skill'}.{query.data.total_pages > 1 ? ' (on this page)' : ''}{contexts.length > 0 && ` ${contexts.map((row) => row.requested.version ? `${row.name} is pinned to v${row.locked_version}` : `${row.name} follows ${row.requested.label}${row.update_state === 'update_available' && skill.data?.labels?.find((label) => label.name === row.requested.label)?.version ? ` and can update to v${skill.data.labels.find((label) => label.name === row.requested.label).version}` : ''}`).join('; ')}.`}</Typography>}
      </Stack>
      <Tooltip title="Refresh dependents"><IconButton aria-label="Refresh dependents" onClick={() => { query.refetch(); history.refetch(); }}><Iconify icon="solar:refresh-linear" width={16} /></IconButton></Tooltip>
    </Stack>
    <Typography variant="s3" color="text.secondary">These are published references, not proof of delivery. Prepare starts a draft for each selected context and shows the version changes. Nothing changes until you accept and apply.</Typography>
    <ReferenceTable selectable={eligible} done={done} query={query} state={state} view="active" selected={selected} onToggle={toggle} page={page} onPage={setPage}
      empty={{ title: 'No published references', description: 'No authorized context references were found in this view. Drafts and unobserved consumers are not included.' }} />
    {eligible.length > 0 && <ContextFollowerUpdate skillName={skill.data?.name} onApplied={(ids) => setDone((previous) => [...new Set([...previous, ...ids])])} skillId={skillId} sourceRelease={sourceRelease} targets={selected.map((row) => ({ contextId: row.context_id, name: row.name, version: row.context_version, ...(row.labels.includes('production') ? { productionVersion: row.context_version } : {}) }))} />}
    {(history.isError || history.data?.total_pages > 1 || history.data?.results?.some((row) => !rows.some((active) => active.context_id === row.context_id && active.context_version === row.context_version))) && <TechnicalDetails title="Show superseded releases">
    <ReferenceTable query={history} state={state} view="history" page={historyPage} onPage={setHistoryPage}
      empty={{ title: 'No historical references', description: 'No retired or superseded context releases referenced this skill.' }} />
    </TechnicalDetails>}
  </Stack>;
}
Dependents.propTypes = { skillId: PropTypes.string.isRequired, state: PropTypes.string };
export default function ContextDependentsPanel(props) {
  const { identity } = useSkillV11Scope();
  return <Dependents key={`${identity}:${props.skillId}`} {...props} />;
}
ContextDependentsPanel.propTypes = Dependents.propTypes;
