import { Suspense, cloneElement, isValidElement, lazy, useMemo, useState } from 'react';
import PropTypes from 'prop-types';
import { Alert, Box, Button, Drawer, IconButton, Link, MenuItem, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, TextField, Tooltip, Typography } from '@mui/material';
import { Link as RouterLink } from 'react-router-dom';
import { visuallyHidden } from '@mui/utils';
import When from '../components/When';
import Iconify from 'src/components/iconify';
import Section from 'src/components/section';
import EmptyState from 'src/components/empty-state';
import StatusBadge from 'src/components/status-badge';
import ContentSkeleton from 'src/components/content-skeleton';
import ErrorState from 'src/components/error-state';
import ListToolbar from 'src/components/list-toolbar';
import FormSearchField from 'src/components/FormSearchField/FormSearchField';
import { DataTablePagination } from 'src/components/data-table';
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from 'src/theme/sizes';
import { ReviewState } from '../review/ReleaseReviewBody';
import IdentityChip from '../components/IdentityChip';
import TechnicalDetails from '../components/TechnicalDetails';
import CapabilityNotice from '../components/CapabilityNotice';
import { RowActionsCell } from '../components/RowActions';
import { rowLinkProps } from '../components/responsiveTable';
import { CONNECTED_APPS, NO_REPORT_YET } from '../utils/terms';
import { formatUtcFull, formatWhen } from '../utils/format';
import { buildContextBindingTraceLink } from '../utils/traces';
import { useSkillTracesEnabled } from '../runtime/skillTracesFlag';
import { consumerStateNames, consumerNextStep, needsDeliveryVerification, consumerReportAt, midSentence, noAppHasReported, appsSummary } from './consumerPresentation';

// The authorized project picker pulls in the adoption panel and its queries, so it loads only when the
// `skillTraces` flag is on: with the flag off nothing from the runtime work is imported or rendered here.
const AuthorizedProjectPicker = lazy(() => import('../governance/AdoptionPanel').then((module) => ({ default: module.AuthorizedProjectPicker })));
const DAY_MS = 24 * 60 * 60 * 1000;
const RUNTIME_TRACES = 'Runtime traces';

