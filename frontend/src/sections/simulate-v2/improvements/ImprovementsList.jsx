import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, Tooltip, Menu, MenuItem, Checkbox,
  Table, TableHead, TableBody, TableRow, TableCell,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { useSimStore } from "../store";
import {
  IMPROVEMENT_SOURCES,
  flattenImprovements,
  heldOutHeadline,
  trialCount,
  targetLabel,
} from "../_mock/improvements";

/**
 * Global list of every self-improvement run, grouped by the thing they
 * targeted (the environment for Simulation-source, the dataset for
 * Dataset-source). Rows sit under an expandable target header, so
 * "everything I ran against Customer Support Line" reads as one bucket.
 *
 * The visual language is the platform's — same table primitives, header
 * treatment, dividers, hover, and checkbox behaviour as the rest of the
 * product. Grouping isn't a DataGrid feature, so this uses MUI's Table
 * primitives directly rather than the shared `DataTable` wrapper.
 */
export default function ImprovementsList() {
  const navigate = useNavigate();
  const { state } = useSimStore();
  const [sourceFilter, setSourceFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [filterAnchor, setFilterAnchor] = useState(null);
  const [selected, setSelected] = useState(new Set());
  /* `collapsed` doubles as an override: null means "no user action yet, so
     start every group closed"; any Set means the user has toggled at least
     once and we track exact state. First toggle promotes null → Set. */
  const [collapsed, setCollapsed] = useState(null);

  /* Filtered flat rows first. Grouping happens on the filtered set, so a
     source filter of "Dataset" collapses the table to just the Dataset
     groups instead of showing empty Simulation groups. */
  const rows = useMemo(() => {
    const all = flattenImprovements(state);
    return all.filter((r) => {
      if (sourceFilter !== "all" && r.source !== sourceFilter) return false;
      if (statusFilter !== "all" && r.status !== statusFilter) return false;
      return true;
    });
  }, [state, sourceFilter, statusFilter]);

  /* Group by target: envId for Simulation, datasetId for Dataset. Each
     group carries the target label + source label so the header row can
     render without touching the child records. */
  const groups = useMemo(() => groupByTarget(rows), [rows]);

  const selectedRows = rows.filter((r) => selected.has(r.rowId));
  const canCompare = selectedRows.filter((r) => r.source === "simulation").length >= 2;

  const toggleRow = (rowId) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(rowId)) next.delete(rowId); else next.add(rowId);
      return next;
    });
  };

  const toggleGroup = (group) => {
    const childIds = group.children.map((c) => c.rowId);
    const anySelected = childIds.some((id) => selected.has(id));
    setSelected((prev) => {
      const next = new Set(prev);
      if (anySelected) childIds.forEach((id) => next.delete(id));
      else childIds.forEach((id) => next.add(id));
      return next;
    });
  };

  const isCollapsed = (key) => {
    /* No user toggles yet → every group starts closed. Once the user
       has interacted, we track exact state in the Set. */
    if (collapsed === null) return true;
    return collapsed.has(key);
  };

  const toggleCollapse = (key) => {
    setCollapsed((prev) => {
      /* First toggle: seed the Set with every group key so the ones the
         user didn't touch stay closed, then remove the one being opened. */
      if (prev === null) {
        const seed = new Set(groups.map((g) => g.key));
        seed.delete(key);
        return seed;
      }
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  };

  const compare = () => {
    const picks = selectedRows.filter((r) => r.source === "simulation");
    if (picks.length < 2) return;
    const runIds = picks.map((r) => r.result?.winner?.runId || r.id).join(",");
    navigate(`${paths.dashboard.simulate.simulationCompare(picks[0].envId)}?runs=${runIds}`);
  };

  const openSource = (r) => {
    if (r.source === "simulation" && r.envId && r.fromRunId) {
      navigate(paths.dashboard.simulate.simulationRun(r.envId, r.fromRunId));
      return;
    }
    if (r.source === "simulation" && r.envId) {
      navigate(paths.dashboard.simulate.environmentDetail(r.envId));
      return;
    }
    if (r.source === "dataset") navigate("/dashboard/develop");
  };

  return (
    <Box sx={{ p: 2, height: "100%", display: "flex", flexDirection: "column", minHeight: 0, gap: 1.5, overflow: "hidden" }}>
      {/* ── header ── */}
      <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" alignItems={{ sm: "flex-end" }} spacing={2} sx={{ flexShrink: 0 }}>
        <Box>
          <Typography sx={{ typography: "m2", fontWeight: 600 }}>Improvements</Typography>
          <Typography sx={{ typography: "s1", color: "text.secondary" }}>
            Every self-improvement run — open one to see its trials.
          </Typography>
        </Box>
        <Stack direction="row" spacing={1} sx={{ flexShrink: 0 }}>
          <Button
            variant="outlined" size="small"
            onClick={(e) => setFilterAnchor(e.currentTarget)}
            startIcon={<Iconify icon="solar:filter-linear" width={14} />}
            sx={{ typography: "s2", fontWeight: 600, color: "text.primary", borderColor: "divider" }}
          >
            Filter
            {(sourceFilter !== "all" || statusFilter !== "all") && (
              <Box component="span" sx={{ ml: 0.5, color: "primary.main", fontWeight: 700 }}>
                · {(sourceFilter !== "all" ? 1 : 0) + (statusFilter !== "all" ? 1 : 0)}
              </Box>
            )}
          </Button>
          <Tooltip title={canCompare ? "" : "Pick two or more Simulation-source rows to compare"} arrow>
            <span>
              <Button
                variant="contained" color="primary" size="small"
                disabled={!canCompare}
                onClick={compare}
                startIcon={<Iconify icon="solar:transfer-horizontal-bold" width={14} />}
                sx={{ typography: "s2", fontWeight: 700 }}
              >
                Compare{selected.size >= 2 ? ` (${selected.size})` : ""}
              </Button>
            </span>
          </Tooltip>
        </Stack>
      </Stack>

      {/* ── filter menu ── */}
      <Menu
        anchorEl={filterAnchor}
        open={!!filterAnchor}
        onClose={() => setFilterAnchor(null)}
        slotProps={{ paper: { sx: { minWidth: 240, p: 1.5 } } }}
      >
        <Typography sx={{ typography: "s3", color: "text.subtitle", fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.4, px: 1, pb: 0.5 }}>
          Source
        </Typography>
        {[{ id: "all", label: "All sources" }, ...Object.values(IMPROVEMENT_SOURCES).map((s) => ({ id: s.id, label: s.label }))].map((opt) => (
          <MenuItem key={opt.id} selected={sourceFilter === opt.id} onClick={() => setSourceFilter(opt.id)} sx={{ typography: "s2" }}>
            {opt.label}
          </MenuItem>
        ))}
        <Typography sx={{ typography: "s3", color: "text.subtitle", fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.4, px: 1, pt: 1, pb: 0.5 }}>
          Status
        </Typography>
        {[
          { id: "all", label: "All statuses" },
          { id: "completed", label: "Completed" },
          { id: "running", label: "Running" },
          { id: "failed", label: "Failed" },
        ].map((opt) => (
          <MenuItem key={opt.id} selected={statusFilter === opt.id} onClick={() => setStatusFilter(opt.id)} sx={{ typography: "s2" }}>
            {opt.label}
          </MenuItem>
        ))}
      </Menu>

      {/* ── grouped table ── */}
      <Box sx={{
        /* Flush against the page — same treatment DataTable uses in
           Evals / Datasets / Environments. No outer stroke, no rounded
           corner; the header row's bottom divider is the only rule. */
        flex: 1, minHeight: 0, overflow: "auto",
        bgcolor: "background.paper",
      }}>
        {groups.length === 0 ? (
          <Stack alignItems="center" justifyContent="center" spacing={1.25} sx={{ py: 8, px: 4, textAlign: "center" }}>
            <Iconify icon="solar:magic-stick-3-linear" width={28} sx={{ color: "text.disabled" }} />
            <Typography sx={{ typography: "s1", fontWeight: 700 }}>No improvement runs yet</Typography>
            <Typography sx={{ typography: "s2", color: "text.subtitle", maxWidth: 420 }}>
              Kick one off from Debug failures on a simulation run, or from the dataset optimizer. Every run you launch will land here.
            </Typography>
          </Stack>
        ) : (
          <Table
            size="small"
            sx={{
              /* No per-cell borders — the only horizontal rule is the
                 row-bottom divider on data rows. Header keeps a lighter
                 vertical separator between columns to match the reference. */
              "& .MuiTableCell-root": { border: "none" },
              "& tbody .MuiTableRow-root": {
                borderBottom: "1px solid",
                borderColor: "divider",
              },
              "& tbody .MuiTableRow-root:last-of-type": { borderBottom: "none" },
            }}
          >
            <TableHead>
              <TableRow
                sx={{
                  /* No fill — matches DataTable header. Bottom divider only. */
                  borderBottom: "1px solid",
                  borderColor: "divider",
                }}
              >
                <Th width={44} />
                <Th>Improvement Name</Th>
                <Th width={140} divider>Source</Th>
                <Th width={150} divider>Created at</Th>
                <Th width={140} align="right" divider>Number of trials</Th>
                <Th width={140} divider>Status</Th>
                <Th width={170} align="right" divider>Held-out score</Th>
              </TableRow>
            </TableHead>
            <TableBody>
              {groups.map((g) => {
                const isOpen = !isCollapsed(g.key);
                const childIds = g.children.map((c) => c.rowId);
                const selectedInGroup = childIds.filter((id) => selected.has(id)).length;
                const allSelected = childIds.length > 0 && selectedInGroup === childIds.length;
                const someSelected = selectedInGroup > 0 && !allSelected;
                return (
                  <GroupRows
                    key={g.key}
                    group={g}
                    isOpen={isOpen}
                    onToggleOpen={() => toggleCollapse(g.key)}
                    onToggleAll={() => toggleGroup(g)}
                    allSelected={allSelected}
                    someSelected={someSelected}
                    onOpenSource={openSource}
                    isChildSelected={(id) => selected.has(id)}
                    onToggleChild={toggleRow}
                    onOpenChild={(r) => navigate(paths.dashboard.simulate.improvementDetail(r.rowId))}
                  />
                );
              })}
            </TableBody>
          </Table>
        )}
      </Box>
    </Box>
  );
}

/* ── grouping ─────────────────────────────────────────────────────────── */

function groupByTarget(rows) {
  const map = new Map();
  rows.forEach((r) => {
    const key = r.source === "simulation" ? `sim:${r.envId}` : `ds:${r.datasetId}`;
    if (!map.has(key)) {
      map.set(key, {
        key,
        source: r.source,
        target: targetLabel(r),
        children: [],
      });
    }
    map.get(key).children.push(r);
  });
  /* Newest first inside each group; groups sort by their most-recent child
     so an active env floats above a stale one. */
  const arr = [...map.values()];
  arr.forEach((g) => g.children.sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1)));
  arr.sort((a, b) => {
    const aLatest = a.children[0]?.createdAt || "";
    const bLatest = b.children[0]?.createdAt || "";
    return aLatest < bLatest ? 1 : -1;
  });
  return arr;
}

