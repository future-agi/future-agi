import { useState } from "react";
import PropTypes from "prop-types";
import { useSearchParams } from "react-router-dom";
import { paths } from "src/routes/paths";
import {
  Box,
  Button,
  Checkbox,
  IconButton,
  ListItemIcon,
  Menu,
  MenuItem,
  Stack,
  Typography,
  Tooltip,
  Table, TableHead, TableBody, TableRow, TableCell, TableContainer,
} from "@mui/material";

import { useSkillLabels, useSkillVersions } from "src/api/skills";
import { useRouter } from "src/routes/hooks";
import { fDateTime, relativeTime } from "src/utils/format-time";
import ContentSkeleton from "src/components/content-skeleton";
import { DataTablePagination } from "src/components/data-table";
import EmptyState from "src/components/empty-state";
import ErrorState from "src/components/error-state";
import Iconify from "src/components/iconify";
import Section from "src/components/section";
import SelectionBar from "src/components/selection-bar";
import StatusBadge from "src/components/status-badge";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import { usePreviewState } from "src/sections/skills/components/SkillsStatePreview";
import VersionLabel from "../components/VersionLabel";
import LabelProtectionDialog from '../governance/LabelProtectionDialog';
import LabelHistoryDrawer from "./LabelHistoryDrawer";
import MoveLabelDialog from "./MoveLabelDialog";
import RestoreVersionDialog from "./RestoreVersionDialog";

// Below sm each version is a stacked card on the 16px gutter: a 32px checkbox column runs the
// card's full height; line 1 is the version and its menu, line 2 the note, line 3
// "author · time · files", line 4 the label badges. Desktop keeps the table untouched.
const cell = (column, extra) => [`& tbody td[data-column="${column}"]`, extra];
const versionsTableSx = (theme) => ({
  "& tbody td": { overflowWrap: "normal", wordBreak: "normal" },
  '& td[data-column="meta"]': { display: "none" },
  [theme.breakpoints.down("sm")]: {
    display: "block",
    "& thead": { position: "absolute", width: "1px", height: "1px", p: 0, m: "-1px", overflow: "hidden", clip: "rect(0 0 0 0)", whiteSpace: "nowrap", border: 0 },
    "& tbody": { display: "block" },
    "& tbody tr": { display: "grid", gridTemplateColumns: "32px minmax(0, 1fr) auto", alignItems: "start", rowGap: 0.5, height: "auto", py: 1.5, borderBottom: `1px solid ${theme.palette.divider}`, "&:last-of-type": { borderBottom: 0 } },
    "& tbody td": { display: "block", minWidth: 0, border: 0, p: 0, whiteSpace: "normal" },
    ...Object.fromEntries([
      cell("select", { gridColumn: "1", gridRow: "1 / span 4", p: 0, width: "auto", "& .MuiCheckbox-root": { p: 0.5, ml: -0.5 } }),
      cell("number", { gridColumn: "2", gridRow: "1", alignSelf: "center" }),
      cell("actions", { gridColumn: "3", gridRow: "1", justifySelf: "end", alignSelf: "center", "& .MuiIconButton-root": { mr: -1 } }),
      cell("note", { gridColumn: "2 / -1", gridRow: "2", "& .MuiTypography-root": { whiteSpace: "normal", overflowWrap: "anywhere" } }),
      cell("meta", { display: "block", gridColumn: "2 / -1", gridRow: "3", ...theme.typography.s3, color: "text.secondary" }),
      cell("labels", { gridColumn: "2 / -1", gridRow: "4", '&[data-empty="true"]': { display: "none" } }),
      cell("createdBy", { display: "none" }),
      cell("createdAt", { display: "none" }),
      cell("fileCount", { display: "none" }),
    ]),
  },
});

