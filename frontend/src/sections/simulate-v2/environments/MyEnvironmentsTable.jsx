import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { alpha, keyframes } from "@mui/material/styles";
import {
  Box, Stack, Typography, Tooltip, IconButton, Menu, MenuItem, ListItemIcon, ListItemText,
  Dialog, DialogTitle, DialogContent, DialogContentText, DialogActions, Button,
} from "@mui/material";
import { paths } from "src/routes/paths";
import { DataTable } from "src/components/data-table";
import Iconify from "src/components/iconify";
import { getAgentType } from "../_mock/agentTypes";
import { subTasksFor } from "../_mock/contract";
import { useSimStore } from "../store";

/**
 * My environments, as the same DataTable the platform uses on Evals,
 * Datasets and Agents.
 *
 * The card grid worked when every card carried the same three fields; the
 * moment environments started carrying a build status, an agent type and
 * derived counts, a table lined those up in columns and matched the way the
 * rest of the platform reads. This file only wires the columns — the
 * grid, header treatment and hover behaviour come from `DataTable`, so no
 * new house style gets introduced.
 */
export default function MyEnvironmentsTable({ envs, onOpen }) {
  const { state, dispatch } = useSimStore();
  const navigate = useNavigate();

  /*
    Two lightweight local states cover the row-level actions: which env's
    kebab menu is open, and which env is queued for delete confirmation.
    Kept up here instead of on each row so opening a menu doesn't rerender
    every other row's cells.
  */
  const [menuFor, setMenuFor] = useState(null); /* { env, anchorEl } */
  const [confirmDelete, setConfirmDelete] = useState(null); /* env */

  const runSimulation = (env) => {
    navigate(paths.dashboard.simulate.simulationRun(env.id, `run-${Date.now().toString(36)}`));
  };
  const deleteEnv = (env) => {
    dispatch({ type: "removeEnvironment", envId: env.id });
    setConfirmDelete(null);
  };

  /*
    Flatten envState-derived counts into each row up front, so column
    accessors stay simple and MUI X can sort on them without a valueGetter.
  */
  const rows = useMemo(
    () => envs.map((env) => {
      const envState = state.byEnv[env.id];
      const scenarios = envState?.scenarios || [];
      const runs = envState?.runs || [];
      return {
        id: env.id,
        env,
        name: env.name,
        description: env.description || env.tagline || "",
        status: env.buildStatus || "ready",
        buildProgress: env.buildProgress,
        agentType: env.agentType,
        tools: env.tools?.length || 0,
        subgoals: scenarios.reduce(
          (n, s) => n + (s.subTasks?.length ?? subTasksFor(s, env).length),
          0,
        ),
        scenarios: scenarios.length,
        runsPassed: runs.filter((r) => r.status === "passed" || r.gate === "clear").length,
        runsTotal: runs.length,
        updatedAt: env.adoptedAt,
        /* Twin backing flag + service count for the inline chip on the
           name column. Lets scanning the gallery tell you at a glance
           which envs are twin-backed vs mocked-integration. */
        twinBacking: envState?.twinBacking || null,
      };
    }),
    [envs, state.byEnv],
  );

  const columns = useMemo(
    () => [
      {
        id: "name",
        accessorKey: "name",
        header: "Name",
        meta: { flex: 1 },
        minSize: 180,
        cell: ({ getValue, row }) => {
          const twin = row.original?.twinBacking;
          return (
            <Stack direction="row" alignItems="center" spacing={0.75} sx={{ minWidth: 0 }}>
              <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
                {getValue()}
              </Typography>
              {twin && (
                <Box sx={{
                  display: "inline-flex", alignItems: "center", gap: 0.375,
                  height: 18, px: 0.625, borderRadius: 0.75,
                  bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.18 : 0.1),
                  color: "#7857FC",
                  border: "1px solid", borderColor: alpha("#7857FC", 0.35),
                  flexShrink: 0,
                }}>
                  <Iconify icon="solar:server-square-linear" width={10} />
                  <Typography sx={{ typography: "s3", fontWeight: 700, letterSpacing: 0.4 }}>
                    CLONE · {twin.services?.length || 0}
                  </Typography>
                </Box>
              )}
            </Stack>
          );
        },
      },
      {
        id: "description",
        accessorKey: "description",
        header: "Description",
        meta: { flex: 1.6 },
        minSize: 240,
        enableSorting: false,
        cell: ({ getValue }) => (
          <Typography noWrap sx={{ typography: "s2", color: "text.secondary" }}>
            {getValue() || "—"}
          </Typography>
        ),
      },
      {
        id: "status",
        accessorKey: "status",
        header: "Status",
        size: 130,
        cell: ({ getValue, row }) => (
          <StatusPill status={getValue()} progress={row.original.buildProgress} />
        ),
      },
      {
        id: "agentType",
        accessorKey: "agentType",
        header: "Agent type",
        size: 180,
        cell: ({ getValue }) => {
          const at = getAgentType(getValue());
          if (!at) return <Typography sx={{ typography: "s2", color: "text.subtitle" }}>—</Typography>;
          return (
            <Stack direction="row" alignItems="center" spacing={0.875} sx={{ minWidth: 0 }}>
              <Box
                sx={{
                  width: 22, height: 22, borderRadius: 0.75, flexShrink: 0,
                  display: "grid", placeItems: "center",
                  bgcolor: "background.neutral", color: "text.secondary",
                }}
              >
                <Iconify icon={at.icon} width={13} />
              </Box>
              <Typography noWrap sx={{ typography: "s2" }}>{at.label}</Typography>
            </Stack>
          );
        },
      },
      {
        id: "tools",
        accessorKey: "tools",
        header: "Tools",
        size: 90,
        cell: ({ getValue }) => (
          <Typography sx={{ typography: "s2", color: "text.secondary", fontVariantNumeric: "tabular-nums" }}>
            {getValue()}
          </Typography>
        ),
      },
      {
        id: "subgoals",
        accessorKey: "subgoals",
        header: "Sub-goals",
        size: 110,
        cell: ({ getValue }) => (
          <Typography sx={{ typography: "s2", color: "text.secondary", fontVariantNumeric: "tabular-nums" }}>
            {getValue()}
          </Typography>
        ),
      },
      {
        id: "scenarios",
        accessorKey: "scenarios",
        header: "Scenarios",
        size: 110,
        cell: ({ getValue }) => (
          <Typography sx={{ typography: "s2", color: "text.secondary", fontVariantNumeric: "tabular-nums" }}>
            {getValue()}
          </Typography>
        ),
      },
      {
        id: "runs",
        accessorKey: "runsTotal",
        header: "Runs",
        size: 100,
        cell: ({ row }) => (
          <RunsPill passed={row.original.runsPassed} total={row.original.runsTotal} />
        ),
      },
      {
        id: "updated",
        accessorKey: "updatedAt",
        header: "Updated",
        size: 130,
        cell: ({ getValue }) => (
          <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
            {relativeTime(getValue())}
          </Typography>
        ),
      },
      {
        /*
          Actions column. A kebab rather than inline icons — the
          Building/Ready state only unlocks Re-run once, so a two-icon row
          would need a disabled/tooltip dance to explain why one is grey.
          A menu carries labels naturally, keeps the row calm, and matches
          the row-actions pattern the rest of the app uses.
        */
        id: "actions",
        accessorKey: "id",
        header: "",
        size: 56,
        enableSorting: false,
        cell: ({ row }) => (
          <IconButton
            size="small"
            aria-label="Row actions"
            onClick={(e) => {
              e.stopPropagation();
              setMenuFor({ env: row.original.env, anchorEl: e.currentTarget });
            }}
            sx={{ color: "text.subtitle" }}
          >
            <Iconify icon="solar:menu-dots-bold" width={16} />
          </IconButton>
        ),
      },
    ],
    [],
  );

  /*
    No wrapping border, no rounded container — matches the Evals / Datasets
    / Agents pages, which render `DataTable` flush against the page so the
    header row and the surrounding filters read as one surface.

    DataTable's outer div is `height: 100%`, so the wrapper Box has to give
    it a determinate height. Rows are unpaginated here (env count is small),
    so the height is derived from the row count; once this grows past a
    screenful the same DataTable takes `DataTablePagination` underneath
    without changing anything above.
  */
  const bodyHeight = Math.min(640, 40 + Math.max(rows.length, 1) * 52 + 12);
  const active = menuFor?.env;
  const buildingActive = active?.buildStatus === "building";

  return (
    <>
      <Box sx={{ height: bodyHeight, minHeight: 0 }}>
        <DataTable
          columns={columns}
          data={rows}
          rowCount={rows.length}
          getRowId={(row) => row.id}
          onRowClick={(row) => onOpen?.(row.env)}
          rowHeight={52}
          emptyMessage="No environments yet"
        />
      </Box>

      {/*
        Row action menu. Menu items are kept short and named the same way
        the header buttons are — "Open" / "Run simulation" / "Delete" — so
        someone jumping from the row into the workspace lands on labels
        that already read the same.
      */}
      <Menu
        anchorEl={menuFor?.anchorEl}
        open={!!menuFor}
        onClose={() => setMenuFor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { minWidth: 200 } } }}
      >
        <MenuItem
          onClick={() => { onOpen?.(active); setMenuFor(null); }}
          sx={{ typography: "s2" }}
        >
          <ListItemIcon sx={{ minWidth: 28 }}>
            <Iconify icon="solar:arrow-right-linear" width={16} />
          </ListItemIcon>
          <ListItemText primary="Open" primaryTypographyProps={{ sx: { typography: "s2" } }} />
        </MenuItem>
        <Tooltip
          arrow placement="left"
          title={buildingActive ? "Wait for setup to finish" : ""}
        >
          <Box>
            <MenuItem
              disabled={buildingActive}
              onClick={() => { runSimulation(active); setMenuFor(null); }}
              sx={{ typography: "s2" }}
            >
              <ListItemIcon sx={{ minWidth: 28 }}>
                <Iconify icon="solar:play-bold" width={16} />
              </ListItemIcon>
              <ListItemText
                primary={(active?.buildStatus === "ready" && (state.byEnv[active?.id]?.runs?.length || 0) > 0)
                  ? "Re-run simulation"
                  : "Run simulation"}
                primaryTypographyProps={{ sx: { typography: "s2" } }}
              />
            </MenuItem>
          </Box>
        </Tooltip>
        <MenuItem
          onClick={() => { setConfirmDelete(active); setMenuFor(null); }}
          sx={{ typography: "s2", color: "#DC2626" }}
        >
          <ListItemIcon sx={{ minWidth: 28, color: "#DC2626" }}>
            <Iconify icon="solar:trash-bin-trash-linear" width={16} />
          </ListItemIcon>
          <ListItemText primary="Delete" primaryTypographyProps={{ sx: { typography: "s2", color: "#DC2626" } }} />
        </MenuItem>
      </Menu>

      {/*
        Confirmation before dropping — a delete strips the env AND its
        scenarios / evals / runs from the store (the reducer's own doing),
        so a misclick would silently lose the derivation work.
      */}
      <Dialog open={!!confirmDelete} onClose={() => setConfirmDelete(null)} maxWidth="xs" fullWidth>
        <DialogTitle sx={{ typography: "m2", fontWeight: 700 }}>
          Delete environment?
        </DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ typography: "s2" }}>
            <b>{confirmDelete?.name}</b> and everything derived from it —
            scenarios, personas, evals and run history — will be removed
            from this workspace. This cannot be undone.
          </DialogContentText>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button
            onClick={() => setConfirmDelete(null)}
            sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}
          >
            Cancel
          </Button>
          <Button
            variant="contained"
            onClick={() => deleteEnv(confirmDelete)}
            sx={{
              typography: "s2", fontWeight: 700,
              bgcolor: "#DC2626",
              "&:hover": { bgcolor: "#B91C1C" },
            }}
          >
            Delete
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
}

