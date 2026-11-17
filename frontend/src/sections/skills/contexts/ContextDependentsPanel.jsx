import { useState } from 'react';
import PropTypes from 'prop-types';
import { useNavigate } from 'react-router-dom';
import { useTheme } from '@mui/material/styles';
import { visuallyHidden } from '@mui/utils';
import { Box, Checkbox, IconButton, useMediaQuery, Pagination, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Tooltip, Typography } from '@mui/material';
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
import RowActions from '../components/RowActions';
import { rowLinkProps } from '../components/responsiveTable';
import { followsLabel, pinnedTo } from '../utils/terms';
import '../components/skillIcons';
import ContextFollowerUpdate from './ContextFollowerUpdate';

// Domain: published references, accepted locks, selectors, pins, history.
// Signature: parent release and included child version remain side by side.
// Intent: authors find eligible followers without treating a pin as stale.
// Hierarchy: the exact-version table leads; scope and history are secondary.
// Palette/surfaces: shared neutral paper, divider and semantic status roles.
// Depth: flat rows, no cards or graph; typography m3/s1/s3, 12px spacing.
// Reject decorative graphs, rollout counters and bulk approval shortcuts.
// Named "Linked contexts" (SKILL-TRACES §2.2): a context links a skill; nothing here says it is used.
// Rows follow the shared row-action pattern (§16 UXF-12): the whole row opens the context release,
// and "Open context" and "Change pin" sit in the trailing ⋯ (a chevron on a phone, where the pin is
// changed from the context's Linked skills tab).
const states = {
  pinned_as_intended: ['pinned', 'Pinned on purpose'],
  unavailable: ['unavailable', 'Upstream unavailable'],
  update_available: ['update_available', 'Update available'],
  current: ['current', 'Current'],
};
// Labels read as names ("Production"), never as the raw lowercase label.
function LabelChips({ labels }) {
  if (!labels?.length) return <Typography variant="s3" color="text.secondary">No labels</Typography>;
  return <Stack direction="row" useFlexGap spacing={0.5} sx={{ flexWrap: 'wrap' }}>{labels.map((name) => (name === 'production' ? <StatusBadge key={name} status="released" label="Production" /> : <Typography key={name} component="span" variant="s3">{`${name.charAt(0).toUpperCase()}${name.slice(1)}`}</Typography>))}</Stack>;
}
LabelChips.propTypes = { labels: PropTypes.array };
// One reference table per view. Active references carry the follower selection; history is read-only.
function ReferenceTable({ query, state, view, selected, onToggle, page, onPage, empty, done = [], selectable = [] }) {
  const theme = useTheme();
  const phone = useMediaQuery(theme.breakpoints.down('sm'));
  const navigate = useNavigate();
  if (query.isLoading || state === 'loading') return <ContentSkeleton />;
  if (query.isError || state === 'error') return <ErrorState title="Dependents unavailable" detail="Couldn't load the contexts that link this skill. Check your connection and try again." onRetry={query.refetch} />;
  if (!query.data || !query.data.results.length || state === 'empty') return <EmptyState title={empty.title} description={empty.description} />;
  const active = view === 'active';
  // One count line, in words: active rows count the contexts (a context can hold two releases that link the skill).
  const paged = query.data.total_pages > 1;
  const contextCount = new Set(query.data.results.map((row) => row.context_id)).size;
  const countLine = active
    ? `${contextCount} ${contextCount === 1 ? 'agent context links' : 'agent contexts link'} this skill${paged ? ' (on this page)' : ''}`
    : `${query.data.count} earlier context ${query.data.count === 1 ? 'release' : 'releases'}`;
  return <>
    <Typography variant="s3" color="text.secondary">{countLine} · <Tooltip title={query.data.as_of || 'Time unavailable'}><span>Checked {relativeTime(query.data.as_of)}</span></Tooltip></Typography>
    <TableContainer><Table size="small" aria-label={active ? 'Published context dependents' : 'Historical context references'} sx={{ '& thead': { display: { xs: 'none', sm: 'table-header-group' } }, '& tbody': { display: { xs: 'block', sm: 'table-row-group' } } }}>
      <TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>{active && <TableCell padding="checkbox"><Typography variant="s3">Update</Typography></TableCell>}<TableCell>Agent context</TableCell><TableCell>Labels</TableCell><TableCell>Version</TableCell><TableCell>Update state</TableCell><TableCell align="right"><Box component="span" sx={visuallyHidden}>Actions</Box></TableCell></TableRow></TableHead>
      <TableBody>{query.data.results.map((row) => {
        const pinned = Boolean(row.requested.version) || row.update_state === 'pinned_as_intended';
        const hasLabels = Boolean(row.labels?.length);
        // Below sm each row is a two-line card in a grid: name and update state; version and labels; the chevron on the right.
        const at = (column, rowLine) => ({ gridColumn: { xs: String(column + (active ? 1 : 0)), sm: 'auto' }, gridRow: { xs: String(rowLine), sm: 'auto' } });
        const contextUrl = `${paths.dashboard.skills.contextDetail(row.context_id)}?version=${row.context_version}`;
        const open = { label: 'Open context', onClick: () => navigate(contextUrl) };
        const changePin = active && pinned ? [{ label: 'Change pin', onClick: () => navigate(`${paths.dashboard.skills.contextDetail(row.context_id)}?tab=links`) }] : [];
        const link = rowLinkProps(open);
        return <TableRow key={`${row.context_id}:${row.context_version}`} {...link} sx={[{ height: { sm: ROW_HEIGHT.default }, display: { xs: 'grid', sm: 'table-row' }, gridTemplateColumns: active ? '32px minmax(0, 1fr) auto auto' : 'minmax(0, 1fr) auto auto', columnGap: 1, rowGap: 0.5, alignItems: 'center', py: { xs: 1.5, sm: 0 }, '& > td': { display: { xs: 'block', sm: 'table-cell' }, borderBottom: { xs: 0 }, whiteSpace: 'normal', overflowWrap: 'anywhere', py: { xs: 0 }, px: { xs: 0, sm: 1.5 }, minWidth: 0 }, '& > td[data-empty="true"]': { display: { xs: 'none', sm: 'table-cell' } } }, link.sx || {}]}>
          {active && <TableCell padding="checkbox" data-row-ignore sx={{ gridColumn: { xs: '1' }, gridRow: { xs: '1 / span 2' }, alignSelf: { xs: 'start' } }}>{(() => {
            const disabled = Boolean(row.requested.version) || row.update_state !== 'update_available' || !selectable.some((item) => item.context_id === row.context_id && item.context_version === row.context_version);
            const reason = row.requested.version ? `Pinned to v${row.requested.version} on purpose. Change the pin in the context to update.` : row.update_state === 'pinned_as_intended' ? `Pinned to v${row.locked_version} on purpose. Change the pin in the context to update.` : '';
            return <Tooltip describeChild title={reason}><Stack component="span" direction="row" alignItems="center" tabIndex={pinned ? 0 : undefined}>
              <Checkbox size="small" checked={selected.some((item) => item.context_id === row.context_id && item.context_version === row.context_version)} disabled={disabled} sx={{ ...(disabled ? { color: 'action.disabled' } : {}), p: { xs: 0.5, sm: 1.125 } }} onChange={() => onToggle(row)} inputProps={{ 'aria-label': `Select ${row.name} v${row.context_version} for update` }} />
            </Stack></Tooltip>;
          })()}</TableCell>}
          <TableCell sx={at(1, 1)}><Stack component="span" direction="row" alignItems="center" spacing={0.5}><Typography component="span" variant="s1" sx={{ overflowWrap: 'anywhere' }}>{row.name}<Typography component="span" variant="s3" color="text.secondary"> v{row.context_version}</Typography></Typography>{pinned && <Iconify icon="solar:lock-keyhole-linear" width={16} aria-hidden sx={{ color: 'text.disabled', flexShrink: 0 }} />}</Stack></TableCell>
          <TableCell data-empty={hasLabels ? undefined : 'true'} sx={{ ...at(2, 2), justifySelf: { xs: 'end' } }}><LabelChips labels={row.labels} /></TableCell>
          <TableCell sx={at(1, 2)}><Typography variant="s3" color="text.secondary">{pinned ? pinnedTo(row.locked_version) : `${followsLabel(row.requested.label)} · v${row.locked_version}`}</Typography></TableCell>
          <TableCell sx={{ ...at(2, 1), justifySelf: { xs: 'end' } }}><StatusBadge status={done.includes(row.context_id) ? 'draft' : states[row.update_state]?.[0] || 'unknown'} label={done.includes(row.context_id) ? 'Draft ready' : states[row.update_state]?.[1] || 'Unknown'} /></TableCell>
          <TableCell align="right" sx={{ width: { sm: '1%' }, whiteSpace: { sm: 'nowrap' }, gridColumn: { xs: '-2 / -1' }, gridRow: { xs: '1 / span 2' }, justifySelf: { xs: 'end' } }}>
            <RowActions name={`${row.name} v${row.context_version}`} primary={open} actions={changePin} phone={phone} />
          </TableCell>
        </TableRow>;
      })}</TableBody>
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
      <Stack spacing={0.5}><Typography variant="m3" component="h2">Linked contexts</Typography>
        {query.data && !query.isError && contexts.length > 0 && <Typography variant="s1">{`${contexts.map((row) => row.requested.version ? `${row.name} is pinned to v${row.locked_version}` : `${row.name} follows ${row.requested.label}${row.update_state === 'update_available' && skill.data?.labels?.find((label) => label.name === row.requested.label)?.version ? ` and can update to v${skill.data.labels.find((label) => label.name === row.requested.label).version}` : ''}`).join('; ')}.`}</Typography>}
      </Stack>
      <Tooltip title="Refresh dependents"><IconButton aria-label="Refresh dependents" onClick={() => { query.refetch(); history.refetch(); }}><Iconify icon="solar:refresh-linear" width={16} /></IconButton></Tooltip>
    </Stack>
    <Typography variant="s3" color="text.secondary">Open a context to see which apps have received it.</Typography>
    <ReferenceTable selectable={eligible} done={done} query={query} state={state} view="active" selected={selected} onToggle={toggle} page={page} onPage={setPage}
      empty={{ title: 'No agent contexts link this skill yet', description: 'Contexts appear here once they publish a version that links it. Drafts are not listed.' }} />
    {eligible.length > 0 && <ContextFollowerUpdate skillName={skill.data?.name} impacts={selected.map((row) => ({ contextId: row.context_id, from: row.locked_version, to: skill.data?.labels?.find((label) => label.name === row.requested.label)?.version }))} onApplied={(ids) => setDone((previous) => [...new Set([...previous, ...ids])])} skillId={skillId} sourceRelease={sourceRelease} targets={selected.map((row) => ({ contextId: row.context_id, name: row.name, version: row.context_version, ...(row.labels.includes('production') ? { productionVersion: row.context_version } : {}) }))} />}
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