/* ── one group's header + child rows ─────────────────────────────────── */

function GroupRows({
  group, isOpen, onToggleOpen, onToggleAll, allSelected, someSelected,
  onOpenSource, isChildSelected, onToggleChild, onOpenChild,
}) {
  const source = IMPROVEMENT_SOURCES[group.source] || IMPROVEMENT_SOURCES.simulation;
  const runCount = group.children.length;
  const totalTrials = group.children.reduce((a, r) => a + trialCount(r), 0);

  return (
    <>
      {/* header row — sits flat on the paper background; only the bottom
          divider separates it from the first child. Chevron + name reads as
          a group heading, not a tinted band. */}
      <TableRow
        onClick={onToggleOpen}
        sx={{ cursor: "pointer", "&:hover": { bgcolor: "action.hover" } }}
      >
        <TableCell padding="checkbox">
          <Stack direction="row" alignItems="center" spacing={0.25} sx={{ pl: 0.5 }}>
            <Iconify
              icon={isOpen ? "eva:arrow-ios-downward-fill" : "eva:arrow-ios-forward-fill"}
              width={14}
              sx={{ color: "text.subtitle" }}
            />
            <Checkbox
              size="small"
              checked={allSelected}
              indeterminate={someSelected}
              onClick={(e) => e.stopPropagation()}
              onChange={onToggleAll}
              sx={{ p: 0.25 }}
            />
          </Stack>
        </TableCell>
        <TableCell>
          <Typography sx={{ typography: "s2", fontWeight: 700 }}>{group.target}</Typography>
        </TableCell>
        <TableCell>
          <Box
            onClick={(e) => {
              e.stopPropagation();
              onOpenSource(group.children[0]);
            }}
            sx={{
              display: "inline-flex", alignItems: "center", gap: 0.5,
              typography: "s2", color: "text.primary",
              cursor: "pointer",
              "&:hover": { color: "primary.main", textDecoration: "underline" },
            }}
          >
            <Iconify icon={source.icon} width={13} sx={{ color: "text.subtitle" }} />
            {source.label}
          </Box>
        </TableCell>
        <TableCell />
        <TableCell align="right">
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {runCount} run{runCount === 1 ? "" : "s"} · {totalTrials} trials
          </Typography>
        </TableCell>
        <TableCell />
        <TableCell />
      </TableRow>

      {/* child rows (collapsed = row hidden via a full-width Collapse in a single cell wouldn't respect the column grid, so we just conditionally render them) */}
      {isOpen && group.children.map((r) => {
        const held = heldOutHeadline(r);
        const isSelected = isChildSelected(r.rowId);
        return (
          <TableRow
            key={r.rowId}
            hover
            onClick={() => onOpenChild(r)}
            selected={isSelected}
            sx={{ cursor: "pointer" }}
          >
            <TableCell padding="checkbox">
              <Box sx={{ pl: 3 }}>
                <Checkbox
                  size="small"
                  checked={isSelected}
                  onClick={(e) => e.stopPropagation()}
                  onChange={() => onToggleChild(r.rowId)}
                  sx={{ p: 0.25 }}
                />
              </Box>
            </TableCell>
            <TableCell>
              <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>{r.name}</Typography>
              <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{r.id}</Typography>
            </TableCell>
            <TableCell>
              <Typography sx={{ typography: "s3", color: "text.disabled" }}>—</Typography>
            </TableCell>
            <TableCell>
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                {formatDate(r.createdAt)}
              </Typography>
            </TableCell>
            <TableCell align="right">
              <Typography sx={{ typography: "s2", color: "text.secondary", fontVariantNumeric: "tabular-nums" }}>
                {String(trialCount(r)).padStart(2, "0")}
              </Typography>
            </TableCell>
            <TableCell>
              <StatusPill status={r.status || "running"} />
            </TableCell>
            <TableCell align="right">
              {held ? (
                <Stack alignItems="flex-end" spacing={0.125}>
                  <Typography sx={{ typography: "s2", fontWeight: 700, color: "text.primary", fontVariantNumeric: "tabular-nums" }}>
                    {held.score}%
                  </Typography>
                  <Typography sx={{ typography: "s3", color: held.tone, fontVariantNumeric: "tabular-nums" }}>
                    {held.lift > 0 ? "+" : ""}{held.lift} from {held.base}%
                  </Typography>
                </Stack>
              ) : (
                <Typography sx={{ typography: "s3", color: "text.disabled" }}>
                  {r.status === "running" ? "Running…" : "—"}
                </Typography>
              )}
            </TableCell>
          </TableRow>
        );
      })}
    </>
  );
}

