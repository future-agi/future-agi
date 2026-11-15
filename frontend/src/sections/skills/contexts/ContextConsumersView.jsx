import { cloneElement, isValidElement, useState } from 'react';
import PropTypes from 'prop-types';
import { Alert, Box, Button, Drawer, IconButton, Link, MenuItem, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, TextField, Tooltip, Typography } from '@mui/material';
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
import { formatUtcFull, formatWhen } from '../utils/format';
import { consumerStateNames, consumerNextStep, needsDeliveryVerification, consumerReportAt, midSentence, noAppHasReported, NO_REPORT_TAG, NO_REPORT_SUMMARY } from './consumerPresentation';

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
  // A column with nothing in it for every consumer says nothing, so it appears once any consumer has a value.
  const anyDelivered = data.results.some((row) => row.loaded);
  const anyReport = data.results.some((row) => consumerReportAt(row, data.asOf));
  const unreported = noAppHasReported(data.results, data.asOf);
  const columns = ['App or repository', 'Kind', 'Target release', ...(anyDelivered ? ['Last delivered'] : []), 'Verification', ...(anyReport ? ['Last report'] : []), 'Inspect'];
  return <Stack spacing={1.5} sx={{ minWidth: 0 }}>
    <Typography variant="s1" color="text.secondary">{missing > 0 ? `${missing} of ${data.results.length} connected ${data.results.length === 1 ? 'consumer' : 'consumers'} ${missing === 1 ? 'needs' : 'need'} a delivery report to confirm the version received.` : 'Connected consumers report the version they receive; delivery does not prove use.'} <Link href="https://docs.futureagi.com/docs/skills/rollout-recovery" target="_blank" rel="noopener noreferrer">How apps report</Link></Typography>
    <Typography variant="s3" color="text.secondary">As of <AsOf value={data.asOf} /></Typography>
    {unreported && <Typography variant="s3" color="text.secondary">{NO_REPORT_SUMMARY}</Typography>}
    {data.results.length > 0 && registrationAction}
    {data.results.length > 0 && <ListToolbar sx={{ flexWrap: 'wrap', gap: 1 }}>
      <FormSearchField label="Search consumers" placeholder="Search consumers" searchQuery={search} onChange={(event) => setSearch(event.target.value)} sx={{ minWidth: 0 }} />
      <TextField select size="small" label="Verification" value={status} onChange={(event) => setStatus(event.target.value)}>
        <MenuItem value="all">All statuses</MenuItem>
        {Object.entries(consumerStateNames).map(([value, label]) => <MenuItem key={value} value={value}>{label}</MenuItem>)}
      </TextField>
      <TextField select size="small" label="Observation window" value={windowDays} onChange={(event) => onWindowChange(Number(event.target.value))} sx={{ minWidth: '16ch' }}>
        {[7, 30, 90].map((days) => <MenuItem key={days} value={days}>{days} days</MenuItem>)}
      </TextField>
    </ListToolbar>}
    {data.count > data.pageSize && <Typography variant="s3" color="text.secondary">Search and filters apply to the consumers on this page.</Typography>}
    {!data.results.length ? <EmptyState variant="first-run" title="No apps connected yet" description={emptyDescription} action={primaryRegistration(registrationAction)} />
      : !rows.length ? <EmptyState variant="no-results" title="No consumers match these filters" onClearFilters={clear} />
        : <TableContainer sx={{ maxWidth: 1 }}><Table size="small" aria-label="Context delivery observations">
          <TableHead sx={{ display: { xs: 'none', sm: 'table-header-group' } }}><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>{columns.map((name) => <TableCell key={name}>{name}</TableCell>)}</TableRow></TableHead>
          <TableBody>{rows.map((row) => {
            const kind = row.binding.kind === 'runtime' ? 'App' : row.binding.kind === 'repository' ? 'Repository' : 'Unknown';
            const pinned = row.binding.selector.version ? `Pinned v${row.binding.selector.version}` : `Follow ${row.binding.selector.label}`;
            const number = (stage) => (stage && versionNumbers?.[stage.versionId] !== undefined ? `v${versionNumbers[stage.versionId]}` : null);
            // At 390 the kind, target, delivery and pin collapse into one line under the name.
            const facts = [kind, number(row.desired) && `Target ${number(row.desired)}`, number(row.loaded) && `Delivered ${number(row.loaded)}`, pinned].filter(Boolean).join(' · ');
            return <TableRow key={row.binding.id} hover sx={(theme) => ({
              height: { xs: 'auto', sm: ROW_HEIGHT.default },
              [theme.breakpoints.down('sm')]: {
                display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) auto', columnGap: 1, rowGap: 0.5, alignItems: 'start', px: 0, py: 1.5, borderBottom: `1px solid ${theme.palette.divider}`,
                '& > td': { p: 0, border: 0, minWidth: 0 },
              },
            })}>
              <TableCell sx={{ gridColumn: { xs: 1, sm: 'auto' }, gridRow: { xs: 1, sm: 'auto' } }}><Button color="inherit" sx={{ px: 0, minWidth: 0, justifyContent: 'flex-start', textAlign: 'left', overflowWrap: 'anywhere', whiteSpace: 'normal', maxWidth: 1 }} onClick={() => setSelectedId(row.binding.id)}>{row.binding.name}</Button><Typography component="div" variant="s3" sx={{ display: { xs: 'none', sm: 'block' } }}>{pinned}</Typography><Typography component="div" variant="s3" color="text.secondary" sx={{ display: { xs: 'block', sm: 'none' } }}>{facts}</Typography></TableCell>
              <TableCell sx={{ display: { xs: 'none', sm: 'table-cell' } }}><Typography variant="s1">{kind}</Typography></TableCell>
              <TableCell sx={{ display: { xs: 'none', sm: 'table-cell' } }}>{row.desired ? <Stage stage={row.desired} versionNumbers={versionNumbers} /> : <Typography variant="s3" color="text.secondary">—</Typography>}</TableCell>
              {anyDelivered && <TableCell sx={{ display: { xs: 'none', sm: 'table-cell' } }}>{row.loaded ? <Stage stage={row.loaded} versionNumbers={versionNumbers} /> : <Typography variant="s3" color="text.secondary">—</Typography>}</TableCell>}
              <TableCell sx={{ gridColumn: { xs: '1 / -1', sm: 'auto' }, gridRow: { xs: 2, sm: 'auto' } }}><Stack spacing={0.5} alignItems="flex-start">{row.states.map((state) => <StatusBadge key={state} status={state} role={state === 'unknown' ? 'neutral' : undefined} label={state === 'unknown' && unreported ? NO_REPORT_TAG : consumerStateNames[state] ?? 'Unknown'} />)}</Stack></TableCell>
              {anyReport && <TableCell sx={{ display: { xs: 'none', sm: 'table-cell' } }}><Typography variant="s3"><When value={consumerReportAt(row, data.asOf)} fallback="—" /></Typography></TableCell>}
              <TableCell sx={{ gridColumn: { xs: 2, sm: 'auto' }, gridRow: { xs: 1, sm: 'auto' } }}><Button size="small" variant="outlined" aria-label={`Inspect ${row.binding.name}`} onClick={() => setSelectedId(row.binding.id)}>Inspect</Button></TableCell>
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
          <Stack component="ol" spacing={0} sx={{ listStyle: 'none', m: 0, p: 0, border: 1, borderColor: 'divider', borderRadius: 1, overflow: 'hidden' }}>
            {Object.entries(stageNames).map(([key, label]) => <Box component="li" key={key} sx={{ display: 'grid', gridTemplateColumns: '11ch minmax(0, 1fr)', gap: 1, p: 1.5, borderBottom: 1, borderColor: 'divider', '&:last-child': { borderBottom: 0 } }}>
              <Typography variant="m3">{label}</Typography><Stack spacing={0.5} alignItems="flex-start"><Stage stage={selected[key]} versionNumbers={versionNumbers} />{selected[key] && <Typography variant="s3" color="text.secondary"><When value={selected[key].observedAt} /></Typography>}</Stack>
            </Box>)}
          </Stack>
          <TechnicalDetails><Stack spacing={1}><IdentityChip label="Consumer ID" kind="id" value={selected.binding.id} /><Typography component="code" variant="s3">{selected.nextActionCode ?? 'Unknown'}</Typography>
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
  return <Section title="Connected apps">
    {!scopeKey || ['denied', 'forbidden'].includes(state) ? <ErrorState title="You cannot view these consumers" />
      : state === 'loading' ? <ContentSkeleton sx={{ '& .MuiSkeleton-root': { bgcolor: 'action.selected' } }} variant="rows" />
        : state === 'error' ? <ErrorState detail="Delivery status is unknown until this loads." title="Couldn't load consumers" onRetry={onRetry} />
          : state === 'off' ? <CapabilityNotice title="Consumer observations are off" description="Delivery can only be verified when a registered runtime reports it." />
            : state === 'empty' ? <EmptyState variant="first-run" title="No apps connected yet" description={emptyDescription} action={primaryRegistration(registrationAction)} />
              : ['ready', 'filtered-empty'].includes(state) && data ? <ConsumerRows key={scopeKey} {...{ data, windowDays, onWindowChange, onPageChange, versionNumbers, registrationAction }} previewState={state} />
                : <ReviewState state={state} onRetry={onRetry} />}
  </Section>;
}
ContextConsumersView.propTypes = { scopeKey: PropTypes.string, state: PropTypes.string, data: PropTypes.object, windowDays: PropTypes.number, onWindowChange: PropTypes.func, onPageChange: PropTypes.func, onRetry: PropTypes.func, versionNumbers: PropTypes.object, registrationAction: PropTypes.node };
