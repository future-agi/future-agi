import { useState } from 'react';
import PropTypes from 'prop-types';
import { Link as RouterLink } from 'react-router-dom';
import { Button, Checkbox, Link, Pagination, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Tooltip, Typography } from '@mui/material';
import { useContextDependents } from 'src/api/skills/context-dependents';
import { useSkill } from 'src/api/skills';
import { useSkillV11Scope } from 'src/api/skills/testing';
import ContentSkeleton from 'src/components/content-skeleton';
import EmptyState from 'src/components/empty-state';
import ErrorState from 'src/components/error-state';
import Section from 'src/components/section';
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
  pinned_as_intended: ['unchanged', 'Pinned as intended'],
  unavailable: ['unavailable', 'Upstream unavailable'],
  update_available: ['update_available', 'Update available'],
  current: ['current', 'Current'],
};
// One reference table per view. Active references carry the follower selection; history is read-only.
function ReferenceTable({ query, state, view, selected, onToggle, page, onPage, empty }) {
  if (query.isLoading || state === 'loading') return <ContentSkeleton />;
  if (query.isError || state === 'error') return <ErrorState title="Dependents unavailable" detail="No empty-inventory assumption was made. Retry in your authorized workspace." onRetry={query.refetch} />;
  if (!query.data || !query.data.results.length || state === 'empty') return <EmptyState title={empty.title} description={empty.description} />;
  const active = view === 'active';
  return <>
    <Typography variant="s3" color="text.secondary">{query.data.count} {query.data.count === 1 ? 'reference' : 'references'} · <Tooltip title={query.data.as_of || 'Time unavailable'}><span>Checked {relativeTime(query.data.as_of)}</span></Tooltip></Typography>
    <TableContainer><Table size="small" aria-label={active ? 'Published context dependents' : 'Historical context references'}>
      <TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>{active && <TableCell padding="checkbox"><Typography variant="s3">Update</Typography></TableCell>}<TableCell>Context release</TableCell><TableCell>Labels</TableCell><TableCell>Policy</TableCell><TableCell>Locked skill version</TableCell><TableCell>Update state</TableCell><TableCell>Review</TableCell></TableRow></TableHead>
      <TableBody>{query.data.results.map((row) => <TableRow hover key={`${row.context_id}:${row.context_version}`} sx={{ height: ROW_HEIGHT.default }}>
        {active && <TableCell padding="checkbox"><Checkbox size="small" checked={selected.some((item) => item.context_id === row.context_id)} disabled={row.update_state !== 'update_available'} onChange={() => onToggle(row)} inputProps={{ 'aria-label': `Select ${row.name} v${row.context_version} for update` }} /></TableCell>}
        <TableCell><Typography variant="s1">{row.name} · v{row.context_version}</Typography></TableCell>
        <TableCell>{row.labels.join(', ') || 'No labels'}</TableCell>
        <TableCell>{row.requested.version ? `Pin v${row.requested.version}` : `Follow ${row.requested.label}`}</TableCell>
        <TableCell>v{row.locked_version}</TableCell>
        <TableCell><StatusBadge status={states[row.update_state]?.[0] || 'unknown'} label={states[row.update_state]?.[1] || 'Unknown'} /></TableCell>
        <TableCell><Link variant="s1" underline="hover" component={RouterLink} to={`${paths.dashboard.skills.contextDetail(row.context_id)}?version=${row.context_version}`} aria-label={`Inspect ${row.name} v${row.context_version}`}>Open context</Link></TableCell>
      </TableRow>)}</TableBody>
    </Table></TableContainer>
    {query.data.total_pages > 1 && <Pagination page={page} count={query.data.total_pages} onChange={(_, value) => onPage(value)} />}
  </>;
}
ReferenceTable.propTypes = { query: PropTypes.object.isRequired, state: PropTypes.string, view: PropTypes.string.isRequired, selected: PropTypes.array, onToggle: PropTypes.func, page: PropTypes.number.isRequired, onPage: PropTypes.func.isRequired, empty: PropTypes.shape({ title: PropTypes.string, description: PropTypes.string }).isRequired };
function Dependents({ skillId, state }) {
  const [page, setPage] = useState(1);
  const [historyPage, setHistoryPage] = useState(1);
  // The complete inventory is one view: active references first, every historical reference below it, no scope to switch.
  const query = useContextDependents(skillId, { view: 'active', page });
  const skill = useSkill(skillId);
  const sourceRelease = JSON.stringify([skill.data?.latestVersion, ...(skill.data?.labels || []).map((label) => [label.name, label.versionId, label.version]).sort((a, b) => a[0].localeCompare(b[0]))]);
  const history = useContextDependents(skillId, { view: 'history', page: historyPage });
  // Followers with an available update are preselected; deliberate pins and current references are shown but never selectable.
  const [chosen, setChosen] = useState(null);
  const rows = query.data?.results || [];
  const eligible = rows.filter((row) => row.update_state === 'update_available');
  const selected = eligible.filter((row) => (chosen === null ? true : chosen.includes(row.context_id)));
  const toggle = (row) => setChosen((selected.some((item) => item.context_id === row.context_id) ? selected.filter((item) => item.context_id !== row.context_id) : [...selected, row]).map((item) => item.context_id));
  return <Stack spacing={1.5}>
    <Section title="Contexts using this skill" description="Published references in this workspace, not proof of delivery to a running agent." />
    <Button variant="outlined" sx={{ alignSelf: 'flex-start' }} onClick={() => { query.refetch(); history.refetch(); }}>Refresh dependents</Button>
    <Typography variant="s3" color="text.secondary">Updates go into drafts. Production doesn’t change until each context is released.</Typography>
    <ReferenceTable query={query} state={state} view="active" selected={selected} onToggle={toggle} page={page} onPage={setPage}
      empty={{ title: 'No published references', description: 'No authorized context references were found in this view. Drafts and unobserved consumers are not included.' }} />
    {eligible.length > 0 && <ContextFollowerUpdate skillId={skillId} sourceRelease={sourceRelease} targets={selected.map((row) => ({ contextId: row.context_id, name: row.name, version: row.context_version, ...(row.labels.includes('production') ? { productionVersion: row.context_version } : {}) }))} />}
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
