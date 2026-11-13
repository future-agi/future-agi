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
} from "@mui/material";

import { useSkillLabels, useSkillVersions } from "src/api/skills";
import { useRouter } from "src/routes/hooks";
import { fDateTime, relativeTime } from "src/utils/format-time";
import ContentSkeleton from "src/components/content-skeleton";
import { DataTable, DataTablePagination } from "src/components/data-table";
import EmptyState from "src/components/empty-state";
import ErrorState from "src/components/error-state";
import Iconify from "src/components/iconify";
import Section from "src/components/section";
import SelectionBar from "src/components/selection-bar";
import StatusBadge from "src/components/status-badge";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import { usePreviewState } from "src/sections/skills/components/SkillsStatePreview";
import IdentityChip from "../components/IdentityChip";
import LabelProtectionDialog from '../governance/LabelProtectionDialog';
import LabelHistoryDrawer from "./LabelHistoryDrawer";
import MoveLabelDialog from "./MoveLabelDialog";
import RestoreVersionDialog from "./RestoreVersionDialog";

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
      size: 90,
      cell: ({ getValue }) => (
        <Typography variant="s1" fontWeight={500}>
          v{getValue()}
        </Typography>
      ),
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
      cell: ({ getValue }) => <Stack direction="row" spacing={0.5}>{(getValue() || []).length ? getValue().map((name) => <StatusBadge key={name} status="unset" label={name} />) : '—' }</Stack>,
    },
    {
      accessorKey: "digest",
      header: "Digest",
      size: 150,
      cell: ({ getValue }) => <Box onClick={(event) => event.stopPropagation()}><IdentityChip value={getValue()} inline /></Box>,
    },
    { accessorKey: "fileCount", header: "Files", size: 70 },
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
        title="Labels"
        description="Agents fetching a label receive the version it points to."
        actions={
          <Stack direction="row" spacing={1}>
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
            <IconButton size="small" aria-label="Label settings" onClick={(event) => setLabelMenu({ anchor: event.currentTarget })}><Iconify icon="solar:menu-dots-bold" /></IconButton>
          </Stack>
        }
      >
        <Typography variant="s3" color="text.secondary">Protected names: {skill.protectedLabelNames?.join(', ') || 'None'}. Protection remains even when a label is unassigned.</Typography>
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
              direction="row"
              spacing={1}
              alignItems="center"
              sx={{ py: 1, borderBottom: "1px solid", borderColor: "divider" }}
            >
              <Stack direction="row" spacing={1} alignItems="center" sx={{ flex: 1, minWidth: 0 }}>
                <Typography variant="s1">{label.name} → v{label.version}</Typography>
                {label.isProtected && <StatusBadge status="protected" label="Protected" />}
              </Stack>
              <IconButton size="small" aria-label={`Actions for ${label.name}`} onClick={(event) => setLabelMenu({ anchor: event.currentTarget, label })}><Iconify icon="solar:menu-dots-bold" /></IconButton>
            </Stack>
          ))}
      </Section>
      <Section
        title="Versions"
        description="Select exactly two versions on this page to compare their files."
      >
        <SelectionBar
          count={selected.length}
          noun="versions"
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
        <Box sx={{ height: rows.length * ROW_HEIGHT.default + TABLE_HEADER_HEIGHT.default, minWidth: 0 }}>
          <DataTable
            data={rows}
            columns={columns}
            rowCount={versions.data?.count || 0}
            onRowClick={(row) =>
              router.push(`${detailPath}?tab=files&version=${row.number}`)
            }
          />
        </Box>
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
