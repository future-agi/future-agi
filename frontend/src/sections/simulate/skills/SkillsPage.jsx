import React, { useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Box,
  Button,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TablePagination,
  TableRow,
  Typography,
} from "@mui/material";
import { formatDistanceToNow } from "date-fns";
import ContentSkeleton from "src/components/content-skeleton";
import EmptyState from "src/components/empty-state";
import ErrorState from "src/components/error-state";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import Iconify from "src/components/iconify";
import ListToolbar from "src/components/list-toolbar";
import PageHeader from "src/components/page-header";
import StatusBadge from "src/components/status-badge";
import { useDebounce } from "src/hooks/use-debounce";
import {
  isSkillsDisabled,
  useSkillsList,
} from "src/api/simulate-skills/skills";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import SkillDrawer from "./SkillDrawer";
import SkillEditorDialog from "./SkillEditorDialog";
import { tagVersion, testBadge } from "./skillLabels";

const PAGE_SIZE = 25;
const COLUMNS = [
  "Skill",
  "Latest",
  "Production",
  "Last test",
  "Used by",
  "Updated",
];

export default function SkillsPage() {
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [creating, setCreating] = useState(false);
  const debounced = useDebounce(search.trim(), 400);
  const forced = params.get("state");
  const openSkill = params.get("skill");
  const list = useSkillsList({
    search: debounced,
    page: page + 1,
    limit: PAGE_SIZE,
  });

  const setOpenSkill = (id) => {
    const next = new URLSearchParams(params);
    if (id) next.set("skill", id);
    else next.delete("skill");
    setParams(next, { replace: true });
  };

  const rows = forced === "empty" ? [] : list.data?.results || [];
  const loading = forced === "loading" || (list.isLoading && !list.data);
  const failed = forced === "error" || (list.isError && !list.data);
  const disabled = isSkillsDisabled(list.error);
  const firstRun = !loading && !failed && !rows.length && !debounced;

  const newSkill = (
    <Button
      variant="contained"
      startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
      onClick={() => setCreating(true)}
    >
      New skill
    </Button>
  );

  let body;
  if (loading) body = <ContentSkeleton variant="rows" rows={6} />;
  else if (disabled)
    body = (
      <EmptyState
        icon="solar:lightbulb-bolt-linear"
        title="The skills library isn't turned on"
        description="Ask an admin to enable it for this workspace."
      />
    );
  else if (failed)
    body = <ErrorState title="Couldn't load skills" onRetry={list.refetch} />;
  else if (firstRun)
    body = (
      <EmptyState
        icon="solar:lightbulb-bolt-linear"
        title="No skills yet"
        description="Keep reusable skills and agent instructions in one place, test a version on your agents, then promote it."
        action={newSkill}
      />
    );
  else if (!rows.length)
    body = (
      <EmptyState
        variant="no-results"
        title="No skills match"
        description="Try another name."
      />
    );
  else
    body = (
      <>
        <Table size="small">
          <TableHead>
            <TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
              {COLUMNS.map((column) => (
                <TableCell key={column}>{column}</TableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map((skill) => {
              const badge = testBadge(
                skill.last_test_state,
                skill.last_test_verdict,
              );
              const production = tagVersion(skill, "production");
              return (
                <TableRow
                  key={skill.id}
                  hover
                  onClick={() => setOpenSkill(skill.id)}
                  sx={{ height: ROW_HEIGHT.default, cursor: "pointer" }}
                >
                  <TableCell>
                    <Typography sx={{ typography: "s1", fontWeight: 500 }}>
                      {skill.name}
                    </Typography>
                    <Typography
                      noWrap
                      sx={{
                        typography: "s3",
                        color: "text.secondary",
                        maxWidth: 420,
                      }}
                    >
                      {skill.kind === "context"
                        ? `Agent context · ${skill.description || "No description"}`
                        : skill.description}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    {skill.latest_version ? `v${skill.latest_version}` : "-"}
                  </TableCell>
                  <TableCell
                    sx={{
                      color: production ? "text.primary" : "text.secondary",
                    }}
                  >
                    {production ? `v${production}` : "None yet"}
                  </TableCell>
                  <TableCell>
                    <StatusBadge
                      status={badge.status}
                      label={
                        skill.last_test_version
                          ? `v${skill.last_test_version} · ${badge.label}`
                          : badge.label
                      }
                      size="sm"
                    />
                  </TableCell>
                  <TableCell sx={{ color: "text.secondary" }}>
                    {skill.used_by
                      ? `${skill.used_by} agent${skill.used_by === 1 ? "" : "s"}`
                      : "No agents"}
                  </TableCell>
                  <TableCell sx={{ color: "text.secondary" }}>
                    {formatDistanceToNow(new Date(skill.updated_at), {
                      addSuffix: true,
                    })}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
        {(list.data?.count || 0) > PAGE_SIZE && (
          <TablePagination
            component="div"
            count={list.data.count}
            page={page}
            onPageChange={(_, next) => setPage(next)}
            rowsPerPage={PAGE_SIZE}
            rowsPerPageOptions={[PAGE_SIZE]}
          />
        )}
      </>
    );

  return (
    <Box sx={{ p: 2 }}>
      <PageHeader
        title="Skills"
        description="Keep reusable skills in one place, test a version on your agents, then promote it."
        actions={firstRun || disabled ? null : newSkill}
        sx={{ mb: 0 }}
      />
      {!disabled && !firstRun && (
        <ListToolbar>
          <FormSearchField
            searchQuery={search}
            onChange={(event) => {
              setSearch(event.target.value);
              setPage(0);
            }}
          />
        </ListToolbar>
      )}
      {body}
      <SkillEditorDialog
        open={creating}
        onClose={() => setCreating(false)}
        mode="create"
        onSaved={(saved) => setOpenSkill(saved.id)}
      />
      <SkillDrawer skillId={openSkill} onClose={() => setOpenSkill(null)} />
    </Box>
  );
}
