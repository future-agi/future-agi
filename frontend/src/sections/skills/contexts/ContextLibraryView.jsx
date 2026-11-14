import { createContext, useContext, useState } from 'react';
import PropTypes from 'prop-types';
import { Button, IconButton, InputAdornment, Link, ListItemIcon, Menu, MenuItem, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TablePagination, TableRow, TextField, Tooltip, Typography } from '@mui/material';
import { relativeTime, fDateTimeLocal } from 'src/utils/format-time';
import PageHeader from 'src/components/page-header';
import ListToolbar from 'src/components/list-toolbar';
import FormSearchField from 'src/components/FormSearchField/FormSearchField';
import EmptyState from 'src/components/empty-state';
import ErrorState from 'src/components/error-state';
import ContentSkeleton from 'src/components/content-skeleton';
import Iconify from 'src/components/iconify';
import StatusBadge from 'src/components/status-badge';
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from 'src/theme/sizes';
import ContextImportDialog from './ContextImportDialog';
import CapabilityNotice from '../components/CapabilityNotice';
import { useSkillsAccess } from '../utils/access';

// Intent: find the context to inspect, without confusing a draft with production.
// Hierarchy: readable names first, independent production/latest/draft facts next.
// Palette/depth/surfaces: neutral flat rows; semantic production badge, one primary.
// Typography/spacing: m3/s1/s3, shared heights, 12px rhythm; stacked phone rows.
const AuthoringContext = createContext(null);
export function ContextAuthoringProvider({ scopeKey, bindings, children }) {
  return <AuthoringContext.Provider value={{ scopeKey, bindings }}><Stack key={scopeKey} sx={{ minWidth: 0, flex: 1 }}>{children}</Stack></AuthoringContext.Provider>;
}
ContextAuthoringProvider.propTypes = { scopeKey: PropTypes.string.isRequired, bindings: PropTypes.object.isRequired, children: PropTypes.node };
export const useContextAuthoring = () => useContext(AuthoringContext);