/* ── helpers ───────────────────────────────────────────────────────────── */

function Th({ children, width, align = "left", divider = false }) {
  return (
    <TableCell
      sx={{
        typography: "s3",
        fontWeight: 600,
        color: "text.secondary",
        width,
        textAlign: align,
        py: 1.25,
        /* Faint left-side vertical rule between header columns, same
           look as the reference. Doesn't extend to body rows. */
        borderLeft: divider ? "1px solid" : "none",
        borderColor: "divider",
      }}
    >
      {children}
    </TableCell>
  );
}

const STATUS_TONE = {
  completed: { color: "#16A34A", label: "Completed" },
  running: { color: "#CA8A04", label: "Running" },
  failed: { color: "#DC2626", label: "Failed" },
};

function StatusPill({ status }) {
  const tone = STATUS_TONE[status] || STATUS_TONE.running;
  /* Outlined pill in the tone colour — matches the reference's clean
     "Completed" chip. No background wash, just a soft outline + text. */
  return (
    <Box
      sx={{
        display: "inline-flex", alignItems: "center",
        height: 22, px: 0.875, borderRadius: 0.75,
        border: `1px solid ${alpha(tone.color, 0.35)}`,
        color: tone.color,
      }}
    >
      <Typography sx={{ typography: "s3", color: "inherit", fontWeight: 500 }}>
        {tone.label}
      </Typography>
    </Box>
  );
}

function formatDate(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString(undefined, { day: "2-digit", month: "2-digit", year: "numeric" });
  } catch { return ""; }
}
