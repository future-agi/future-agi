import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { formatDistanceToNow } from "date-fns";
import { Alert, Avatar, Box, Button, Chip, IconButton, Link, ListItemIcon, Menu, MenuItem, Stack, TextField, Typography } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import { downloadSkillsZip, useArchiveSkill, useSkillsList } from "src/api/skills";
import { HOST_API } from "src/config-global";
import PageHeader from "src/components/page-header";
import MetricStrip from "src/components/metric-strip";
import ListToolbar from "src/components/list-toolbar";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { DataTable, DataTablePagination } from "src/components/data-table";
import ContentSkeleton from "src/components/content-skeleton";
import EmptyState from "src/components/empty-state";
import ErrorState from "src/components/error-state";
import { ConfirmDialog } from "src/components/custom-dialog";
import Iconify from "src/components/iconify";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import { buildInstallCommand } from "src/sections/skills/utils/install";
import NewSkillDialog from "./NewSkillDialog";
import ImportSkillsDialog from "./ImportSkillsDialog";
import LibraryEmptyState from "./LibraryEmptyState";

export default function SkillsLibraryView() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [newTemplate, setNewTemplate] = useState(null);
  const [importOpen, setImportOpen] = useState(false);
  const [menu, setMenu] = useState(null);
  const [archiveTarget, setArchiveTarget] = useState(null);
  const [archiveError, setArchiveError] = useState("");
  const archive = useArchiveSkill();
  const search = params.get("search") || "";
  const tag = params.get("tag") || "";
  const label = params.get("label") || "";
  const sort = ["updated", "name", "created"].includes(params.get("sort")) ? params.get("sort") : "updated";
  const order = params.get("order") === "asc" ? "asc" : "desc";
  const parsedPage = Number(params.get("page") || 1);
  const page = Number.isSafeInteger(parsedPage) && parsedPage > 0 ? parsedPage : 1;
  const requestedPageSize = Number(params.get("page_size") || 25);
  const pageSize = [10, 25, 50, 100].includes(requestedPageSize) ? requestedPageSize : 25;
  const query = useSkillsList({ search, tag, label, sort, order, page, pageSize });
  const previewState = ["loading", "error", "empty"].includes(params.get("state")) ? params.get("state") : null;
  const loading = previewState === "loading" || (!previewState && query.isPending);
  const failed = previewState === "error" || (!previewState && query.isError);
  const rows = previewState === "empty" ? [] : query.data?.results || [];
  const count = previewState === "empty" ? 0 : query.data?.count || 0;
  const filtered = Boolean(search || tag || label);
  const firstRun = !loading && !failed && count === 0 && !filtered;
  const tags = [...new Set([...(tag ? [tag] : []), ...rows.flatMap((skill) => skill.tags)])].sort();

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
  const openSkill = (skill, edit = false) => navigate(`/dashboard/skills/${skill.id}${edit ? "/edit" : ""}`);
  const runMenuAction = async (action) => {
    const skill = menu.skill;
    setMenu(null);
    try {
      if (action === "copy") {
        await navigator.clipboard.writeText(buildInstallCommand({ hostApi: HOST_API, names: [skill.name], label: "production", dir: ".claude/skills" }));
        enqueueSnackbar("Install command copied", { variant: "success" });
      } else if (action === "download") await downloadSkillsZip({ names: [skill.name], version: skill.latestVersion });
    } catch (_failure) {
      enqueueSnackbar(action === "copy" ? "Couldn't copy the install command. Try again." : "Couldn't download this skill. Try again.", { variant: "error" });
    }
  };
  const confirmArchive = async () => {
    if (archive.isPending) return;
    setArchiveError("");
    try {
      await archive.mutateAsync(archiveTarget.id);
      setArchiveTarget(null);
      enqueueSnackbar("Skill archived", { variant: "success" });
    } catch (_failure) { setArchiveError("Couldn't archive this skill. Try again."); }
  };
  const columns = [
    { accessorKey: "name", header: "Name", minSize: 240, meta: { flex: 2 }, cell: ({ row: { original: skill } }) => (
      <Stack sx={{ minWidth: 0 }}>
        <Link href={`/dashboard/skills/${skill.id}`} underline="hover" color="text.primary" onClick={(event) => {
          event.stopPropagation();
          if (!event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) { event.preventDefault(); openSkill(skill); }
        }} sx={{ typography: "s1", fontFamily: "monospace" }}>{skill.name}</Link>
        <Typography variant="s3" color="text.secondary" noWrap>{skill.description}</Typography>
      </Stack>
    ) },
    { id: "production", header: "Production", size: 108, cell: ({ row: { original: skill } }) => {
      const production = skill.labels.find((item) => item.name === "production");
      return production ? <Chip size="small" variant="outlined" label={`v${production.version}`} /> : "—";
    } },
    { accessorKey: "latestVersion", header: "Latest", size: 118, cell: ({ getValue }) => getValue() ? `v${getValue()}` : "Not published" },
    { accessorKey: "tags", header: "Tags", minSize: 150, meta: { flex: 1 }, cell: ({ getValue }) => <Typography variant="s3" noWrap>{getValue().join(", ") || "—"}</Typography> },
    { accessorKey: "fetches7d", header: "Fetches 7d", size: 100 },
    { accessorKey: "updatedAt", header: "Updated", minSize: 170, cell: ({ row: { original: skill } }) => (
      <Stack direction="row" alignItems="center" spacing={1}>
        {skill.updatedBy && <Avatar alt={skill.updatedBy.name} sx={{ width: 24, height: 24 }}>{skill.updatedBy.name?.slice(0, 1)}</Avatar>}
        <Stack><Typography variant="s3">{formatDistanceToNow(new Date(skill.updatedAt), { addSuffix: true })}</Typography>
          <Typography variant="s3" color="text.secondary">{skill.updatedBy?.name || "API"}</Typography></Stack>
      </Stack>
    ) },
    { id: "actions", header: "", size: 48, cell: ({ row: { original: skill } }) => (
      <IconButton size="small" aria-label={`Actions for ${skill.name}`} onClick={(event) => { event.stopPropagation(); setMenu({ anchor: event.currentTarget, skill }); }}>
        <Iconify icon="solar:menu-dots-bold" width={16} />
      </IconButton>
    ) },
  ].map((column) => ({ ...column, enableSorting: false }));

  return (
    <Stack sx={{ p: { xs: 2, md: 3 }, minWidth: 0 }}>
      <PageHeader title="Skills" description="Versioned instructions and files your agents load on demand." sx={{ mb: 0 }}
        actions={!firstRun && <><Button variant="outlined" onClick={() => setImportOpen(true)}>Import</Button><Button variant="contained" onClick={() => setNewTemplate("blank")}>New skill</Button></>} />
      {!loading && !failed && count > 0 && <MetricStrip sx={{ mt: 2, gridTemplateColumns: { xs: "repeat(2, minmax(0, 1fr))", md: "repeat(4, minmax(0, 1fr))" } }} items={[
        { key: "skills", label: "Skills", value: count, hint: filtered ? "Matching filters" : "In this workspace" },
        { key: "production", label: "In production", value: rows.filter((skill) => skill.labels.some((item) => item.name === "production")).length, hint: "On this page" },
        { key: "fetches", label: "Fetches (7d)", value: rows.reduce((sum, skill) => sum + skill.fetches7d, 0), hint: "On this page" },
        { key: "drafts", label: "Drafts in progress", value: rows.filter((skill) => skill.hasDraft).length, hint: "Your drafts on this page" },
      ]} />}
      {!firstRun && <ListToolbar sx={{ flexWrap: "wrap", rowGap: 1 }}>
        <FormSearchField searchQuery={search} placeholder="Search skills" inputProps={{ "aria-label": "Search skills" }} onChange={(event) => changeParams({ search: event.target.value })} />
        <TextField select label="Tag" size="small" value={tag} onChange={(event) => changeParams({ tag: event.target.value })} sx={{ minWidth: 120 }}>
          <MenuItem value="">Any tag</MenuItem>{tags.map((value) => <MenuItem key={value} value={value}>{value}</MenuItem>)}
        </TextField>
        <TextField select label="Label" size="small" value={label} onChange={(event) => changeParams({ label: event.target.value })} sx={{ minWidth: 140 }}>
          <MenuItem value="">Any label</MenuItem><MenuItem value="production">Production</MenuItem><MenuItem value="staging">Staging</MenuItem>
          {label && !["production", "staging"].includes(label) && <MenuItem value={label}>{label}</MenuItem>}
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
        : failed ? <ErrorState title="Couldn't load skills" detail="Your library is still saved. Try loading it again." onRetry={() => { changeParams({ state: null }, false); query.refetch(); }} />
        : firstRun ? <LibraryEmptyState onCreate={setNewTemplate} onImport={() => setImportOpen(true)} />
        : rows.length === 0 ? <EmptyState variant="no-results" title="No skills found" description="Try a different search or clear the filters."
          onClearFilters={() => changeParams({ search: null, tag: null, label: null, state: null })} />
        : <Box sx={{ minWidth: 0 }}><Box sx={{ height: rows.length * ROW_HEIGHT.twoLine + TABLE_HEADER_HEIGHT.default }}><DataTable columns={columns} data={rows} rowCount={count} rowHeight={ROW_HEIGHT.twoLine} onRowClick={openSkill} /></Box>
          <DataTablePagination page={page - 1} pageSize={pageSize} total={count} pageCount={query.data.totalPages}
            onPageChange={(nextPage) => changeParams({ page: nextPage + 1 }, false)} onPageSizeChange={(size) => changeParams({ page_size: size })} />
        </Box>}
      <Menu anchorEl={menu?.anchor} open={Boolean(menu)} onClose={() => setMenu(null)}>
        <MenuItem onClick={() => { openSkill(menu.skill); setMenu(null); }}><ListItemIcon><Iconify icon="solar:folder-open-linear" width={16} /></ListItemIcon>Open</MenuItem>
        <MenuItem onClick={() => { openSkill(menu.skill, true); setMenu(null); }}><ListItemIcon><Iconify icon="solar:pen-linear" width={16} /></ListItemIcon>Edit</MenuItem>
        <MenuItem disabled={!menu?.skill.labels.some((item) => item.name === "production")} onClick={() => runMenuAction("copy")}><ListItemIcon><Iconify icon="solar:copy-linear" width={16} /></ListItemIcon>Copy install command</MenuItem>
        <MenuItem disabled={!menu?.skill.latestVersion} onClick={() => runMenuAction("download")}><ListItemIcon><Iconify icon="solar:download-linear" width={16} /></ListItemIcon>Download</MenuItem>
        <MenuItem onClick={() => { setArchiveTarget(menu.skill); setArchiveError(""); setMenu(null); }}><ListItemIcon><Iconify icon="solar:archive-linear" width={16} /></ListItemIcon>Archive</MenuItem>
      </Menu>
      {archiveError && <Alert severity="error">{archiveError}</Alert>}
      <ConfirmDialog open={Boolean(archiveTarget)} onClose={() => { if (!archive.isPending) setArchiveTarget(null); }} closeDisabled={archive.isPending}
        title="Archive skill?" content={archiveError || `Archive ${archiveTarget?.name || "this skill"}? It will leave the library and agents will no longer be able to fetch it.`}
        action={<Button variant="contained" color="error" disabled={archive.isPending} onClick={confirmArchive}>Archive</Button>} />
      {newTemplate !== null && <NewSkillDialog open onClose={() => setNewTemplate(null)} initialTemplate={newTemplate} />}
      {importOpen && <ImportSkillsDialog open onClose={() => setImportOpen(false)} />}
    </Stack>
  );
}