MyEnvironmentsTable.propTypes = {
  envs: PropTypes.array.isRequired,
  onOpen: PropTypes.func,
};

/* ── status pill ─────────────────────────────────────────────────────────── */

const pulse = keyframes`
  0%,100% { opacity: 0.55; }
  50%     { opacity: 1; }
`;

const STATUS_META = {
  building: { label: "Building", color: "#7857FC" },
  ready:    { label: "Ready",    color: "#16A34A" },
  failed:   { label: "Failed",   color: "#DC2626" },
};

function StatusPill({ status, progress }) {
  const meta = STATUS_META[status] || STATUS_META.ready;
  const detail = status === "building" && progress
    ? `${progress.done}/${progress.total} steps`
    : "";

  return (
    <Tooltip arrow title={detail}>
      <Stack
        direction="row" alignItems="center" spacing={0.75}
        sx={{
          display: "inline-flex",
          px: 0.875, py: 0.375, borderRadius: 999,
          border: "1px solid",
          borderColor: alpha(meta.color, 0.35),
          bgcolor: (t) => alpha(meta.color, t.palette.mode === "dark" ? 0.14 : 0.09),
        }}
      >
        <Box
          sx={{
            width: 6, height: 6, borderRadius: "50%", bgcolor: meta.color,
            animation: status === "building" ? `${pulse} 1.4s ease-in-out infinite` : "none",
          }}
        />
        <Typography sx={{ typography: "s3", fontWeight: 700, color: meta.color }}>
          {meta.label}
        </Typography>
      </Stack>
    </Tooltip>
  );
}