function Library({ scopeKey, query, filters = {}, onFilterChange, onOpen, importActions, state }) {
  const { canWrite } = useSkillsAccess();
  const proposedLinks = importActions?.proposedLinks;
  const [importOpen, setImportOpen] = useState(Array.isArray(proposedLinks) && proposedLinks.length > 0);
  const [menu, setMenu] = useState(null);
  const scoped = query.scopeKey === scopeKey;
  const data = scoped ? query.data : null;
  const rows = data?.results || [];
  const loading = state === 'loading' || query.isLoading || !scoped;
  const error = state === 'error' || query.error;
  const denied = state === 'denied' || [401, 403].includes(query.error?.response?.status ?? query.error?.statusCode ?? query.error?.status ?? query.error?.httpStatus);
  const empty = ['empty', 'filtered-empty'].includes(state) || !rows.length;
  const filtered = state === 'filtered-empty' || Boolean(filters.search || filters.tag || filters.label);
  const outOfRange = empty && !filtered && (data?.page ?? filters.page ?? 1) > 1;
  const firstRun = empty && !filtered && !outOfRange && !loading && !error && !denied;
  const clearFilters = () => onFilterChange?.({ ...filters, search: '', tag: '', label: '', page: 1 });
  const change = (key, value) => onFilterChange?.({ ...filters, [key]: value, page: 1 });
  const production = (context) => context.labels?.find((label) => label.name === 'production')?.version;
  const productionCount = rows.filter((context) => production(context) != null).length;
  const draftCount = rows.filter((context) => context.hasDraft).length;
  const fullInventory = data?.count === rows.length;
  const createActions = canWrite && <><Button color="primary" variant="contained" disabled={!importActions} onClick={() => setImportOpen(true)}>New agent context</Button><Button variant="outlined" disabled={!importActions} onClick={() => setImportOpen(true)}>Import AGENTS.md</Button></>;
  if (state === 'off') return <Stack spacing={1.5}><PageHeader title="Agent contexts" description="Standing instructions and linked skills, released together." /><CapabilityNotice title="Agent contexts aren't enabled for this workspace." description="Existing skills stay available. Ask a workspace admin about access." /></Stack>;
  return <Stack spacing={1.5} sx={{ minWidth: 0 }}>
    <PageHeader title="Agent contexts" description="Standing instructions and linked skills, released together." sx={{ mb: 0, flexDirection: { xs: 'column', sm: 'row' }, gap: 1.5, '& > .MuiStack-root': { minWidth: 0, maxWidth: 1, ml: { xs: 0 }, flexWrap: 'wrap' } }} actions={!firstRun && !denied && createActions} />
    {query.isFetching && !loading && <StatusBadge status="checking" label="Updating…" />}
    {!firstRun && !denied && <ListToolbar useFlexGap sx={{ flexWrap: 'wrap', '& > *': { maxWidth: '100%' } }}>
      <FormSearchField label="Search agent contexts" searchQuery={filters.search || ''} onChange={(event) => change('search', event.target.value)} sx={{ width: 280, minWidth: 0, flexShrink: 0 }} />
      <TextField size="small" label="Tag" placeholder="Any tag" value={filters.tag || ''} onChange={(event) => change('tag', event.target.value)} sx={{ width: 160 }} InputProps={{ startAdornment: <InputAdornment position="start"><Iconify icon="solar:tag-linear" width={16} /></InputAdornment> }} />
      <TextField select size="small" label="Label" sx={{ width: 160 }} InputLabelProps={{ shrink: true }} SelectProps={{ displayEmpty: true }} value={filters.label || ''} onChange={(event) => change('label', event.target.value)}><MenuItem value="">All labels</MenuItem><MenuItem value="production">Production</MenuItem><MenuItem value="staging">Staging</MenuItem></TextField>
      <TextField select size="small" label="Sort" sx={{ width: 180 }} value={filters.sort || 'updated'} onChange={(event) => change('sort', event.target.value)}><MenuItem value="updated">Recently updated</MenuItem><MenuItem value="name">Name</MenuItem><MenuItem value="created">Recently created</MenuItem></TextField>
    </ListToolbar>}
    {loading ? <ContentSkeleton variant="rows" /> : denied ? <ErrorState title="You don't have access to agent contexts." detail="You don't have access to agent contexts in this workspace. Ask a workspace admin." /> : error ? <ErrorState title="Couldn't load agent contexts." detail="Check your connection and try again." onRetry={query.refetch} /> : outOfRange ? <EmptyState title="This page is no longer available" action={<Button onClick={() => onFilterChange?.({ ...filters, page: 1 })}>Go to first page</Button>} /> : empty ? filtered ? <EmptyState variant="no-results" title="No contexts match these filters" onClearFilters={clearFilters} /> : <EmptyState variant="first-run" icon="solar:folder-with-files-linear" title="Create your first agent context" description="Combine standing instructions with linked skills, then review and release them together." action={canWrite && <Button color="primary" variant="contained" disabled={!importActions} onClick={() => setImportOpen(true)}>Create agent context</Button>} secondaryAction={canWrite && <Button variant="outlined" disabled={!importActions} onClick={() => setImportOpen(true)}>Import AGENTS.md</Button>} /> : <>
      <Typography variant="s3" color="text.secondary">{fullInventory ? `${data.count} ${data.count === 1 ? 'context' : 'contexts'}` : `${rows.length} contexts on this page`} · {productionCount} in production · {draftCount} {draftCount === 1 ? 'draft' : 'drafts'}</Typography>
      <TableContainer><Table size="small" aria-label="Agent contexts" sx={{ '& thead': { display: { xs: 'none', sm: 'table-header-group' } }, '& tbody': { display: { xs: 'block', sm: 'table-row-group' } } }}><TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>{['Name', 'Production', 'Latest', 'Draft', 'Updated', ''].map((label) => <TableCell key={label}>{label}</TableCell>)}</TableRow></TableHead><TableBody>
        {rows.map((context) => <TableRow hover key={context.id} sx={{ height: { sm: ROW_HEIGHT.default }, display: { xs: 'grid', sm: 'table-row' }, gridTemplateColumns: 'minmax(0, 1fr) auto', py: { xs: 1.5, sm: 0 }, borderBottomWidth: { xs: 1, sm: 0 }, borderBottomStyle: 'solid', borderColor: 'divider', '& > td': { display: { xs: 'block', sm: 'table-cell' }, borderBottomWidth: { xs: 0, sm: 1 }, borderBottomStyle: 'solid', borderColor: 'divider', px: { xs: 0, sm: 1.5 }, py: { xs: 0.5 }, minWidth: 0 } }}>
          <TableCell sx={{ gridColumn: '1', gridRow: '1', width: { sm: '40%' } }}><Link component="button" color="text.primary" variant="s1" underline="hover" onClick={() => onOpen?.(context)} disabled={!onOpen} sx={{ fontWeight: 500, overflowWrap: 'anywhere', textAlign: 'left' }}>{context.name}</Link><Typography variant="s3" display="block" color="text.secondary">{context.description}</Typography></TableCell>
          <TableCell sx={{ gridColumn: '1 / -1' }}>{production(context) != null ? <StatusBadge status="released" label={`Production v${production(context)}`} /> : <Typography variant="s3" color="text.secondary">Not in production</Typography>}</TableCell>
          <TableCell sx={{ gridColumn: '1 / -1' }}>{context.latestVersion == null ? 'Not published' : `Latest v${context.latestVersion}`}</TableCell>
          <TableCell sx={{ gridColumn: '1 / -1' }}>{context.hasDraft ? <StatusBadge status="draft" label="Your draft" /> : '—'}</TableCell>
          <TableCell sx={{ gridColumn: '1 / -1', whiteSpace: 'nowrap' }}>{context.updatedAt ? <Tooltip title={fDateTimeLocal(context.updatedAt) || context.updatedAt}><Typography component="span" variant="s3" color="text.secondary">{relativeTime(context.updatedAt)}</Typography></Tooltip> : 'Not reported'}</TableCell>
          <TableCell sx={{ gridColumn: '2', gridRow: '1' }} align="right"><IconButton aria-label={`Actions for ${context.name}`} size="small" onClick={(event) => setMenu({ anchor: event.currentTarget, context })}><Iconify icon="solar:menu-dots-bold" width={16} /></IconButton></TableCell>
        </TableRow>)}
      </TableBody></Table></TableContainer>
      <TablePagination component="div" count={data.count} page={data.page - 1} rowsPerPage={data.pageSize} rowsPerPageOptions={[]} onPageChange={(_, page) => onFilterChange?.({ ...filters, page: page + 1 })} />
    </>}
    <Menu anchorEl={menu?.anchor} open={Boolean(menu)} onClose={() => setMenu(null)}><MenuItem disabled={!onOpen} onClick={() => { onOpen?.(menu.context); setMenu(null); }}><ListItemIcon><Iconify icon="solar:eye-linear" width={16} /></ListItemIcon>Open context</MenuItem></Menu>
    {!importActions && !denied && <CapabilityNotice title="Agent contexts aren't enabled for this workspace." description="You can still read the contexts you have access to." />}
    {importOpen && importActions && canWrite && <ContextImportDialog {...importActions} scopeKey={scopeKey} proposedLinkCount={Array.isArray(proposedLinks) ? proposedLinks.length : 0} open onClose={() => setImportOpen(false)} onCreated={(result) => { setImportOpen(false); importActions.onCreated?.(result); }} />}
  </Stack>;
}
Library.propTypes = { scopeKey: PropTypes.string.isRequired, query: PropTypes.object.isRequired, filters: PropTypes.object, onFilterChange: PropTypes.func, onOpen: PropTypes.func, onSkills: PropTypes.func, onArchived: PropTypes.func, importActions: PropTypes.object, state: PropTypes.string };
export default function ContextLibraryView(props) { return <Library key={props.scopeKey} {...props} />; }
ContextLibraryView.propTypes = Library.propTypes;