export default function VersionsTab({ skill }) {
  const router = useRouter();
  const preview = usePreviewState();
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(25);
  const [, setParams] = useSearchParams();
  const [selectedIds, setSelectedIds] = useState([]);
  const [menu, setMenu] = useState(null);
  const [labelMenu, setLabelMenu] = useState(null);
  const [move, setMove] = useState(null);
  const [history, setHistory] = useState(null);
  const [protectionOpen, setProtectionOpen] = useState(false);
  const [restore, setRestore] = useState(null);
  const versions = useSkillVersions(skill.id, { page: page + 1, pageSize });
  const labels = useSkillLabels(skill.id);
  const rows = versions.data?.results ?? [];
  const selected = rows.filter((row) => selectedIds.includes(row.id));
  const production =
    !labels.isError &&
    labels.data?.find((label) => label.name === "production")?.version;
  const detailPath = paths.dashboard.skills.detail(skill.id);
  const compare = (from, to) =>
    router.push(
      `${paths.dashboard.skills.compare(skill.id)}?from=${from}&to=${to}`,
    );
  const changePage = (next) => {
    setSelectedIds([]);
    setPage(next);
  };
  const retryVersions = () => {
    if (preview)
      setParams(
        (current) => {
          const next = new URLSearchParams(current);
          next.delete("state");
          return next;
        },
        { replace: true },
      );
    versions.refetch();
  };
  const menuAction = (action) => {
    setMenu(null);
    action();
  };

  const columns = [
    { id: 'select', header: '', size: 48, cell: ({ row: { original } }) => <Checkbox size="small" checked={selectedIds.includes(original.id)} disabled={selectedIds.length === 2 && !selectedIds.includes(original.id)} inputProps={{ 'aria-label': `Select v${original.number} for comparison` }} onClick={(event) => event.stopPropagation()} onChange={(_, checked) => setSelectedIds((current) => checked ? [...current, original.id] : current.filter((id) => id !== original.id))} /> },
    {
      accessorKey: "number",
      header: "Version",
      size: 180,
      cell: ({ row: { original } }) => <Box component="span" onClick={(event) => event.stopPropagation()}><VersionLabel number={original.number} digest={original.digest} label="version" /></Box>,
    },
    {
      accessorKey: "note",
      header: "Note",
      minSize: 180,
      meta: { flex: 2 },
      cell: ({ getValue }) => (
        <Typography variant="s1" noWrap title={getValue() || ""}>
          {getValue() || "—"}
        </Typography>
      ),
    },
    {
      accessorKey: "createdBy",
      header: "Author",
      desktopOnly: true,
      size: 140,
      cell: ({ getValue }) => (
        <Typography variant="s1" noWrap>
          {getValue()?.name || "Unknown author"}
        </Typography>
      ),
    },
    {
      accessorKey: "createdAt",
      header: "Published",
      desktopOnly: true,
      size: 160,
      cell: ({ getValue }) => (
        <Typography
          variant="s3"
          color="text.secondary"
          title={fDateTime(getValue())}
        >
          {relativeTime(getValue())}
        </Typography>
      ),
    },
    {
      accessorKey: "labels",
      header: "Labels",
      size: 170,
      cell: ({ getValue }) => <Stack direction="row" spacing={0.5}>{(getValue() || []).length ? getValue().map((name) => <StatusBadge key={name} status={name === "production" ? "released" : "unset"} label={name} />) : '—' }</Stack>,
    },
    { accessorKey: "fileCount", header: "Files", size: 70, desktopOnly: true },
    // Below sm the author, time and file count read as one quiet line (author · time · files).
    { id: "meta", header: "", mobileOnly: true, cell: ({ row: { original } }) => [original.createdBy?.name || "Unknown author", relativeTime(original.createdAt), `${original.fileCount} ${original.fileCount === 1 ? "file" : "files"}`].join(" · ") },
    {
      id: "actions",
      header: "Actions",
      size: 72,
      cell: ({ row }) => (
        <IconButton
          size="small"
          aria-label={`Actions for v${row.original.number}`}
          onClick={(event) => {
            event.stopPropagation();
            setMenu({ anchor: event.currentTarget, version: row.original });
          }}
        >
          <Iconify icon="solar:menu-dots-bold" width={20} sx={{ pointerEvents: "none" }} />
        </IconButton>
      ),
    },
  ].map((column) => ({ ...column, enableSorting: false }));

  if (preview === "loading" || (!preview && versions.isLoading))
    return <ContentSkeleton variant="rows" rows={6} label="Loading versions" />;
  if (preview === "error" || (!preview && versions.isError))
    return (
      <ErrorState title="Could not load versions" onRetry={retryVersions} />
    );
  if (preview === "empty")
    return (
      <EmptyState
        title="No published versions yet"
        description="Publish your draft to create the first immutable version. Labels can then point agents to it."
      />
    );

  return (
    <Stack spacing={3} sx={{ minWidth: 0, flex: 1 }}>
      <Section
        sx={{ "& > .MuiStack-root:first-of-type": { flexDirection: { xs: "column", sm: "row" }, alignItems: { xs: "flex-start", sm: "center" }, gap: 1.5 } }}
        title="Labels"
        description="Agents fetching a label receive the version it points to."
        actions={
          <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: "wrap" }}>
            <Button
              variant="outlined"
              size="small"
              disabled={
                !skill.latestVersion || labels.isError || labels.isLoading
              }
              onClick={() => setMove({})}
            >
              Set label
            </Button>
            <Button size="small" onClick={(event) => setLabelMenu({ anchor: event.currentTarget })}>Label settings</Button>
          </Stack>
        }
      >

        {labels.isLoading && (
          <ContentSkeleton variant="rows" rows={2} label="Loading labels" />
        )}
        {labels.isError && (
          <ErrorState title="Could not load labels" onRetry={labels.refetch} />
        )}
        {!labels.isLoading && !labels.isError && labels.data?.length === 0 && (
          <Typography variant="s1" color="text.secondary">
            No labels yet. Set production to make this skill available to agents
            using the default selector.
          </Typography>
        )}
        {!labels.isError &&
          labels.data?.map((label) => (
            <Stack
              key={label.name}
              direction={{ xs: "column", sm: "row" }}
              spacing={1}
              alignItems={{ xs: "flex-start", sm: "center" }}
              sx={{ py: 1, borderBottom: "1px solid", borderColor: "divider" }}
            >
              <Stack direction="row" spacing={1} alignItems="center" sx={{ flex: 1, minWidth: 0 }}>
                <Typography variant="s1">{label.name.charAt(0).toUpperCase()}{label.name.slice(1)} → v{label.version}</Typography>
                {(label.isProtected || skill.protectedLabelNames?.includes(label.name)) && <Tooltip title={`Only reviewers can move ${label.name}.`}><span tabIndex={0}><StatusBadge status="protected" label="Protected" /></span></Tooltip>}
              </Stack>
              <Stack direction="row" spacing={1} alignItems="center">
              <Button size="small" disabled={label.canMove !== true} onClick={() => setMove({ label: label.name })}>Move {label.name}</Button>
              <Button size="small" onClick={() => setHistory(label.name)}>History for {label.name}</Button>
              <IconButton size="small" aria-label={`Actions for ${label.name}`} onClick={(event) => setLabelMenu({ anchor: event.currentTarget, label })}><Iconify icon="solar:menu-dots-bold" /></IconButton>
              </Stack>
            </Stack>
          ))}
      </Section>
      <Section
        title="Versions"
        description="Select two versions on this page to compare their files."
      >
        <TableContainer><Table size="small" aria-label="Skill versions" sx={versionsTableSx}>
          <TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>{columns.map((column) => <TableCell key={column.id || column.accessorKey} data-column={column.id || column.accessorKey} sx={column.mobileOnly ? { display: { xs: "table-cell", sm: "none" } } : undefined}>{column.header}</TableCell>)}</TableRow></TableHead>
          <TableBody>{rows.map((row) => <TableRow hover key={row.id} sx={{ height: ROW_HEIGHT.twoLine }} onClick={() => router.push(`${detailPath}?tab=files&version=${row.number}`)}>
            {columns.map((column) => <TableCell padding={column.id === "select" ? "checkbox" : "normal"} data-column={column.id || column.accessorKey} data-empty={column.accessorKey === "labels" && !(row.labels || []).length ? "true" : undefined} key={column.id || column.accessorKey}>{column.cell ? column.cell({ row: { original: row }, getValue: () => row[column.accessorKey] }) : row[column.accessorKey]}</TableCell>)}
          </TableRow>)}</TableBody>
        </Table></TableContainer>
        <DataTablePagination
          page={page}
          pageSize={pageSize}
          total={versions.data?.count || 0}
          onPageChange={changePage}
          onPageSizeChange={(size) => {
            setPageSize(size);
            changePage(0);
          }}
          loading={versions.isFetching}
          disabled={versions.isFetching}
        />
      </Section>
        <SelectionBar
          count={selected.length}
          sx={{ position: "sticky", bottom: 0, zIndex: 1, height: "auto", minHeight: 40, flexWrap: "wrap", py: 0.5 }}
          noun="version"
          onCancel={() => setSelectedIds([])}
          actions={[
            {
              label: selected.length === 2 ? `Compare ${selected.map((item) => item.number).sort((a, b) => a - b).map((number) => `v${number}`).join(" and ")}` : "Select two versions",
              disabled: selected.length !== 2,
              onClick: () => {
                if (selected.length === 2) {
                  const numbers = selected
                    .map((item) => item.number)
                    .sort((a, b) => a - b);
                  compare(numbers[0], numbers[1]);
                }
              },
            },
          ]}
        />
      <Menu
        anchorEl={menu?.anchor}
        open={Boolean(menu)}
        onClose={() => setMenu(null)}
      >
        <MenuItem
          onClick={() =>
            menuAction(() =>
              router.push(
                `${detailPath}?tab=files&version=${menu.version.number}`,
              ),
            )
          }
        >
          <ListItemIcon>
            <Iconify icon="solar:document-text-linear" width={16} />
          </ListItemIcon>
          View files
        </MenuItem>
        <MenuItem
          disabled={!menu || menu.version.number <= 1}
          onClick={() =>
            menuAction(() =>
              compare(menu.version.number - 1, menu.version.number),
            )
          }
        >
          <ListItemIcon>
            <Iconify icon="solar:transfer-horizontal-linear" width={16} />
          </ListItemIcon>
          Compare with previous
        </MenuItem>
        <MenuItem
          disabled={!production || menu?.version.number === production}
          onClick={() =>
            menuAction(() => compare(production, menu.version.number))
          }
        >
          <ListItemIcon>
            <Iconify icon="solar:transfer-horizontal-linear" width={16} />
          </ListItemIcon>
          Compare with production
        </MenuItem>
        <MenuItem
          onClick={() => menuAction(() => setRestore(menu.version.number))}
        >
          <ListItemIcon>
            <Iconify icon="solar:restart-linear" width={16} />
          </ListItemIcon>
          Restore as new version
        </MenuItem>
        <MenuItem
          onClick={() =>
            menuAction(() => setMove({ version: menu.version.number }))
          }
        >
          <ListItemIcon>
            <Iconify icon="solar:tag-linear" width={16} />
          </ListItemIcon>
          Set label…
        </MenuItem>
      </Menu>
      <Menu anchorEl={labelMenu?.anchor} open={Boolean(labelMenu)} onClose={() => setLabelMenu(null)}>
        {labelMenu?.label && <MenuItem disabled={labelMenu.label.canMove !== true} onClick={() => { setMove({ label: labelMenu.label.name }); setLabelMenu(null); }}><ListItemIcon><Iconify icon="solar:tag-linear" width={16} /></ListItemIcon>Move</MenuItem>}
        <MenuItem onClick={() => { setHistory(labelMenu?.label?.name || ''); setLabelMenu(null); }}><ListItemIcon><Iconify icon="solar:history-linear" width={16} /></ListItemIcon>History</MenuItem>
        <MenuItem disabled={skill.permissions?.canManageLabelProtection !== true} onClick={() => { setProtectionOpen(true); setLabelMenu(null); }}><ListItemIcon><Iconify icon="solar:lock-linear" width={16} /></ListItemIcon>Protection</MenuItem>
      </Menu>
      <LabelProtectionDialog open={protectionOpen} onClose={() => setProtectionOpen(false)} skill={skill} />
      <MoveLabelDialog
        open={move !== null}
        onClose={() => setMove(null)}
        skill={skill}
        {...move}
      />
      <LabelHistoryDrawer
        open={history !== null}
        onClose={() => setHistory(null)}
        skill={skill}
        label={history ?? undefined}
      />
      {restore !== null && (
        <RestoreVersionDialog
          skill={skill}
          number={restore}
          onClose={() => setRestore(null)}
        />
      )}
    </Stack>
  );
}

VersionsTab.propTypes = {
  skill: PropTypes.shape({
    id: PropTypes.string.isRequired,
    name: PropTypes.string.isRequired,
    latestVersion: PropTypes.number,
    protectedLabelNames: PropTypes.array, permissions: PropTypes.object,
  }).isRequired,
};
