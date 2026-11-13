import { createContext, useContext, useState } from "react";
import PropTypes from "prop-types";
import { Button, MenuItem, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TablePagination, TableRow, TextField, Typography } from "@mui/material";
import PageHeader from "src/components/page-header";
import ListToolbar from "src/components/list-toolbar";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import EmptyState from "src/components/empty-state";
import ErrorState from "src/components/error-state";
import ContentSkeleton from "src/components/content-skeleton";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import ContextImportDialog from "./ContextImportDialog";
import CapabilityNotice from '../components/CapabilityNotice';
import StatusBadge from 'src/components/status-badge';

// Intent: locate the exact standing-context release, then create a deliberate draft.
// Hierarchy: purpose and independent production/latest columns, not a quality score.
// Palette/depth/surfaces: existing neutral flat tables; one primary purple action.
// Typography/spacing: m3/s1/s3, shared row heights, 12px toolbar rhythm.

// Composition seam, not an API client or query cache. I supplies L7's scoped hook
// results/callbacks. Absence is explicitly unavailable rather than a legacy-kind fallback.
const AuthoringContext = createContext(null);
export function ContextAuthoringProvider({ scopeKey, bindings, children }) {
  return <AuthoringContext.Provider value={{ scopeKey, bindings }}><Stack key={scopeKey} sx={{ minWidth: 0, flex: 1 }}>{children}</Stack></AuthoringContext.Provider>;
}
ContextAuthoringProvider.propTypes = { scopeKey: PropTypes.string.isRequired, bindings: PropTypes.object.isRequired, children: PropTypes.node };
export const useContextAuthoring = () => useContext(AuthoringContext);

