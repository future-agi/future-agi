import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, TextField, InputAdornment, Chip,
  Checkbox, Tooltip,
  Dialog, DialogTitle, DialogContent, DialogContentText, DialogActions, Button,
} from "@mui/material";
import { neutralCheckboxSx } from "../components/primitives";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { DataTable } from "src/components/data-table";
import DataTablePagination from "src/components/data-table/DataTablePagination";
import FilterPanel from "src/components/filter-panel/FilterPanel";
import { useSimStore } from "../store";
import { runSummaries, trialSummaries } from "../_mock/comparison";
import { DATASETS } from "../_mock/datasets";

/**
 * Improvements — level 1 · workspace-wide runs, one row per source.
 *
 * A source is either an environment (its runs + self-improvement
 * trials) or a dataset (its optimization trials). Row shape is the
 * same across sources so the reader scans one table.
 *
 * Clicking a row opens level 2 — a run-scoped lens on that source,
 * showing the same RunsSummary UI the env's own Runs tab shows. Any
 * runs added, evals edited or winners chosen there are live-shared
 * with the env store; Improvements is a lens, not a snapshot.
 */

/* Filter/search shape borrowed from the Environments gallery — same
   input height + typography, so this page reads as part of the same
   family. */
const compactInputSx = {
  "& .MuiInputBase-root": { typography: "s2", height: 34 },
  "& .MuiOutlinedInput-input": { py: 0 },
};

/* Agent-type modality resolver, borrowed from the Environments list so
   Improvements labels a source the same way the workspace does. */
const MODALITY = {
  voice:    { id: "voice",    label: "Voice",         icon: "solar:microphone-3-linear" },
  chat:     { id: "chat",     label: "Chat",          icon: "solar:chat-round-line-linear" },
  computer: { id: "computer", label: "Computer use",  icon: "solar:monitor-linear" },
  code:     { id: "code",     label: "Code",          icon: "solar:code-square-linear" },
  tools:    { id: "tools",    label: "Tools",         icon: "solar:widget-6-linear" },
};
function modalityForEnv(env, envState) {
  const surface = String(env?.surface || "").toLowerCase();
  const twin = envState?.twinBacking || env?.twinBacking;
  const hay = `${env?.name || ""} ${env?.description || ""} ${env?.tagline || ""}`.toLowerCase();
  if (twin) return MODALITY.tools;
  if (surface === "voice" || /\bvoice|phone|call\b/.test(hay)) return MODALITY.voice;
  if (surface === "browser" || /browser|screen|desktop|computer.use/.test(hay)) return MODALITY.computer;
  if (surface === "coding" || surface === "code" || /\bcode|repo|pull request|\bpr\b|github\b/.test(hay)) return MODALITY.code;
  return MODALITY.chat;
}

/* Three-state status, latest-run based. Labels match prod (Scenarios
   page uses Completed / Failed / Processing) so the meaning is
   consistent across surfaces. */
const STATUS_META = {
  running: {
    label: "Running…",
    color: "#2563EB",
    bg: (t) => alpha("#2563EB", t.palette.mode === "dark" ? 0.16 : 0.1),
    animated: true,
  },
  completed: {
    label: "Completed",
    color: "#16A34A",
    bg: (t) => alpha("#16A34A", t.palette.mode === "dark" ? 0.16 : 0.1),
  },
  failed: {
    label: "Failed",
    color: "#DC2626",
    bg: (t) => alpha("#DC2626", t.palette.mode === "dark" ? 0.16 : 0.1),
  },
};

/* Prod semantics: Completed means the run FINISHED, not that every task
   passed. Failed means the run itself errored out. Pass rate lives in
   its own column and shouldn't leak into the status chip. */
function statusFrom(latest) {
  if (!latest) return null;
  if (latest.status === "running") return "running";
  if (latest.status === "error" || latest.status === "failed") return "failed";
  if (latest.finishedAt) return "completed";
  return null;
}

/* Dataset-source seeds. Datasets don't run against an env, so their
   "runs" come from scripted optimization trials. Two seeds carry
   enough variety for the demo without swamping the env-source rows. */
const DAY = 24 * 60 * 60 * 1000;
const iso = (offsetDays) => new Date(Date.now() - offsetDays * DAY).toISOString();
const DATASET_SEEDS = [
  {
    id: "ds-support-transcripts",
    datasetId: "ds-support-transcripts",
    createdAt: iso(4),
    runCount: 4,
    /* Datasets don't own an agent — but a run against a dataset does.
       The modality here is the agent modality the run used, seeded so
       the row's Agent type column reflects a real value. */
    modalityKey: "chat",
    latestStatus: "completed",
  },
  {
    id: "ds-refund-appeals",
    datasetId: "ds-refund-appeals",
    createdAt: iso(11),
    runCount: 3,
    modalityKey: "voice",
    latestStatus: "failed",
  },
];