StatusPill.propTypes = { status: PropTypes.string, progress: PropTypes.object };

/* ── runs pill ───────────────────────────────────────────────────────────── */

function RunsPill({ passed, total }) {
  const empty = !total;
  const bad = total > 0 && passed === 0;
  const good = total > 0 && passed === total;
  const tone = bad ? "#DC2626" : good ? "#16A34A" : "text.subtitle";

  return (
    <Box
      sx={{
        display: "inline-flex", alignItems: "center",
        px: 0.75, py: 0.25, borderRadius: 0.75,
        border: "1px solid",
        borderColor: empty ? "divider" : alpha(bad ? "#DC2626" : good ? "#16A34A" : "#94A3B8", 0.5),
        color: tone,
      }}
    >
      <Typography sx={{ typography: "s3", fontWeight: 700, fontVariantNumeric: "tabular-nums", color: tone }}>
        {passed}/{total}
      </Typography>
    </Box>
  );
}

RunsPill.propTypes = { passed: PropTypes.number, total: PropTypes.number };

/* ── relative time ───────────────────────────────────────────────────────── */

function relativeTime(iso) {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const diffMs = Date.now() - then;
  const minutes = Math.floor(diffMs / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} ${minutes === 1 ? "minute" : "minutes"} ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} ${hours === 1 ? "hour" : "hours"} ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} ${days === 1 ? "day" : "days"} ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months} ${months === 1 ? "month" : "months"} ago`;
  const years = Math.floor(months / 12);
  return `${years} ${years === 1 ? "year" : "years"} ago`;
}