const mobileLabel = (label) => ({ '&::before': { content: { xs: `"${label}"`, sm: 'none' }, display: 'block', typography: 's3', color: 'text.secondary', mb: 0.5 } });
const stageNames = { desired: 'Desired', resolved: 'Resolved', installed: 'Installed', discovered: 'Discovered', loaded: 'Delivered' };
function Stage({ stage, versionNumbers = {} }) {
  if (!stage) return <Typography variant="s3" color="text.secondary">Waiting for first report</Typography>;
  return versionNumbers[stage.versionId] !== undefined ? <Typography variant="s1">v{versionNumbers[stage.versionId]}</Typography> : <IdentityChip label="Release" kind="id" value={stage.versionId} />;
}
Stage.propTypes = { stage: PropTypes.object, versionNumbers: PropTypes.object };
// "As of just now": a relative time inside a sentence starts lowercase. Same structure as When
// (short text, full UTC time in a keyboard-reachable tooltip), so the flows' title lookups still hold.
export function AsOf({ value }) {
  const full = formatUtcFull(value);
  if (!full) return 'Time not reported';
  return <Tooltip title={full} describeChild>
    <Box component="time" dateTime={new Date(value).toISOString()} tabIndex={0} sx={{ whiteSpace: 'nowrap', borderRadius: 0.5, '&:focus-visible': { outline: 2, outlineColor: 'primary.main', outlineOffset: 2 } }}>{midSentence(formatWhen(value))}</Box>
  </Tooltip>;
}
AsOf.propTypes = { value: PropTypes.oneOfType([PropTypes.string, PropTypes.instanceOf(Date)]) };
const primaryRegistration = (action) => isValidElement(action) ? cloneElement(action, { variant: 'contained' }) : action;
const emptyDescription = 'Connect an app or repository so its deliveries can be checked.';
function ConsumerRows({ data, windowDays, onWindowChange, onPageChange, versionNumbers, previewState, registrationAction }) {
  const [selectedId, setSelectedId] = useState(null);
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('all');
  const selected = data.results.find((item) => item.binding.id === selectedId);
  const rows = previewState === 'filtered-empty' ? [] : data.results.filter((row) => row.binding.name.toLowerCase().includes(search.trim().toLowerCase()) && (status === 'all' || row.states.includes(status)));
  const clear = () => { setSearch(''); setStatus('all'); };
  const missing = data.results.filter(needsDeliveryVerification).length;
  // A column with nothing in it for every app says nothing, so it appears once any app has a value.
  const anyDelivered = data.results.some((row) => row.loaded);
  const anyReport = data.results.some((row) => consumerReportAt(row, data.asOf));
  const unreported = noAppHasReported(data.results, data.asOf);
  // UXF-15: "Target release" is what the app should receive, "Verification" is delivery, "Last report" stays.
  // "Runtime traces" (§7.4, flag `skillTraces`): the traces whose skill loads came through one binding,
  // over the window this list reports from. The project is chosen from the ones this person can open;
  // the link never confers access.
  const tracesOn = useSkillTracesEnabled();
  const [traceProject, setTraceProject] = useState('');
  const traceWindow = useMemo(() => {
    const end = new Date();
    return { windowStart: new Date(end.getTime() - windowDays * DAY_MS), windowEnd: end };
  }, [windowDays]);
  const traceLinkOf = (binding) => (tracesOn && traceProject ? buildContextBindingTraceLink({ projectId: traceProject, contextId: binding.contextId, bindingId: binding.id, ...traceWindow }) : null);
  // The item exists whenever the flag is on, so it can be found; without a project it says what is missing.
  const traceAction = (binding) => {
    if (!tracesOn) return [];
    const link = traceLinkOf(binding);
    if (link) return [{ label: RUNTIME_TRACES, to: link }];
    return [{ label: RUNTIME_TRACES, disabled: true, reason: traceProject ? "Couldn't build the link" : 'Choose a traces project first' }];
  };
  const columns = ['App or repository', 'Kind', 'Should receive', ...(anyDelivered ? ['Last delivered'] : []), 'Delivery', ...(anyReport ? ['Last report'] : [])];
  return <Stack spacing={1.5} sx={{ minWidth: 0 }}>
    {data.results.length > 0 && <Typography variant="s1" color="text.secondary">{appsSummary({ total: data.count ?? data.results.length, shown: data.results.length, missing, unreported })} <Link href="https://docs.futureagi.com/docs/skills/rollout-recovery" target="_blank" rel="noopener noreferrer">How apps report</Link></Typography>}
    <Typography variant="s3" color="text.secondary">As of <AsOf value={data.asOf} /></Typography>
    {/* UXF-15: when nothing has reported, the state is said once here and not on every row. */}
    {unreported && <Box><StatusBadge status="unknown" role="neutral" label={NO_REPORT_YET} /></Box>}
    {data.results.length > 0 && registrationAction}
    {data.results.length > 0 && <ListToolbar sx={{ flexWrap: 'wrap', gap: 1 }}>
      <FormSearchField label="Search apps" placeholder="Search apps" searchQuery={search} onChange={(event) => setSearch(event.target.value)} sx={{ minWidth: 0 }} />
      <TextField select size="small" label="Delivery" value={status} onChange={(event) => setStatus(event.target.value)}>
        <MenuItem value="all">All statuses</MenuItem>
        {Object.entries(consumerStateNames).map(([value, label]) => <MenuItem key={value} value={value}>{label}</MenuItem>)}
      </TextField>
      <TextField select size="small" label="Reports from" value={windowDays} onChange={(event) => onWindowChange(Number(event.target.value))} sx={{ minWidth: '16ch' }}>
        {[7, 30, 90].map((days) => <MenuItem key={days} value={days}>{days} days</MenuItem>)}
      </TextField>
    </ListToolbar>}
    {/* On its own line, not in the filter row: the picker brings its own loading, error and "no project" states. */}
    {tracesOn && data.results.length > 0 && <Suspense fallback={null}><AuthorizedProjectPicker label="Traces project" value={traceProject} onChange={setTraceProject} size="small" sx={{ maxWidth: 320 }} /></Suspense>}
    {data.count > data.pageSize && <Typography variant="s3" color="text.secondary">Search and filters apply to the apps on this page.</Typography>}
    {!data.results.length ? <EmptyState variant="first-run" title="No apps connected yet" description={emptyDescription} action={primaryRegistration(registrationAction)} />
      : !rows.length ? <EmptyState variant="no-results" title="No apps match these filters" onClearFilters={clear} />
        : <TableContainer sx={{ maxWidth: 1 }}><Table size="small" aria-label="Context delivery observations">
          <TableHead sx={{ display: { xs: 'none', sm: 'table-header-group' } }}><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>{columns.map((name) => <TableCell key={name}>{name}</TableCell>)}<TableCell><Box component="span" sx={visuallyHidden}>Actions</Box></TableCell></TableRow></TableHead>
          <TableBody>{rows.map((row) => {
            const kind = row.binding.kind === 'runtime' ? 'App' : row.binding.kind === 'repository' ? 'Repository' : 'Unknown';
            const pinned = row.binding.selector.version ? `Pinned v${row.binding.selector.version}` : `Follow ${row.binding.selector.label}`;
            const number = (stage) => (stage && versionNumbers?.[stage.versionId] !== undefined ? `v${versionNumbers[stage.versionId]}` : null);
            // At 390 the kind, target, delivery and pin collapse into one line under the name.
            const facts = [kind, number(row.desired) && `Target ${number(row.desired)}`, number(row.loaded) && `Delivered ${number(row.loaded)}`, pinned].filter(Boolean).join(' · ');
            // UXF-12: the row opens Inspect; Inspect and every other action sit in the trailing ⋯.
            const primary = { label: 'Inspect', onClick: () => setSelectedId(row.binding.id) };
            const { sx: linkSx, ...linkProps } = rowLinkProps(primary);
            return <TableRow key={row.binding.id} hover {...linkProps} sx={[(theme) => ({
              height: { xs: 'auto', sm: ROW_HEIGHT.default },
              [theme.breakpoints.down('sm')]: {
                display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) auto', columnGap: 1, rowGap: 0.5, alignItems: 'start', px: 0, py: 1.5, borderBottom: `1px solid ${theme.palette.divider}`,
                '& > td': { p: 0, border: 0, minWidth: 0 },
              },
            }), ...(linkSx ? [linkSx] : [])]}>
              <TableCell sx={{ gridColumn: { xs: 1, sm: 'auto' }, gridRow: { xs: 1, sm: 'auto' } }}><Button color="inherit" sx={{ px: 0, minWidth: 0, justifyContent: 'flex-start', textAlign: 'left', overflowWrap: 'anywhere', whiteSpace: 'normal', maxWidth: 1 }} onClick={() => setSelectedId(row.binding.id)}>{row.binding.name}</Button><Typography component="div" variant="s3" sx={{ display: { xs: 'none', sm: 'block' } }}>{pinned}</Typography><Typography component="div" variant="s3" color="text.secondary" sx={{ display: { xs: 'block', sm: 'none' } }}>{facts}</Typography></TableCell>
              <TableCell sx={{ display: { xs: 'none', sm: 'table-cell' } }}><Typography variant="s1">{kind}</Typography></TableCell>
              <TableCell sx={{ display: { xs: 'none', sm: 'table-cell' } }}>{row.desired ? <Stage stage={row.desired} versionNumbers={versionNumbers} /> : <Typography variant="s3" color="text.secondary">—</Typography>}</TableCell>
              {anyDelivered && <TableCell sx={{ display: { xs: 'none', sm: 'table-cell' } }}>{row.loaded ? <Stage stage={row.loaded} versionNumbers={versionNumbers} /> : <Typography variant="s3" color="text.secondary">—</Typography>}</TableCell>}
              <TableCell sx={{ gridColumn: { xs: '1 / -1', sm: 'auto' }, gridRow: { xs: 2, sm: 'auto' }, ...(unreported && { display: { xs: 'none', sm: 'table-cell' } }) }}><Stack spacing={0.5} alignItems="flex-start">{unreported ? <Typography variant="s3" color="text.secondary">—</Typography> : row.states.map((state) => <StatusBadge key={state} status={state} role={state === 'unknown' ? 'neutral' : undefined} label={consumerStateNames[state] ?? 'Unknown'} />)}</Stack></TableCell>
              {anyReport && <TableCell sx={{ display: { xs: 'none', sm: 'table-cell' } }}><Typography variant="s3"><When value={consumerReportAt(row, data.asOf)} fallback="—" /></Typography></TableCell>}
              <RowActionsCell name={row.binding.name} primary={primary} actions={traceAction(row.binding)} sx={{ gridColumn: { xs: 2, sm: 'auto' }, gridRow: { xs: 1, sm: 'auto' } }} />
            </TableRow>;
          })}</TableBody>
        </Table></TableContainer>}
    {data.count > 0 && <DataTablePagination page={data.page - 1} pageSize={data.pageSize} total={data.count} onPageChange={(page) => onPageChange(page + 1)} pageSizeOptions={[data.pageSize]} />}
    <Drawer anchor="right" open={Boolean(selected)} onClose={() => setSelectedId(null)} PaperProps={{ role: 'dialog', 'aria-modal': true, 'aria-labelledby': 'consumer-receipts-title', sx: { width: { xs: 1, sm: 560 }, maxWidth: 1 } }}>
      {selected && <Box sx={{ p: 2, minWidth: 0 }}>
        <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ position: 'sticky', top: 0, bgcolor: 'background.paper', py: 1, zIndex: 1 }}>
          <Box><Typography component="h2" id="consumer-receipts-title" variant="m1">{selected.binding.name}</Typography><Typography variant="s3" color="text.secondary">Delivery receipts</Typography></Box>
          <IconButton aria-label="Close delivery receipts" onClick={() => setSelectedId(null)}><Iconify icon="solar:close-linear" width={18} /></IconButton>
        </Stack>
        <Stack spacing={2}>
          <Alert severity="info"><Typography variant="s3" component="div">Next step</Typography><Typography variant="s1" component="div">{consumerNextStep(selected.nextActionCode)}</Typography></Alert>
          {/* At 390 px a row is one tappable card with no ⋯ menu, so the traces link is also reachable from here. */}
          {traceLinkOf(selected.binding) && <Button component={RouterLink} to={traceLinkOf(selected.binding)} variant="text" color="inherit" size="small" sx={{ alignSelf: 'flex-start' }}>{RUNTIME_TRACES}</Button>}
          <Stack component="ol" spacing={0} sx={{ listStyle: 'none', m: 0, p: 0, border: 1, borderColor: 'divider', borderRadius: 1, overflow: 'hidden' }}>
            {Object.entries(stageNames).map(([key, label]) => <Box component="li" key={key} sx={{ display: 'grid', gridTemplateColumns: '11ch minmax(0, 1fr)', gap: 1, p: 1.5, borderBottom: 1, borderColor: 'divider', '&:last-child': { borderBottom: 0 } }}>
              <Typography variant="m3">{label}</Typography><Stack spacing={0.5} alignItems="flex-start"><Stage stage={selected[key]} versionNumbers={versionNumbers} />{selected[key] && <Typography variant="s3" color="text.secondary"><When value={selected[key].observedAt} /></Typography>}</Stack>
            </Box>)}
          </Stack>
          <TechnicalDetails><Stack spacing={1}><IdentityChip label="App ID" kind="id" value={selected.binding.id} /><Typography component="code" variant="s3">{selected.nextActionCode ?? 'Unknown'}</Typography>
            {Object.entries(stageNames).filter(([key]) => selected[key]).map(([key, label]) => <Box key={key}><Typography variant="m3">{label}</Typography><IdentityChip label="Release ID" kind="id" value={selected[key].versionId} />{selected[key].sessionId && <IdentityChip label="Session" kind="id" value={selected[key].sessionId} />}<IdentityChip label="Digest" value={selected[key].digest} /><Typography variant="s3">{selected[key].origin || 'Provenance not reported'}</Typography><Typography variant="s3" component="div">{selected[key].observedAt}</Typography></Box>)}
          </Stack></TechnicalDetails>
          <Typography variant="s3" color="text.secondary">Delivered means the release was sent to this runtime. It doesn’t show whether it was used.</Typography>
        </Stack>
      </Box>}
    </Drawer>
  </Stack>;
}
ConsumerRows.propTypes = { data: PropTypes.object.isRequired, windowDays: PropTypes.number.isRequired, onWindowChange: PropTypes.func.isRequired, onPageChange: PropTypes.func.isRequired, versionNumbers: PropTypes.object, previewState: PropTypes.string, registrationAction: PropTypes.node };
export default function ContextConsumersView({ scopeKey, state: resolvedState = 'unavailable', data, windowDays = 7, onWindowChange, onPageChange, onRetry, versionNumbers, registrationAction }) {
  const preview = new URLSearchParams(window.location.search).get('state');
  const state = resolvedState === 'ready' && ['loading', 'error', 'empty', 'filtered-empty', 'off', 'denied'].includes(preview) ? preview : resolvedState;
  return <Section title={CONNECTED_APPS}>
    {!scopeKey || ['denied', 'forbidden'].includes(state) ? <ErrorState title="You can't view these apps" />
      : state === 'loading' ? <ContentSkeleton sx={{ '& .MuiSkeleton-root': { bgcolor: 'action.selected' } }} variant="rows" />
        : state === 'error' ? <ErrorState detail="Delivery status is unknown until this loads." title="Couldn't load apps" onRetry={onRetry} />
          : state === 'off' ? <CapabilityNotice title="App reports are off" description="Delivery can only be verified when a registered runtime reports it." />
            : state === 'empty' ? <EmptyState variant="first-run" title="No apps connected yet" description={emptyDescription} action={primaryRegistration(registrationAction)} />
              : ['ready', 'filtered-empty'].includes(state) && data ? <ConsumerRows key={scopeKey} {...{ data, windowDays, onWindowChange, onPageChange, versionNumbers, registrationAction }} previewState={state} />
                : <ReviewState state={state} onRetry={onRetry} />}
  </Section>;
}
ContextConsumersView.propTypes = { scopeKey: PropTypes.string, state: PropTypes.string, data: PropTypes.object, windowDays: PropTypes.number, onWindowChange: PropTypes.func, onPageChange: PropTypes.func, onRetry: PropTypes.func, versionNumbers: PropTypes.object, registrationAction: PropTypes.node };