/* ── the view ────────────────────────────────────────────────────────── */

export default function ImprovementsList() {
  const navigate = useNavigate();
  const { state, dispatch } = useSimStore();
  const [query, setQuery] = useState("");
  /* Multi-select for bulk delete. Selection lives outside the table
     so the confirm dialog can reach the checked rows. */
  const [selectedIds, setSelectedIds] = useState([]);
  const [confirmBulkDelete, setConfirmBulkDelete] = useState(false);
  const [hiddenDatasets, setHiddenDatasets] = useState(() => new Set());
  /* Pagination — same pattern MyEnvironmentsTable uses. Client-side
     slice because DataTable doesn't handle paging internally. */
  const [pageSize, setPageSize] = useState(25);
  const [page, setPage] = useState(0);
  /* Filter panel — mirrors the Evaluations page. FilterPanel returns
     either { field: [values] } (Basic tab) or [{ field, operator, value }]
     (Query tab); we flatten both to { field: [values] } here. */
  const [filterAnchorEl, setFilterAnchorEl] = useState(null);
  const [filters, setFilters] = useState({});
  const activeFilterCount = Object.values(filters).reduce(
    (s, v) => s + (Array.isArray(v) ? v.length : 0), 0,
  );
  const applyFilters = (result) => {
    if (!result) { setFilters({}); setPage(0); return; }
    if (Array.isArray(result)) {
      const flat = {};
      result.forEach((r) => {
        const values = Array.isArray(r.value) ? r.value : (r.value != null ? [r.value] : []);
        if (!values.length) return;
        flat[r.field] = [...(flat[r.field] || []), ...values];
      });
      setFilters(flat);
    } else {
      setFilters(result);
    }
    setPage(0);
  };

  const clearSelection = () => setSelectedIds([]);
  const toggleRow = (id) => setSelectedIds((prev) => (
    prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
  ));

  /* Bulk delete: envs hit the store; dataset seeds are hidden locally. */
  const performBulkDelete = () => {
    selectedIds.forEach((compositeId) => {
      const [kind, rawId] = compositeId.split(":");
      if (kind === "env") {
        dispatch({ type: "removeEnvironment", envId: rawId });
      } else if (kind === "dataset") {
        setHiddenDatasets((prev) => {
          const next = new Set(prev);
          next.add(rawId);
          return next;
        });
      }
    });
    setSelectedIds([]);
    setConfirmBulkDelete(false);
  };

  const rows = useMemo(() => {
    const envRows = (state.myEnvironments || []).map((env) => {
      const es = state.byEnv?.[env.id];
      if (!es) return null;
      const runs = runSummaries(env, es).filter((r) => !r.synthetic);
      const trials = trialSummaries(env, es).slice(0, 8);
      const total = runs.length + trials.length;
      /* Improvements only shows sources that have at least one run.
         An env with no runs has nothing to say here — it belongs on
         the Environments page, not this one. */
      if (total === 0) return null;
      /* Latest run for status. Prefer an in-flight run so a Running…
         chip actually appears while the run is live; otherwise take
         the newest by finishedAt. */
      const combined = [...runs, ...trials];
      const inFlight = combined.find((r) => r.status === "running");
      const latest = inFlight
        || combined.filter((r) => r.finishedAt)
          .sort((a, b) => new Date(b.finishedAt) - new Date(a.finishedAt))[0];
      return {
        id: `env:${env.id}`,
        rawId: env.id,
        sourceKind: "env",
        sourceLabel: "Environment",
        sourceIcon: "solar:test-tube-linear",
        name: env.name,
        modality: modalityForEnv(env, es),
        createdAt: env.adoptedAt || es.createdAt || latest?.finishedAt || null,
        latestRunAt: inFlight ? null : latest?.finishedAt || null,
        latestIsRunning: !!inFlight,
        runCount: total,
        statusKey: statusFrom(latest),
      };
    }).filter(Boolean);

    const datasetRows = DATASET_SEEDS.filter((ds) => !hiddenDatasets.has(ds.id)).map((ds) => {
      const dataset = DATASETS.find((d) => d.id === ds.datasetId);
      /* Datasets are scripted for the demo — synthesize a `latest`
         shape so statusFrom reads them the same way. */
      const latest = {
        finishedAt: ds.createdAt,
        status: ds.latestStatus,
      };
      return {
        id: `dataset:${ds.id}`,
        rawId: ds.id,
        sourceKind: "dataset",
        sourceLabel: "Dataset",
        sourceIcon: "solar:database-linear",
        name: dataset?.name || ds.id,
        modality: MODALITY[ds.modalityKey] || MODALITY.chat,
        createdAt: ds.createdAt,
        latestRunAt: ds.createdAt,
        latestIsRunning: false,
        runCount: ds.runCount,
        statusKey: statusFrom(latest),
      };
    });

    return [...envRows, ...datasetRows];
  }, [state.myEnvironments, state.byEnv, hiddenDatasets]);

  /* Filter fields — Source, Agent type, Status. Choices are derived
     from the rows so the panel only offers values that actually exist. */
  const filterFields = useMemo(() => {
    const sourceChoices = [...new Set(rows.map((r) => r.sourceLabel))].sort();
    const agentChoices = [...new Set(rows.map((r) => r.modality?.label).filter(Boolean))].sort();
    const statusChoices = [...new Set(rows.map((r) => STATUS_META[r.statusKey]?.label).filter(Boolean))].sort();
    return [
      { value: "source", label: "Source", type: "enum", choices: sourceChoices },
      { value: "agentType", label: "Agent type", type: "enum", choices: agentChoices },
      { value: "status", label: "Status", type: "enum", choices: statusChoices },
    ];
  }, [rows]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return rows.filter((r) => {
      if (q && !(r.name.toLowerCase().includes(q) || r.sourceLabel.toLowerCase().includes(q))) return false;
      if (filters.source?.length && !filters.source.includes(r.sourceLabel)) return false;
      if (filters.agentType?.length && !filters.agentType.includes(r.modality?.label)) return false;
      if (filters.status?.length && !filters.status.includes(STATUS_META[r.statusKey]?.label)) return false;
      return true;
    });
  }, [rows, query, filters]);

  const openRow = (row) => {
    if (row.sourceKind === "env") {
      navigate(paths.dashboard.simulate.improvementEnvRuns(row.rawId));
    } else {
      // eslint-disable-next-line no-alert
      alert("Dataset optimizations detail is coming soon.");
    }
  };

  const columns = useMemo(() => [
    {
      id: "select",
      header: () => null,
      size: 44,
      enableSorting: false,
      cell: ({ row }) => {
        const id = row.original.id;
        return (
          <Checkbox
            size="small"
            checked={selectedIds.includes(id)}
            onChange={(e) => { e.stopPropagation(); toggleRow(id); }}
            onClick={(e) => e.stopPropagation()}
            sx={{ p: 0, ...neutralCheckboxSx }}
          />
        );
      },
    },
    {
      id: "name",
      accessorKey: "name",
      header: "Run name",
      meta: { flex: 1.6 },
      minSize: 240,
      cell: ({ row }) => (
        <Typography noWrap sx={{ typography: "s2", fontWeight: 600, minWidth: 0 }}>
          {row.original.name}
        </Typography>
      ),
    },
    {
      id: "source",
      accessorKey: "sourceLabel",
      header: "Source",
      size: 170,
      cell: ({ row }) => (
        /* Exact shape of the Scenarios list's ChipCell — thin bordered
           rectangle (small corner radius, not pill), transparent fill,
           icon in text-primary, caption text at 500 weight. */
        <Box
          sx={{
            display: "inline-flex", alignItems: "center", gap: 0.5,
            border: "1px solid", borderColor: "divider",
            borderRadius: 0.25,
            px: 1.5, py: 0.25,
          }}
        >
          <Iconify icon={row.original.sourceIcon} width={14} sx={{ color: "text.primary" }} />
          <Typography variant="caption" sx={{ fontWeight: 500 }}>
            {row.original.sourceLabel}
          </Typography>
        </Box>
      ),
    },
    {
      id: "agentType",
      accessorKey: "modality",
      header: "Agent type",
      size: 170,
      cell: ({ row }) => {
        const m = row.original.modality;
        if (!m) {
          return <Typography sx={{ typography: "s3", color: "text.disabled" }}>—</Typography>;
        }
        /* Same visual as the Environments list's Agent-type column —
           neutral rounded-square icon badge next to the label. */
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
      id: "status",
      accessorKey: "statusKey",
      header: "Status",
      size: 140,
      cell: ({ getValue }) => <StatusPill status={getValue()} />,
    },
    {
      id: "runs",
      accessorKey: "runCount",
      header: "Runs",
      size: 90,
      cell: ({ getValue }) => (
        <Box
          sx={{
            display: "inline-flex", alignItems: "center",
            px: 0.75, py: 0.25, borderRadius: 0.75,
            border: "1px solid", borderColor: "divider",
          }}
        >
          <Typography sx={{ typography: "s3", fontWeight: 700, fontVariantNumeric: "tabular-nums", color: "text.secondary" }}>
            {getValue()}
          </Typography>
        </Box>
      ),
    },
    {
      id: "createdAt",
      accessorKey: "createdAt",
      header: "Created",
      size: 170,
      cell: ({ getValue }) => (
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {formatCreatedAt(getValue())}
        </Typography>
      ),
    },
    {
      id: "latestRun",
      accessorKey: "latestRunAt",
      header: "Latest run",
      size: 150,
      cell: ({ row }) => {
        if (row.original.latestIsRunning) {
          return (
            <Typography sx={{ typography: "s3", color: "text.secondary", fontWeight: 600 }}>
              in progress
            </Typography>
          );
        }
        return (
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {relativeTime(row.original.latestRunAt)}
          </Typography>
        );
      },
    },
  ], [selectedIds]);

  return (
    <Box sx={{
      height: "100%", display: "flex", flexDirection: "column",
      gap: 2, overflow: "hidden", minHeight: 0, p: 2,
    }}>
      {/* header */}
      <Box sx={{ flexShrink: 0 }}>
        <Typography sx={{ typography: "m2", fontWeight: 600 }}>Improvements</Typography>
        <Typography sx={{ typography: "s1", color: "text.secondary" }}>
          Every self improvement runs across your environments and datasets.
        </Typography>
      </Box>

      {/* search + filter + primary action inline. Filter button styled
          the same way the Evaluations page does (mage:filter icon + count
          suffix + primary tint when active). */}
      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ flexShrink: 0 }}>
        <TextField
          size="small"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by name or source"
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Iconify icon="solar:magnifer-linear" width={15} sx={{ color: "text.subtitle" }} />
              </InputAdornment>
            ),
          }}
          sx={{ width: 280, ...compactInputSx }}
        />
        <Button
          size="small"
          variant="outlined"
          startIcon={<Iconify icon="mage:filter" width={16} />}
          endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={14} />}
          onClick={(e) => setFilterAnchorEl(e.currentTarget)}
          sx={{
            textTransform: "none",
            fontSize: "13px",
            height: "32px",
            borderColor: activeFilterCount > 0 ? "primary.main" : "divider",
            color: activeFilterCount > 0 ? "primary.main" : "text.secondary",
          }}
        >
          Filter{activeFilterCount > 0 ? ` (${activeFilterCount})` : ""}
        </Button>
        <Box flex={1} />
        <Button
          variant="contained" color="primary" size="small"
          onClick={() => navigate(paths.dashboard.simulate.environments)}
          startIcon={<Iconify icon="solar:play-bold" width={15} />}
          sx={{ typography: "s2", fontWeight: 700, flexShrink: 0 }}
        >
          Run simulation
        </Button>
      </Stack>

      {/* Filter panel — same component the Evaluations page uses. */}
      <FilterPanel
        anchorEl={filterAnchorEl}
        open={Boolean(filterAnchorEl)}
        onClose={() => setFilterAnchorEl(null)}
        filterFields={filterFields}
        currentFilters={filters}
        onApply={applyFilters}
        placement="bottom-start"
      />

      {/* Bulk-action bar — appears once anything is checked. Delete
          fires the confirm dialog; Clear drops the selection. */}
      {selectedIds.length > 0 && (
        <Stack
          direction="row" alignItems="center" spacing={1.5}
          sx={{
            flexShrink: 0,
            px: 2, py: 1, borderRadius: 1.5,
            border: "1px solid", borderColor: "divider",
            bgcolor: "background.paper",
          }}
        >
          <Typography sx={{ typography: "s2", color: "text.primary" }}>
            <Box component="span" sx={{ fontWeight: 700 }}>{selectedIds.length}</Box>
            {" "}
            <Box component="span" sx={{ color: "text.secondary" }}>
              {selectedIds.length === 1 ? "row selected" : "rows selected"}
            </Box>
          </Typography>
          <Box flex={1} />
          <Tooltip arrow title="Deselect all">
            <Button
              size="small"
              onClick={clearSelection}
              sx={{
                typography: "s2", fontWeight: 600,
                color: "text.secondary", minWidth: 0, px: 1,
                "&:hover": { color: "text.primary", bgcolor: "transparent" },
              }}
            >
              Clear
            </Button>
          </Tooltip>
          <Button
            size="small"
            onClick={() => setConfirmBulkDelete(true)}
            startIcon={<Iconify icon="solar:trash-bin-trash-linear" width={13} />}
            sx={{
              typography: "s2", fontWeight: 600,
              color: "#DC2626",
              px: 1, minWidth: 0,
              "&:hover": { bgcolor: (t) => alpha("#DC2626", t.palette.mode === "dark" ? 0.12 : 0.06) },
            }}
          >
            Delete
          </Button>
        </Stack>
      )}

      {/* table + pagination — DataTable fills the available vertical
          space and DataTablePagination pins to the bottom, same shape
          MyEnvironmentsTable uses. */}
      <Box sx={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
        <DataTable
          columns={columns}
          data={filtered.slice(
            Math.min(page, Math.max(0, Math.ceil(filtered.length / pageSize) - 1)) * pageSize,
            (Math.min(page, Math.max(0, Math.ceil(filtered.length / pageSize) - 1)) + 1) * pageSize,
          )}
          rowCount={filtered.length}
          getRowId={(row) => row.id}
          onRowClick={(row) => openRow(row)}
          rowHeight={44}
          emptyMessage={
            query
              ? "No sources match your search."
              : "No runs yet. Start a simulation on any environment — it'll show up here."
          }
        />
        {filtered.length > 0 && (
          <DataTablePagination
            page={Math.min(page, Math.max(0, Math.ceil(filtered.length / pageSize) - 1))}
            pageSize={pageSize}
            total={filtered.length}
            onPageChange={setPage}
            onPageSizeChange={(n) => { setPageSize(n); setPage(0); }}
          />
        )}
      </Box>

      {/* Bulk-delete confirmation. Envs get stripped from the store
          (same reducer path the Environments list uses); dataset
          seeds are hidden locally. */}
      <Dialog
        open={confirmBulkDelete}
        onClose={() => setConfirmBulkDelete(false)}
        maxWidth="xs"
        fullWidth
      >
        <DialogTitle sx={{ typography: "m2", fontWeight: 700 }}>
          Delete {selectedIds.length} row{selectedIds.length === 1 ? "" : "s"}?
        </DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ typography: "s2" }}>
            Environments in your selection will be removed from this
            workspace along with their scenarios, evals and run history —
            this cannot be undone. Dataset sources will be hidden from
            Improvements; the underlying dataset stays where it lives.
          </DialogContentText>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button
            onClick={() => setConfirmBulkDelete(false)}
            sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}
          >
            Cancel
          </Button>
          <Button
            variant="contained"
            onClick={performBulkDelete}
            sx={{
              typography: "s2", fontWeight: 700,
              bgcolor: "#DC2626", color: "#fff",
              "&:hover": { bgcolor: "#B91C1C", color: "#fff" },
            }}
          >
            Delete {selectedIds.length}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}

