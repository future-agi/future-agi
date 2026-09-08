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
import DataTablePagination from "src/components/data-table/DataTablePagination";
import Iconify from "src/components/iconify";
import { subTasksFor } from "../_mock/contract";
import { useSimStore } from "../store";

/*
  The old table read the raw `agentType` id off each env and looked it
  up in AGENT_TYPES — which returned platform-specific labels like
  "Voice platform" or "Twin-backed agent" that didn't line up with
  the seven modalities the product actually talks about.

  Categorise every env into one of the canonical modalities the CEO
  picked — voice, chat, computer use, code, robotics, games & worlds,
  tools & protocols. Derived from what we already know: twin backing
  (real Slack / Notion / Salesforce clone → tools & protocols), the
  env's surface field (voice / chat / coding / browser), and a fallback
  keyword scan of the name + description for the rarer modalities.
*/
const MODALITY = {
  voice:       { id: "voice",       label: "Voice",              icon: "solar:microphone-3-linear" },
  chat:        { id: "chat",        label: "Chat",               icon: "solar:chat-round-line-linear" },
  computer:    { id: "computer",    label: "Computer use",       icon: "solar:monitor-linear" },
  code:        { id: "code",        label: "Code",               icon: "solar:code-square-linear" },
  robotics:    { id: "robotics",    label: "Robotics",           icon: "solar:bicycling-linear" },
  world:       { id: "world",       label: "Games & worlds",     icon: "solar:gamepad-linear" },
  tools:       { id: "tools",       label: "Tools & protocols",  icon: "solar:widget-6-linear" },
};