function Library({ scopeKey, query, filters = {}, onFilterChange, onOpen, importActions, state }) {
  // Arriving from a reviewed migration opens creation directly; its published
  // skills are proposed (not accepted) as pinned links in the new draft.
  const proposedLinks = importActions?.proposedLinks;
  const [importOpen, setImportOpen] = useState(Array.isArray(proposedLinks) && proposedLinks.length > 0);
  const scoped = query.scopeKey === scopeKey;
  const data = scoped ? query.data : null;
  const rows = data?.results || [];
  const loading = state === "loading" || query.isLoading || !scoped;
  const error = state === "error" || query.error;
  const empty = ['empty', 'filtered-empty'].includes(state) || !rows.length;
  const filtered = state === 'filtered-empty' || Boolean(filters.search || filters.tag || filters.label);
  const outOfRange = empty && !filtered && (data?.page ?? filters.page ?? 1) > 1;
  const firstRun = empty && !filtered && !outOfRange && !loading && !error;
  const clearFilters = () => onFilterChange?.({ ...filters, search: '', tag: '', label: '', page: 1 });
  const change = (key, value) => onFilterChange?.({ ...filters, [key]: value, page: 1 });
  if (state === 'off') return <Stack spacing={1.5}><PageHeader title="Agent contexts" description="Standing instructions and linked skills, released together." /><CapabilityNotice title="Agent contexts aren't enabled for this workspace." description="Existing skills stay available. Ask a workspace admin about access." /></Stack>;
  return <Stack spacing={1.5} sx={{ minWidth: 0 }}>
    <PageHeader title="Agent contexts" description="Standing instructions and linked skills, released together." sx={{ mb: 0 }} actions={!firstRun && <Button variant="contained" disabled={!importActions} onClick={() => setImportOpen(true)}>New agent context</Button>} />
    {query.isFetching && !loading && <StatusBadge status="checking" label="Updating…" />}
    <ListToolbar>
      <FormSearchField label="Search agent contexts" searchQuery={filters.search || ""} onChange={(event) => change("search", event.target.value)} sx={{ minWidth: 0, flex: 1 }} />
      <TextField size="small" label="Tag" value={filters.tag || ""} onChange={(event) => change("tag", event.target.value)} />
      <TextField select size="small" label="Label" value={filters.label || ""} onChange={(event) => change("label", event.target.value)}><MenuItem value="">Any label</MenuItem><MenuItem value="production">Production</MenuItem><MenuItem value="staging">Staging</MenuItem></TextField>
      <TextField select size="small" label="Sort" value={filters.sort || "updated"} onChange={(event) => change("sort", event.target.value)}><MenuItem value="updated">Updated</MenuItem><MenuItem value="name">Name</MenuItem><MenuItem value="created">Created</MenuItem></TextField>
    </ListToolbar>
    {loading ? <ContentSkeleton variant="rows" /> : state === 'off' ? <CapabilityNotice title="Agent contexts aren't enabled for this workspace." description="Existing skills stay available. Ask a workspace admin about access." /> : state === 'denied' ? <ErrorState title="You don't have access to agent contexts." onRetry={query.refetch} /> : error ? <ErrorState title="Couldn't load agent contexts." onRetry={query.refetch} /> : outOfRange ? <EmptyState title="This page is no longer available" action={<Button onClick={() => onFilterChange?.({ ...filters, page: 1 })}>Go to first page</Button>} /> : empty ? filtered ? <EmptyState variant="no-results" title="No contexts match these filters" onClearFilters={clearFilters} /> : <EmptyState variant="first-run" title="Create your first agent context" description="Combine standing instructions with linked skills, then review and release them together." action={<Button variant="contained" disabled={!importActions} onClick={() => setImportOpen(true)}>Create agent context</Button>} secondaryAction={<Button variant="outlined" disabled={!importActions} onClick={() => setImportOpen(true)}>Import AGENTS.md</Button>} /> : <>
      <TableContainer><Table size="small" aria-label="Agent contexts"><TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>{['Name', 'Production', 'Latest', 'Draft', 'Updated'].map((label) => <TableCell key={label}>{label}</TableCell>)}</TableRow></TableHead><TableBody>
        {rows.map((context) => <TableRow hover key={context.id} sx={{ height: ROW_HEIGHT.default }}>
          <TableCell><Button onClick={() => onOpen?.(context)} disabled={!onOpen} sx={{ justifyContent: "flex-start", overflowWrap: "anywhere", textAlign: "left" }}>{context.name}</Button><Typography variant="s3" display="block">{context.description}</Typography></TableCell>
          <TableCell>{context.labels?.find((label) => label.name === "production")?.version ? `v${context.labels.find((label) => label.name === "production").version}` : "Not assigned"}</TableCell>
          <TableCell>{context.latestVersion == null ? "Not published" : `v${context.latestVersion}`}</TableCell>
          <TableCell>{context.hasDraft ? 'Your draft' : '—'}</TableCell><TableCell>{context.updatedAt ? new Date(context.updatedAt).toLocaleString() : 'Not reported'}</TableCell>
        </TableRow>)}
      </TableBody></Table></TableContainer>
      <TablePagination component="div" count={data.count} page={data.page - 1} rowsPerPage={data.pageSize} rowsPerPageOptions={[]} onPageChange={(_, page) => onFilterChange?.({ ...filters, page: page + 1 })} />
    </>}
    {!importActions && state !== 'off' && <CapabilityNotice title="Agent contexts aren't enabled for this workspace." description="You can still read the contexts you have access to." />}
    {importOpen && importActions && <ContextImportDialog {...importActions} scopeKey={scopeKey} proposedLinkCount={Array.isArray(proposedLinks) ? proposedLinks.length : 0} open onClose={() => setImportOpen(false)} onCreated={(result) => { setImportOpen(false); importActions.onCreated?.(result); }} />}
  </Stack>;
}
Library.propTypes = { scopeKey: PropTypes.string.isRequired, query: PropTypes.object.isRequired, filters: PropTypes.object, onFilterChange: PropTypes.func, onOpen: PropTypes.func, onSkills: PropTypes.func, onArchived: PropTypes.func, importActions: PropTypes.object, state: PropTypes.string };
export default function ContextLibraryView(props) { return <Library key={props.scopeKey} {...props} />; }
ContextLibraryView.propTypes = Library.propTypes;
