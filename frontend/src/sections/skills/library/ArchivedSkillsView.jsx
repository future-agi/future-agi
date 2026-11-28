import { useState } from "react";
import PropTypes from "prop-types";
import { Alert, Box, Button, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Typography, useMediaQuery } from "@mui/material";
import { useTheme } from "@mui/material/styles";
import { enqueueSnackbar } from "notistack";
import PageHeader from "src/components/page-header";
import EmptyState from "src/components/empty-state";
import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import StatusBadge from "src/components/status-badge";
import { DataTablePagination } from "src/components/data-table";
import { getSkillErrorCode, operationStatus } from "src/api/skills/operation-errors";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import When from "../components/When";
import { stackedTableSx } from "../components/responsiveTable";
import { AuthoringError, useAuthoringOperation } from "../editor/HotfixHeadReview";
import ContextSafetyDialog from "../contexts/ContextSafetyDialog";

// Intent: an admin finds an archived skill and brings it back with its versions, labels and history.
// Hierarchy: the skill and the labels that come back, then one Restore per row; the review is the dialog.
// Palette/depth/surfaces: flat table like the library; the shared safety dialog owns the acknowledgment.
// Type/spacing: m3/s1/s3, shared table sizing, stacked rows below sm.
const labelTitle = (name) => `${name.charAt(0).toUpperCase()}${name.slice(1)}`;
const LABELS_CHANGED = "The labels changed since you opened this. Review again.";

function Archived({ scopeKey, query, page = 1, pageSize = 25, onPageChange, onReview, onRestore, onOpenSkill, onBack, canRestore = false }) {
  const theme = useTheme();
  const phone = useMediaQuery(theme.breakpoints.down("sm"));
  const [selection, setSelection] = useState(null);
  const [changed, setChanged] = useState(false);
  const operation = useAuthoringOperation(scopeKey);
  const scoped = query.scopeKey === undefined || query.scopeKey === scopeKey;
  const rows = scoped ? query.data?.results || [] : [];
  const denied = operationStatus(query.error) === 403;
  // A fresh review for every click, never reused: the labels that come back are decided by what the server says now.
  const review = async (skill) => {
    setChanged(false);
    const result = await operation.run(() => onReview(skill), { resourceId: skill.id }, { retainUnknown: false });
    if (result) setSelection({ skill, review: result });
  };
  // The operation key is the dialog's; it is passed on untouched, so a retry after an unknown outcome repeats one restore.
  const apply = async (input, options) => {
    try {
      return await onRestore(selection.skill, input, options);
    } catch (error) {
      // The reviewed labels are stale: drop the review and its acknowledgment. Restore reviews again from the server.
      if (getSkillErrorCode(error) === "impact_changed") { setSelection(null); setChanged(true); }
      throw error;
    }
  };
  const restored = (skill) => {
    setSelection(null);
    enqueueSnackbar(`${skill.name} restored`, { variant: "success", action: onOpenSkill ? () => <Button color="inherit" size="small" onClick={() => onOpenSkill(skill)}>Open skill</Button> : undefined });
  };
  const back = onBack && <Button variant="outlined" onClick={onBack}>Back to skills</Button>;
  let body;
  if (!scoped || query.isLoading) body = <ContentSkeleton variant="rows" rows={4} label="Loading archived skills" />;
  else if (denied) body = <EmptyState title="Only workspace admins can see archived skills." description="Ask a workspace admin to restore a skill for you." action={back} />;
  else if (query.error) body = <ErrorState title="Couldn't load archived skills." detail="Try loading the list again." onRetry={query.refetch} />;
  else if (!rows.length) body = <EmptyState title="No archived skills" description="Skills you archive appear here. Restoring brings back their versions, labels and history." action={back} />;
  else body = <>
    <TableContainer>
      <Table size="small" aria-label="Archived skills" sx={stackedTableSx}>
        <TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>{["Skill", "Labels", "Latest", "Archived", ""].map((name) => <TableCell key={name || "actions"}>{name}</TableCell>)}</TableRow></TableHead>
        <TableBody>{rows.map((skill) => <TableRow hover key={skill.id} sx={{ height: { sm: ROW_HEIGHT.twoLine } }}>
          <TableCell sx={{ width: { sm: "36%" } }}>
            <Typography variant="s1" display="block" sx={{ fontWeight: 500, overflowWrap: "anywhere" }}>{skill.name}</Typography>
            {skill.description && <Typography variant="s3" color="text.secondary" display="block" sx={{ overflowWrap: "anywhere" }}>{skill.description}</Typography>}
          </TableCell>
          <TableCell>{skill.labels?.length
            ? <Stack direction="row" spacing={0.5} useFlexGap sx={{ flexWrap: "wrap" }}>{skill.labels.map((label) => <StatusBadge key={label.name} status={label.name === "production" ? "released" : "unset"} label={`${labelTitle(label.name)} v${label.version}`} />)}</Stack>
            : "—"}</TableCell>
          <TableCell>{skill.latestVersion ? `${phone ? "Latest " : ""}v${skill.latestVersion}` : "Not published"}</TableCell>
          <TableCell>{phone ? <>Archived <When value={skill.archivedAt} fallback="—" /></> : <When value={skill.archivedAt} fallback="—" />}</TableCell>
          <TableCell data-cell="actions" align="right">
            <Button size="small" aria-label={`Restore ${skill.name}`} disabled={!canRestore || !onReview || !onRestore || operation.pending} onClick={() => review(skill)}>Restore</Button>
          </TableCell>
        </TableRow>)}</TableBody>
      </Table>
    </TableContainer>
    <Box sx={{ "& > .MuiBox-root": { flexWrap: "wrap", columnGap: { xs: 1, sm: 2 }, px: { xs: 0, sm: 2 } } }}>
      <DataTablePagination page={Math.max(page - 1, 0)} pageSize={pageSize} pageSizeOptions={[pageSize]} total={query.data?.count || 0} disabled={Boolean(query.isFetching)} onPageChange={(next) => onPageChange?.(next + 1)} />
    </Box>
  </>;
  return <Stack spacing={1.5} sx={{ minWidth: 0 }}>
    <PageHeader title="Archived skills" description="Restore a skill to bring back its versions, labels and history." />
    <AuthoringError error={operation.error} />
    {changed && <Alert severity="warning">{LABELS_CHANGED}</Alert>}
    {body}
    {scoped && !query.error && selection && rows.some((row) => row.id === selection.skill.id) && <ContextSafetyDialog open scopeKey={scopeKey} action="unarchive" resource={selection.skill} review={selection.review} canAct={canRestore} onClose={() => setSelection(null)} onApply={apply} onApplied={() => restored(selection.skill)} />}
  </Stack>;
}
Archived.propTypes = {
  scopeKey: PropTypes.string.isRequired, query: PropTypes.object.isRequired, page: PropTypes.number, pageSize: PropTypes.number,
  onPageChange: PropTypes.func, onReview: PropTypes.func, onRestore: PropTypes.func, onOpenSkill: PropTypes.func, onBack: PropTypes.func, canRestore: PropTypes.bool,
};
export default function ArchivedSkillsView(props) { return <Archived key={props.scopeKey} {...props} />; }
ArchivedSkillsView.propTypes = Archived.propTypes;
