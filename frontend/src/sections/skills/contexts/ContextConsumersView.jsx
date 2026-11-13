import { useState } from 'react';
import PropTypes from 'prop-types';
import { addIcon } from '@iconify/react';
import { Box, Button, Drawer, IconButton, MenuItem, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, TextField, Typography } from '@mui/material';
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
import { consumerStateNames, consumerNextStep } from './consumerPresentation';

// Bundle the Solar close glyph: dismissal must render before an icon CDN responds.
addIcon('solar:close-linear', { width: 24, height: 24, body: '<path fill="none" stroke="currentColor" stroke-linecap="round" stroke-width="1.5" d="M18 6L6 18M6 6l12 12"/>' });

const mobileLabel = (label) => ({ '&::before': { content: { xs: `"${label}"`, sm: 'none' }, display: 'block', typography: 's3', color: 'text.secondary', mb: 0.5 } });
const stageNames = { desired: 'Desired', resolved: 'Resolved', installed: 'Installed', discovered: 'Discovered', loaded: 'Delivered' };
function Stage({ stage, versionNumbers = {}, details = false }) {
  if (!stage) return <Typography variant="s3">Not observed</Typography>;
  return <Stack spacing={0.5}>
    {versionNumbers[stage.versionId] !== undefined ? <Typography variant="s1">v{versionNumbers[stage.versionId]}</Typography> : <IdentityChip label="Release" kind="id" value={stage.versionId} />}
    {details && <>
      <IdentityChip label="Release ID" kind="id" value={stage.versionId} />
      {stage.sessionId && <IdentityChip label="Session" kind="id" value={stage.sessionId} />}
      <IdentityChip label="Digest" value={stage.digest} />
      <Typography variant="s3">{stage.observedAt ?? 'Time unknown'} · {stage.origin ?? 'Provenance unknown'}</Typography>
    </>}
  </Stack>;
}
Stage.propTypes = { stage: PropTypes.object, versionNumbers: PropTypes.object, details: PropTypes.bool };
function ConsumerRows({ data, windowDays, onWindowChange, onPageChange, versionNumbers, previewState }) {
  const [selectedId, setSelectedId] = useState(null);
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('all');
  const selected = data.results.find((item) => item.binding.id === selectedId);
  const rows = previewState === 'filtered-empty' ? [] : data.results.filter((row) => row.binding.name.toLowerCase().includes(search.trim().toLowerCase()) && (status === 'all' || row.states.includes(status)));
  const clear = () => { setSearch(''); setStatus('all'); };
  return <Stack spacing={1.5} sx={{ minWidth: 0 }}>
    <Typography variant="s3" color="text.secondary">As of {data.asOf}. Only declared consumers are shown. Installation and fetching do not prove delivery.</Typography>
    {data.results.some((row) => row.observationsComplete === false) && <Typography variant="s3">Some observations are missing.</Typography>}
    <ListToolbar sx={{ flexWrap: 'wrap', gap: 1 }}>
      <FormSearchField label="Search consumers on this page" searchQuery={search} onChange={(event) => setSearch(event.target.value)} sx={{ minWidth: 0 }} />
      <TextField select size="small" label="Verification" value={status} onChange={(event) => setStatus(event.target.value)}>
        <MenuItem value="all">All statuses</MenuItem>
        {Object.entries(consumerStateNames).map(([value, label]) => <MenuItem key={value} value={value}>{label}</MenuItem>)}
      </TextField>
      <TextField select size="small" label="Observation window" value={windowDays} onChange={(event) => onWindowChange(Number(event.target.value))} sx={{ minWidth: '16ch' }}>
        {[7, 30, 90].map((days) => <MenuItem key={days} value={days}>{days} days</MenuItem>)}
      </TextField>
    </ListToolbar>
    {!data.results.length ? <EmptyState variant="first-run" title="No declared consumers" description="Register a supported repository or runtime explicitly. Fetch activity is not a consumer inventory." />
      : !rows.length ? <EmptyState variant="no-results" title="No consumers match these filters" onClearFilters={clear} />
        : <TableContainer sx={{ maxWidth: 1 }}><Table size="small" aria-label="Context delivery observations">
          <TableHead sx={{ display: { xs: 'none', sm: 'table-header-group' } }}><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>{['Consumer', 'Target release', 'Last delivered', 'Verification', 'Last report', 'Inspect'].map((name) => <TableCell key={name}>{name}</TableCell>)}</TableRow></TableHead>
          <TableBody>{rows.map((row) => <TableRow key={row.binding.id} hover sx={{ height: { xs: 'auto', sm: ROW_HEIGHT.default }, display: { xs: 'grid', sm: 'table-row' }, gridTemplateColumns: '1fr 1fr auto', py: { xs: 1, sm: 0 }, '& > td': { borderBottom: { xs: 0, sm: undefined } } }}>
            <TableCell sx={{ gridColumn: '1 / 3' }}><Button color="inherit" onClick={() => setSelectedId(row.binding.id)}>{row.binding.name}</Button><Typography component="div" variant="s3">{row.binding.selector.version ? `Pinned v${row.binding.selector.version}` : `Follow ${row.binding.selector.label}`}</Typography></TableCell>
            <TableCell sx={{ ...mobileLabel('Target release'), gridRow: 2 }}><Stage stage={row.desired} versionNumbers={versionNumbers} /></TableCell>
            <TableCell sx={{ ...mobileLabel('Last delivered'), gridRow: 2 }}><Stage stage={row.loaded} versionNumbers={versionNumbers} /></TableCell>
            <TableCell sx={{ gridColumn: '1 / 4', gridRow: 3 }}><Stack spacing={0.5}>{row.states.map((state) => <StatusBadge key={state} status={state} label={consumerStateNames[state] ?? 'Unknown'} />)}</Stack></TableCell>
            <TableCell sx={{ display: { xs: 'none', sm: 'table-cell' } }}><Typography variant="s3">{row.observationAgeSeconds == null ? 'Time unknown' : `${row.observationAgeSeconds}s ago`}</Typography></TableCell>
            <TableCell sx={{ gridColumn: 3, gridRow: 1 }}><Button size="small" aria-label={`Inspect ${row.binding.name}`} onClick={() => setSelectedId(row.binding.id)}>Inspect</Button></TableCell>
          </TableRow>)}</TableBody>
        </Table></TableContainer>}
    {data.count > 0 && <DataTablePagination page={data.page - 1} pageSize={data.pageSize} total={data.count} onPageChange={(page) => onPageChange(page + 1)} pageSizeOptions={[data.pageSize]} />}
    <Drawer anchor="right" open={Boolean(selected)} onClose={() => setSelectedId(null)} PaperProps={{ role: 'dialog', 'aria-modal': true, 'aria-labelledby': 'consumer-receipts-title', sx: { width: { xs: 1, sm: 560 }, maxWidth: 1 } }}>
      {selected && <Box sx={{ p: 2, minWidth: 0 }}>
        <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ position: 'sticky', top: 0, bgcolor: 'background.paper', py: 1, zIndex: 1 }}>
          <Typography component="h2" id="consumer-receipts-title" variant="m1">Delivery receipts</Typography>
          <IconButton aria-label="Close delivery receipts" onClick={() => setSelectedId(null)}><Iconify icon="solar:close-linear" width={18} /></IconButton>
        </Stack>
        <Stack spacing={2}>
          <Typography variant="m3">{selected.binding.name}</Typography>
          <Section title="Next step"><Typography variant="s1">{consumerNextStep(selected.nextActionCode)}</Typography></Section>
          {Object.entries(stageNames).map(([key, label]) => <Section key={key} title={label}><Stage stage={selected[key]} versionNumbers={versionNumbers} details /></Section>)}
          <TechnicalDetails><IdentityChip label="Consumer ID" kind="id" value={selected.binding.id} /><Typography component="code" variant="s3">{selected.nextActionCode ?? 'Unknown'}</Typography></TechnicalDetails>
          <Typography variant="s3">Delivery is supplied content, not proven compliance or a successful business action. Old threads remain pinned; a new install does not erase older sessions. Version numbers do not establish freshness.</Typography>
        </Stack>
      </Box>}
    </Drawer>
  </Stack>;
}
ConsumerRows.propTypes = { data: PropTypes.object.isRequired, windowDays: PropTypes.number.isRequired, onWindowChange: PropTypes.func.isRequired, onPageChange: PropTypes.func.isRequired, versionNumbers: PropTypes.object, previewState: PropTypes.string };
export default function ContextConsumersView({ scopeKey, state: resolvedState = 'unavailable', data, windowDays = 7, onWindowChange, onPageChange, onRetry, versionNumbers }) {
  const preview = new URLSearchParams(window.location.search).get('state');
  const state = resolvedState === 'ready' && ['loading', 'error', 'empty', 'filtered-empty', 'off', 'denied'].includes(preview) ? preview : resolvedState;
  return <Section title="Consumers">
    {!scopeKey || ['denied', 'forbidden'].includes(state) ? <ErrorState title="You cannot view these consumers" />
      : state === 'loading' ? <ContentSkeleton sx={{ '& .MuiSkeleton-root': { bgcolor: 'action.selected' } }} variant="rows" />
        : state === 'error' ? <ErrorState title="Couldn't load consumers" onRetry={onRetry} />
          : state === 'off' ? <CapabilityNotice title="Consumer observations are off" description="Delivery can only be verified when a registered runtime reports it." />
            : state === 'empty' ? <EmptyState variant="first-run" title="No declared consumers" />
              : ['ready', 'filtered-empty'].includes(state) && data ? <ConsumerRows key={scopeKey} {...{ data, windowDays, onWindowChange, onPageChange, versionNumbers }} previewState={state} />
                : <ReviewState state={state} onRetry={onRetry} />}
  </Section>;
}
ContextConsumersView.propTypes = { scopeKey: PropTypes.string, state: PropTypes.string, data: PropTypes.object, windowDays: PropTypes.number, onWindowChange: PropTypes.func, onPageChange: PropTypes.func, onRetry: PropTypes.func, versionNumbers: PropTypes.object };