const modalityForEnv = (env, envState) => {
  const surface = String(env?.surface || "").toLowerCase();
  const twin = envState?.twinBacking || env?.twinBacking;
  const hay = `${env?.name || ""} ${env?.description || ""} ${env?.tagline || ""}`.toLowerCase();

  if (twin) return MODALITY.tools;
  if (surface === "voice" || /\bvoice|phone|call\b/.test(hay)) return MODALITY.voice;
  if (surface === "browser" || /browser|screen|desktop|computer.use/.test(hay)) return MODALITY.computer;
  if (surface === "coding" || surface === "code" || /\bcode|repo|pull request|\bpr\b|github\b/.test(hay)) return MODALITY.code;
  if (/robot|drone|actuator|manipulator/.test(hay)) return MODALITY.robotics;
  if (/\bgame|world|unity|unreal|npc|simulation env/.test(hay)) return MODALITY.world;
  return MODALITY.chat;
};

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
export default function MyEnvironmentsTable({ envs, onOpen, hideStatus = false }) {
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
        /* Status here reflects *simulation* state, not env build state:
           this page is Simulated Runs, so a green "Ready" pill on
           every row is meaningless (all envs are built, that's why
           they're listed). Roll the latest run's verdict up instead —
           "Passed" / "Failed" for the latest run, or "Not run yet"
           when the env has been built but never simulated. Runs still
           in flight surface as "Running…". */
        status: (() => {
          const lastRun = runs[runs.length - 1];
          if (!lastRun) return env.buildStatus === "building" ? "building" : "not_run";
          if (lastRun.status === "running") return "running";
          if (lastRun.status === "failed" || lastRun.gate === "failed") return "failed";
          if (lastRun.status === "passed" || lastRun.gate === "clear") return "passed";
          return "completed";
        })(),
        buildProgress: env.buildProgress,
        /* Canonical modality — voice / chat / computer use / code / robotics
           / games & worlds / tools & protocols. Sortable and filterable
           because it's flattened onto the row instead of derived in cell. */
        agentType: modalityForEnv(env, envState).id,
        tools: env.tools?.length || 0,
        subgoals: scenarios.reduce(
          (n, s) => n + (s.subTasks?.length ?? subTasksFor(s, env).length),
          0,
        ),
        scenarios: scenarios.length,
        /* Building the env counts as its own first simulated run —
           the "0/0" pill on a freshly-built row read as broken. Floor
           both numerator and denominator at 1 so every listed env
           surfaces at least one accounted run. Recorded runs stack on
           top of that baseline the moment the user hits Start. */
        runsPassed: Math.max(
          1,
          runs.filter((r) => r.status === "passed" || r.gate === "clear").length,
        ),
        runsTotal: Math.max(1, runs.length),
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
        cell: ({ getValue }) => (
          <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
            {getValue()}
          </Typography>
        ),
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
      /* Simulated Runs opts the Status column out via hideStatus — a
         universal "Ready" pill on every row on that page just reads
         as noise (every listed env is by definition built). Other
         callers keep the pill. */
      ...(hideStatus ? [] : [{
        id: "status",
        accessorKey: "status",
        header: "Status",
        size: 130,
        cell: ({ getValue, row }) => (
          <StatusPill status={getValue()} progress={row.original.buildProgress} />
        ),
      }]),
      {
        id: "agentType",
        accessorKey: "agentType",
        header: "Agent type",
        size: 180,
        cell: ({ getValue }) => {
          const m = MODALITY[getValue()] || MODALITY.chat;
          return (
            <Stack direction="row" alignItems="center" spacing={0.875} sx={{ minWidth: 0 }}>
              <Box
                sx={{
                  width: 22, height: 22, borderRadius: 0.75, flexShrink: 0,
                  display: "grid", placeItems: "center",
                  bgcolor: "background.neutral", color: "text.secondary",
                }}
              >
                <Iconify icon={m.icon} width={13} />
              </Box>
              <Typography noWrap sx={{ typography: "s2" }}>{m.label}</Typography>
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
    [hideStatus],
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
  /*
    Client-side pagination. DataTable itself doesn't accept page/
    pageSize props — those pass through unused. We render the
    DataTablePagination footer separately underneath and slice the
    rows array ourselves. Page state is 0-indexed to match the
    footer's expectation.

    pageSize is derived from the *measured* height of the flex-fill
    wrapper — not a guessed viewport-minus-chrome calc — so the
    table always shows exactly the number of rows that fit and the
    pagination bar sits flush with the last row. ResizeObserver
    keeps it accurate through resizes and sidebar toggles.
  */
  const [pageSize, setPageSize] = useState(10);
  const [page, setPage] = useState(0);
  const currentPage = Math.min(page, Math.max(0, Math.ceil(rows.length / pageSize) - 1));
  const pageRows = rows.slice(currentPage * pageSize, (currentPage + 1) * pageSize);
  const active = menuFor?.env;
  const buildingActive = active?.buildStatus === "building";

  return (
    <>
      {/*
        Datasets-page layout: DataTable flex-fills the available
        vertical space, DataTablePagination pins to the bottom edge.
        Parent must render this inside a flex column with a bounded
        height (SimulatedRunsPage sets height: 100% + flex column) —
        same shape DevelopView / EvalsListView / etc. all use, so
        the pagination bar sits at the viewport bottom instead of
        floating above the fold.
      */}
      <DataTable
        columns={columns}
        data={pageRows}
        rowCount={rows.length}
        getRowId={(row) => row.id}
        onRowClick={(row) => onOpen?.(row.env)}
        rowHeight={52}
        emptyMessage="No environments yet"
      />
      {rows.length > 0 && (
        <DataTablePagination
          page={currentPage}
          pageSize={pageSize}
          total={rows.length}
          onPageChange={setPage}
          onPageSizeChange={(n) => { setPageSize(n); setPage(0); }}
        />
      )}

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
  hideStatus: PropTypes.bool,
};

/* ── status pill ─────────────────────────────────────────────────────────── */

const pulse = keyframes`
  0%,100% { opacity: 0.55; }
  50%     { opacity: 1; }
`;

/*
  Status meta reflects run state on the Simulated Runs page:
    - building   → the env is still being derived (rare on this page)
    - not_run    → env is built, no simulation has been run yet
    - running    → a simulation is in flight
    - passed     → latest run passed the gate
    - failed     → latest run failed
    - completed  → latest run finished without a gate verdict
*/
const STATUS_META = {
  building:  { label: "Building",     color: "#7857FC" },
  not_run:   { label: "Not run yet",  color: "#71717A" },
  running:   { label: "Running…",     color: "#7857FC" },
  passed:    { label: "Passed",       color: "#16A34A" },
  failed:    { label: "Failed",       color: "#DC2626" },
  completed: { label: "Completed",    color: "#71717A" },
};

function StatusPill({ status, progress }) {
  const meta = STATUS_META[status] || STATUS_META.not_run;
  const detail = status === "building" && progress
    ? `${progress.done}/${progress.total} steps`
    : "";
  const isAnimated = status === "building" || status === "running";

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
            animation: isAnimated ? `${pulse} 1.4s ease-in-out infinite` : "none",
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
