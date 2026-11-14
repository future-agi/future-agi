import { useEffect, useState } from "react";
import { Link as RouterLink, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import When from "../components/When";
import { httpStatus } from "../utils/conclusions";
import { useSkillsAccess } from "../utils/access";
import { Autocomplete, Avatar, Box, Button, Link, MenuItem, Stack, TextField, Typography } from "@mui/material";
import { useSkillsList } from "src/api/skills";
import { paths } from "src/routes/paths";
import PageHeader from "src/components/page-header";
import StatusBadge from "src/components/status-badge";
import ListToolbar from "src/components/list-toolbar";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { DataTable, DataTablePagination } from "src/components/data-table";
import ContentSkeleton from "src/components/content-skeleton";
import EmptyState from "src/components/empty-state";
import ErrorState from "src/components/error-state";
import { dsSize } from "src/theme/ds-tokens";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import { usePreviewState } from "src/sections/skills/components/SkillsStatePreview";
import { LIMITS, SYSTEM_LABELS } from "src/sections/skills/constants";
import NewSkillDialog from "./NewSkillDialog";
import ImportSkillsDialog from "./ImportSkillsDialog";
import LibraryEmptyState from "./LibraryEmptyState";
import SkillRowMenu from "./SkillRowMenu";

export default function SkillsLibraryView() {
  const navigate = useNavigate();
  const { canWrite } = useSkillsAccess();
  const [params, setParams] = useSearchParams();
  const [newTemplate, setNewTemplate] = useState(null);
  const location = useLocation();
  // Returning from draft preservation reopens the same retained migration review.
  const [reopenPlanIds] = useState(() => {
    const ids = location.state?.reopenMigration?.planIds;
    return Array.isArray(ids) && ids.length && ids.every((id) => typeof id === "string") ? ids : null;
  });
  const [importOpen, setImportOpen] = useState(Boolean(reopenPlanIds));
  useEffect(() => {
    if (location.state?.reopenMigration) navigate(`${location.pathname}${location.search}`, { replace: true, state: null });
  }, [location, navigate]);
  const search = params.get("search") || "";
  const tag = params.get("tag") || "";
  const label = params.get("label") || "";
  const sort = ["updated", "name", "created"].includes(params.get("sort")) ? params.get("sort") : "updated";
  const order = params.get("order") === "asc" ? "asc" : "desc";
  const parsedPage = Number(params.get("page") || 1);
  const page = Number.isSafeInteger(parsedPage) && parsedPage > 0 ? parsedPage : 1;
  const requestedPageSize = Number(params.get("page_size") || LIMITS.DEFAULT_PAGE_SIZE);
  const pageSize = [10, 25, 50, 100].includes(requestedPageSize) ? requestedPageSize : LIMITS.DEFAULT_PAGE_SIZE;
  const query = useSkillsList({ search, tag, label, sort, order, page, pageSize });
  const statePreview = usePreviewState();
  const previewState = params.get("state") === "filtered-empty" ? "filtered-empty" : statePreview;
  const loading = previewState === "loading" || (!previewState && query.isPending);
  const status = httpStatus(query.error);
  const errorData = query.error?.response?.data ?? query.error?.data ?? query.error;
  const pageError = errorData?.page ?? errorData?.errors?.page ?? errorData?.detail?.page ?? errorData?.details?.page;
  const outOfRange = !previewState && page > 1 && (status === 404
    || ([400, 422].includes(status) && Boolean(pageError))
    || (!query.isError && query.data?.count > 0 && !query.data.results?.length));
  const failed = !outOfRange && (previewState === "error" || (!previewState && query.isError));
  const rows = ['empty', 'filtered-empty'].includes(previewState) ? [] : query.data?.results || [];
  const count = ['empty', 'filtered-empty'].includes(previewState) ? 0 : query.data?.count || 0;
  const stats = previewState === "empty" ? { total: 0 } : query.data?.stats;
  const filtered = previewState === 'filtered-empty' || Boolean(search || tag || label);
  const firstRun = !loading && !failed && stats?.total === 0 && !filtered;
  // This page supplies suggestions, not an exhaustive workspace tag inventory.
  const tagSuggestions = [...new Set([...(tag ? [tag] : []), ...rows.flatMap((skill) => skill.tags)])].sort();

  const changeParams = (changes, resetPage = true) => {
    setParams((previous) => {
      const next = new URLSearchParams(previous);
      if (resetPage) next.delete("page");
      Object.entries(changes).forEach(([key, value]) => {
        if (value === "" || value == null) next.delete(key);
        else next.set(key, String(value));
      });
      return next;
    }, { replace: true });
  };
  const openSkill = (skill) => navigate(paths.dashboard.skills.detail(skill.id));
  const columns = [
    { accessorKey: "name", header: "Name", minSize: 240, meta: { flex: 2 }, cell: ({ row: { original: skill } }) => (
      <Stack sx={{ minWidth: 0 }}>
        <Stack direction="row" spacing={1} alignItems="center"><Link component={RouterLink} to={paths.dashboard.skills.detail(skill.id)} underline="hover" color="text.primary"
          onClick={(event) => event.stopPropagation()} sx={{ typography: "s1" }}>{skill.name}</Link>{skill.hasDraft && <StatusBadge status="draft" label="Draft" />}</Stack>
        <Typography variant="s3" color="text.secondary" noWrap>{skill.description}</Typography>
      </Stack>
    ) },
    { id: "production", header: "Production", size: 150, cell: ({ row: { original: skill } }) => {
      const production = skill.labels.find((item) => item.name === "production");
      return production ? <StatusBadge status="released" label={`v${production.version}`} /> : <StatusBadge status="unset" label="Not in production" />;
    } },
    { accessorKey: "latestVersion", header: "Latest", size: 118, cell: ({ getValue }) => getValue() ? `v${getValue()}` : "Not published" },
    { accessorKey: "tags", header: "Tags", minSize: 150, meta: { flex: 1 }, cell: ({ getValue }) => <Typography variant="s3" noWrap>{getValue().join(", ") || "—"}</Typography> },
    { accessorKey: "fetches7d", header: "Fetches (7 days)", size: 125 },
    { accessorKey: "updatedAt", header: "Updated", minSize: 170, cell: ({ row: { original: skill } }) => (
      <Stack direction="row" alignItems="center" spacing={1}>
        {skill.updatedBy && <Avatar alt={skill.updatedBy.name} sx={{ width: 20, height: 20, typography: "s3", fontFamily: "inherit" }}>{skill.updatedBy.name?.split(/\s+/).map((part) => part[0]).slice(0, 2).join("")}</Avatar>}
        <Stack><Typography variant="s3"><When value={skill.updatedAt} /></Typography>
          <Typography variant="s3" color="text.secondary">{skill.updatedBy?.name || "API"}</Typography></Stack>
      </Stack>
    ) },
    { id: "actions", header: "", size: 48, cell: ({ row: { original: skill } }) => <SkillRowMenu skill={skill} /> },
  ].filter((column) => column.accessorKey !== "tags" || rows.some((skill) => skill.tags?.length)).map((column) => ({ ...column, enableSorting: false }));

  return (
    <Stack sx={{ p: { xs: 2, md: 3 }, minWidth: 0 }}>
      <PageHeader title="Skills" description="Versioned instructions and files your agents load on demand." sx={{ mb: 0 }}
        actions={canWrite && !firstRun && <><Button variant="outlined" onClick={() => setImportOpen(true)}>Import</Button><Button variant={outOfRange ? "outlined" : "contained"} color="primary" onClick={() => setNewTemplate("blank")}>New skill</Button></>} />
      {!loading && !failed && stats?.total > 0 && <Typography variant="s3" color="text.secondary" sx={{ mt: 1.5 }}>{[`${stats.total} ${stats.total === 1 ? 'skill' : 'skills'}`, stats.inProduction > 0 && `${stats.inProduction} in production`, stats.fetches7d > 0 && `${stats.fetches7d} ${stats.fetches7d === 1 ? 'fetch' : 'fetches'} in 7 days`, stats.myDrafts > 0 && `${stats.myDrafts} ${stats.myDrafts === 1 ? 'draft' : 'drafts'}`].filter(Boolean).join(' · ')}</Typography>}
      {!loading && query.isFetching && query.data && <StatusBadge status="checking" label="Updating…" />}
      {!firstRun && <ListToolbar alignItems="flex-end" sx={{ flexWrap: "wrap", rowGap: 1 }}>
        <FormSearchField sx={{ width: 280, maxWidth: "100%" }} label="Search skills" searchQuery={search} placeholder="Search skills" inputProps={{ "aria-label": "Search skills" }} onChange={(event) => changeParams({ search: event.target.value })} />
        <Autocomplete freeSolo autoSelect selectOnFocus forcePopupIcon size="small" options={tagSuggestions} value={tag || null}
          onChange={(_event, value) => changeParams({ tag: value?.trim() || "" })} sx={{ width: 200, maxWidth: "100%", "& .MuiOutlinedInput-root": { height: dsSize.control.sm, py: 0 } }}
          renderInput={(inputParams) => <TextField {...inputParams} size="small" label="Tag" placeholder="Any tag" />} />
        <TextField select label="Label" size="small" SelectProps={{ displayEmpty: true }} value={label} onChange={(event) => changeParams({ label: event.target.value })} sx={{ minWidth: 140 }}>
          <MenuItem value="">All labels</MenuItem>{SYSTEM_LABELS.map((value) => <MenuItem key={value} value={value}>{value}</MenuItem>)}
          {label && !SYSTEM_LABELS.includes(label) && <MenuItem value={label}>{label}</MenuItem>}
        </TextField>
        <TextField select label="Sort" size="small" value={`${sort}:${order}`} sx={{ minWidth: 180 }} onChange={(event) => {
          const [nextSort, nextOrder] = event.target.value.split(":"); changeParams({ sort: nextSort, order: nextOrder });
        }}>
          <MenuItem value="updated:desc">Recently updated</MenuItem><MenuItem value="updated:asc">Least recently updated</MenuItem>
          <MenuItem value="name:asc">Name A–Z</MenuItem><MenuItem value="name:desc">Name Z–A</MenuItem>
          <MenuItem value="created:desc">Newest first</MenuItem><MenuItem value="created:asc">Oldest first</MenuItem>
        </TextField>
      </ListToolbar>}
      {loading ? <ContentSkeleton variant="rows" rows={6} label="Loading skills" />
        : outOfRange ? <EmptyState variant="no-results" title="This page is no longer available" action={<Button variant="contained" onClick={() => changeParams({ page: 1 }, false)}>Go to first page</Button>} />
        : failed ? <ErrorState title="Couldn't load skills." detail="Your library is still saved. Try loading it again." onRetry={() => { changeParams({ state: null }, false); query.refetch(); }} />
        : firstRun ? <LibraryEmptyState isViewer={!canWrite} onCreate={setNewTemplate} onImport={() => setImportOpen(true)} />
        : rows.length === 0 && count > 0 ? <EmptyState variant="no-results" title="No skills on this page" description="The library changed since you opened this page."
          action={<Button variant="outlined" onClick={() => changeParams({ page: 1 }, false)}>Go to first page</Button>} />
        : rows.length === 0 ? <EmptyState variant="no-results" title="No skills match these filters." description="Try a different search or clear the filters."
          onClearFilters={() => changeParams({ search: null, tag: null, label: null, state: null })} />
        : <Box sx={{ minWidth: 0 }}><Box sx={{ height: rows.length * ROW_HEIGHT.twoLine + TABLE_HEADER_HEIGHT.default }}><DataTable columns={columns} data={rows} rowCount={count} rowHeight={ROW_HEIGHT.twoLine} onRowClick={openSkill} /></Box>
          <DataTablePagination page={page - 1} pageSize={pageSize} pageSizeOptions={[10, 25, 50, 100]} total={count} pageCount={query.data.totalPages}
            disabled={query.isFetching} onPageChange={(nextPage) => changeParams({ page: nextPage + 1 }, false)} onPageSizeChange={(size) => changeParams({ page_size: size })} />
        </Box>}
      {canWrite && newTemplate !== null && <NewSkillDialog open onClose={() => setNewTemplate(null)} initialTemplate={newTemplate} />}
      {canWrite && importOpen && <ImportSkillsDialog open onClose={() => setImportOpen(false)} {...(reopenPlanIds ? { initialTab: "migration", reopenPlanIds } : {})} />}
    </Stack>
  );
}
