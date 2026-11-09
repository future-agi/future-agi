import { createContext, useContext, useState } from "react";
import PropTypes from "prop-types";
import { Alert, Button, MenuItem, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TablePagination, TableRow, TextField, Typography } from "@mui/material";
import PageHeader from "src/components/page-header";
import ListToolbar from "src/components/list-toolbar";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import EmptyState from "src/components/empty-state";
import ErrorState from "src/components/error-state";
import ContentSkeleton from "src/components/content-skeleton";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import ContextImportDialog from "./ContextImportDialog";

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

function Library({ scopeKey, query, filters = {}, onFilterChange, onOpen, onSkills, onArchived, importActions, state }) {
  const [importOpen, setImportOpen] = useState(false);
  const scoped = query.scopeKey === scopeKey;
  const data = scoped ? query.data : null;
  const rows = data?.results || [];
  const loading = state === "loading" || query.isLoading || !scoped;
  const error = state === "error" || query.error;
  const empty = state === "empty" || !rows.length;
  const change = (key, value) => onFilterChange?.({ ...filters, [key]: value, page: 1 });
  return <Stack spacing={1.5} sx={{ minWidth: 0 }}>
    <PageHeader title="Agent contexts" description="Standing instructions and linked skills, released together." sx={{ mb: 0 }} />
    <Stack direction="row" spacing={1} sx={{ flexWrap: "wrap" }}>
      <Button variant="outlined" onClick={onSkills} disabled={!onSkills}>Skills</Button>
      <Button variant="outlined" onClick={onArchived} disabled={!onArchived}>Archived identities</Button>
      {!empty && <Button variant="contained" disabled={!importActions} onClick={() => setImportOpen(true)}>New agent context</Button>}
    </Stack>
    <ListToolbar>
      <FormSearchField label="Search agent contexts" searchQuery={filters.search || ""} onChange={(event) => change("search", event.target.value)} sx={{ minWidth: 0, flex: 1 }} />
      <TextField size="small" label="Tag" value={filters.tag || ""} onChange={(event) => change("tag", event.target.value)} />
      <TextField select size="small" label="Label" value={filters.label || ""} onChange={(event) => change("label", event.target.value)}><MenuItem value="">Any label</MenuItem><MenuItem value="production">Production</MenuItem><MenuItem value="staging">Staging</MenuItem></TextField>
      <TextField select size="small" label="Sort" value={filters.sort || "updated"} onChange={(event) => change("sort", event.target.value)}><MenuItem value="updated">Updated</MenuItem><MenuItem value="name">Name</MenuItem><MenuItem value="created">Created</MenuItem></TextField>
    </ListToolbar>
    {loading ? <ContentSkeleton /> : error ? <ErrorState title="Agent contexts unavailable" detail="An access or network error is not an empty library." onRetry={query.refetch} /> : empty ? <EmptyState variant={filters.search || filters.tag || filters.label ? "no-results" : "first-run"} title="No agent contexts" description="Standing instructions and linked skills, released together." action={<Button variant="contained" disabled={!importActions} onClick={() => setImportOpen(true)}>{importActions?.onCreate && !importActions.onPreview ? 'Write AGENTS.md' : 'Import AGENTS.md'}</Button>} /> : <>
      <TableContainer><Table size="small" aria-label="Agent contexts"><TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>{["Context and purpose", "Production", "Latest", "Draft", "Linked skills", "Updates", "Delivery"].map((label) => <TableCell key={label}>{label}</TableCell>)}</TableRow></TableHead><TableBody>
        {rows.map((context) => <TableRow hover key={context.id} sx={{ height: ROW_HEIGHT.default }}>
          <TableCell><Button onClick={() => onOpen?.(context)} disabled={!onOpen} sx={{ justifyContent: "flex-start", overflowWrap: "anywhere", textAlign: "left" }}>{context.name}</Button><Typography variant="s3" display="block">{context.description}</Typography></TableCell>
          <TableCell>{context.labels?.find((label) => label.name === "production")?.version ? `v${context.labels.find((label) => label.name === "production").version}` : "Not assigned"}</TableCell>
          <TableCell>{context.latestVersion == null ? "Not published" : `v${context.latestVersion}`}</TableCell>
          <TableCell>{context.hasDraft ? "Your draft" : "—"}</TableCell><TableCell>{context.linkCount ?? "Not loaded"}</TableCell><TableCell>{context.updateCount ?? "Not checked"}</TableCell><TableCell>{context.deliverySummary || "Not observed"}</TableCell>
        </TableRow>)}
      </TableBody></Table></TableContainer>
      <TablePagination component="div" count={data.count} page={data.page - 1} rowsPerPage={data.pageSize} rowsPerPageOptions={[]} onPageChange={(_, page) => onFilterChange?.({ ...filters, page: page + 1 })} />
    </>}
    {!importActions && <Alert severity="info">Context authoring is unavailable until its service and scoped client are enabled.</Alert>}
    {importOpen && importActions && <ContextImportDialog {...importActions} scopeKey={scopeKey} open onClose={() => setImportOpen(false)} onCreated={(result) => { setImportOpen(false); importActions.onCreated?.(result); }} />}
  </Stack>;
}
Library.propTypes = { scopeKey: PropTypes.string.isRequired, query: PropTypes.object.isRequired, filters: PropTypes.object, onFilterChange: PropTypes.func, onOpen: PropTypes.func, onSkills: PropTypes.func, onArchived: PropTypes.func, importActions: PropTypes.object, state: PropTypes.string };
export default function ContextLibraryView(props) { return <Library key={props.scopeKey} {...props} />; }
ContextLibraryView.propTypes = Library.propTypes;