/* ── status pill — mirrors MyEnvironmentsTable's StatusPill ───────────── */

function StatusPill({ status }) {
  if (!status) {
    return <Typography sx={{ typography: "s3", color: "text.disabled" }}>—</Typography>;
  }
  const meta = STATUS_META[status];
  if (!meta) return null;
  /* Outlined pill matching the prod Scenarios chip — visible colored
     border, near-transparent fill, colored text. Higher contrast than
     the soft variant when the row background is dark. */
  return (
    <Chip
      variant="outlined"
      label={meta.label}
      size="small"
      sx={{
        typography: "s3",
        fontWeight: "fontWeightMedium",
        borderColor: meta.color,
        color: meta.color,
        backgroundColor: (t) => alpha(meta.color, t.palette.mode === "dark" ? 0.04 : 0.02),
        pointerEvents: "none",
        animation: meta.animated ? "improvement-status-pulse 1.6s ease-in-out infinite" : "none",
        "@keyframes improvement-status-pulse": {
          "0%,100%": { opacity: 0.75 },
          "50%":     { opacity: 1 },
        },
      }}
    />
  );
}

/* ── helpers ─────────────────────────────────────────────────────────── */

function formatCreatedAt(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    day: "2-digit", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

/* Relative timestamp — same shape the Environments table uses so
   "Latest run" reads the same as "Updated" over there. */
function relativeTime(iso) {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const diff = Date.now() - then;
  const m = Math.round(diff / 60_000);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min${m === 1 ? "" : "s"} ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} hour${h === 1 ? "" : "s"} ago`;
  const d = Math.round(h / 24);
  if (d < 30) return `${d} day${d === 1 ? "" : "s"} ago`;
  const mo = Math.round(d / 30);
  return `${mo} month${mo === 1 ? "" : "s"} ago`;
}
