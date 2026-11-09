import { useState } from "react";
import PropTypes from "prop-types";
import { Button, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TablePagination, TableRow, Typography } from "@mui/material";
import PageHeader from "src/components/page-header";
import EmptyState from "src/components/empty-state";
import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import { AuthoringError, useAuthoringOperation } from "../editor/HotfixHeadReview";
import ContextSafetyDialog from "../contexts/ContextSafetyDialog";

// Intent: administrator recovers the original archived UUID, never a namesake.
// Hierarchy: identity/history list followed by exact retained-label review.
// Palette/depth/surfaces: neutral flat table and shared dialog; no health badges.
// Type/spacing: m3/s1/s3, shared table sizing and 12px groups.
function Archived({ scopeKey, query, onPageChange, onInspect, onReview, onUnarchive, onRecovered, canRecover = false }) {
  const [selection, setSelection] = useState(null);
  const operation = useAuthoringOperation(scopeKey);
  const scoped = query.scopeKey === scopeKey;
  const rows = scoped ? query.data?.results || [] : [];
  const review = async (resource) => {
    const result = await operation.run(onReview, { resourceId: resource.id }, { retainUnknown: false });
    if (result) setSelection({ resource, review: result });
  };
  return <Stack spacing={1.5} sx={{ minWidth: 0 }}>
    <PageHeader title="Archived skills" description="Inspect and recover original identities, versions and retained label policies." />
    <AuthoringError error={operation.error} />
    {!scoped || query.isLoading ? <ContentSkeleton /> : query.error ? <ErrorState title="Archived skills unavailable" detail="Access is checked before listing or counting archived identities." onRetry={query.refetch} /> : !rows.length ? <EmptyState title="No archived skills" description="No authorized archived identities were returned." /> : <>
      <TableContainer><Table size="small" aria-label="Archived skill identities"><TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}><TableCell>Original identity</TableCell><TableCell>Retained labels</TableCell><TableCell>Actions</TableCell></TableRow></TableHead>
        <TableBody>{rows.map((skill) => <TableRow hover key={skill.id} sx={{ height: ROW_HEIGHT.default }}>
          <TableCell><Typography variant="s1" display="block">{skill.name}</Typography><Typography variant="s3" display="block" sx={{ overflowWrap: "anywhere" }}>{skill.id}</Typography></TableCell>
          <TableCell>{(skill.labels || []).map((label) => <Typography key={label.name} variant="s3" display="block">{label.name} → v{label.version}</Typography>)}</TableCell>
          <TableCell><Stack direction={{ xs: "column", sm: "row" }}><Button disabled={!onInspect || operation.pending} onClick={() => onInspect(skill)}>Inspect history</Button><Button disabled={!canRecover || !onReview || !onUnarchive || operation.pending} onClick={() => review(skill)}>Review recovery</Button></Stack></TableCell>
        </TableRow>)}</TableBody></Table></TableContainer>
      <TablePagination component="div" count={query.data.count} page={query.data.page - 1} rowsPerPage={query.data.pageSize} rowsPerPageOptions={[]} onPageChange={(_, page) => onPageChange?.(page + 1)} />
    </>}
    {scoped && !query.error && selection && rows.some((row) => row.id === selection.resource.id) && <ContextSafetyDialog open scopeKey={scopeKey} action="unarchive" resource={selection.resource} review={selection.review} canAct={canRecover} onClose={() => setSelection(null)} onApply={(input, options) => onUnarchive(selection.resource.id, input, options)} onApplied={(result) => { setSelection(null); onRecovered?.(result); }} />}
  </Stack>;
}
Archived.propTypes = { scopeKey: PropTypes.string.isRequired, query: PropTypes.object.isRequired, onPageChange: PropTypes.func, onInspect: PropTypes.func, onReview: PropTypes.func, onUnarchive: PropTypes.func, onRecovered: PropTypes.func, canRecover: PropTypes.bool };
export default function ArchivedSkillsView(props) { return <Archived key={props.scopeKey} {...props} />; }
ArchivedSkillsView.propTypes = Archived.propTypes;
