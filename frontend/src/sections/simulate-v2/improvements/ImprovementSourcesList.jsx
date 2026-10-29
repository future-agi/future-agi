import PropTypes from "prop-types";
import ImprovementMetricsStrip from "./ImprovementMetricsStrip";
import NewImprovement from "./NewImprovement";
import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  Box,
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  InputAdornment,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { neutralCheckboxSx } from "../components/primitives";
import Iconify from "src/components/iconify";
import PageHeader from "src/components/page-header";
import { DataTable } from "src/components/data-table";
import { ObjectFilterPanel } from "src/components/filter";
import { useSimStore, useConnectedAgents } from "../store";
import { useIntegrations } from "../_mock/integrations";
import { improvementRowsOf } from "./improvementRows";
import { formatCreatedAt, modalityOfSurface } from "./improvementSources";
import StatusPill from "./ImprovementStatusPill";

const compactInputSx = {
  "& .MuiInputBase-root": { typography: "s2", height: 32 },
  "& .MuiOutlinedInput-input": { py: 0 },
};
const ROW_HEIGHT = 44;
const HEADER_HEIGHT = 40;

/**
 * Improvements, level 1 — every improvement: one trigger (an Error Feed
 * issue, a schedule, a goal, a manual start) and the search it ran. Run name
 * · Agent type · Status · Runs · Created · Latest run. A row opens level 2,
 * that improvement's runs — chart, compare, choose winner; a run there opens
 * its run page (level 3).
 *
 * With `agent` it's the agent page's Improvements tab: only that agent's.
 */
export default function ImprovementSourcesList({ agent, run }) {
  const runEnvId = run?.envId;
  const runId = run?.runId;
  const navigate = useNavigate();
  const location = useLocation();
  const { state, dispatch } = useSimStore();
  const { agents } = useConnectedAgents();
  const { prs } = useIntegrations();
  const [query, setQuery] = useState("");
  const [filterAnchorEl, setFilterAnchorEl] = useState(null);
  const [filters, setFilters] = useState({});
  const [selectedIds, setSelectedIds] = useState([]);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [newOpen, setNewOpen] = useState(false);

  /* A running improvement ticks its phase — read the clock while one runs. */
  const [now, setNow] = useState(() => Date.now());
  const improvements = useMemo(
    () => improvementRowsOf({ state, agents, prs, now }),
    [state, agents, prs, now],
  );
  const anyRunning = improvements.some((r) => r.status?.key === "running");
  useEffect(() => {
    if (!anyRunning) return undefined;
    const t = setInterval(() => setNow(Date.now()), 2000);
    return () => clearInterval(t);
  }, [anyRunning]);

  const rows = useMemo(() => {
    /* A run's tab lists what was started from that run — Debug failures,
       a goal's first measure, an issue's reproduce run. */
    const mine = runId
      ? improvements.filter(
          (r) => r.envId === runEnvId && r.fromRunId === runId,
        )
      : agent
        ? improvements.filter((r) => r.agentKey === agent.key)
        : improvements;
    const envRows = mine.map((r) => {
      const env = (state.myEnvironments || []).find((e) => e.id === r.envId);
      const record = (state.byEnv?.[r.envId]?.optimizations || []).find(
        (o) => o.id === r.recordId,
      );
      const a = agents.find((x) => x.key === r.agentKey);
      return {
        ...r,
        kind: "env",
        modality: modalityOfSurface(env?.surface),
        /* A running search hasn't settled on its runs yet — "—" until it has. */
        runCount:
          r.status?.key === "running" ? 0 : record?.result?.trials?.length || 0,
        latestAt: record?.completedAt || r.createdAt,
        latestIsRunning: r.status?.key === "running",
        /* The platform's id when it has one — tells same-named environments apart. */
        agentId: a?.values?.agentId || r.agentKey || null,
      };
    });
    const all = envRows;
    /* Two environments with one name: say which agent each is. */
    const counts = new Map();
    all
      .filter((r) => r.kind === "env")
      .forEach((r) => counts.set(r.envId, r.envName));
    const names = new Map();
    [...counts.values()].forEach((n) => names.set(n, (names.get(n) || 0) + 1));
    return all
      .map((r) => ({
        ...r,
        duplicate: r.kind === "env" && names.get(r.envName) > 1,
      }))
      .sort(
        (a, b) =>
          (Date.parse(b.createdAt || 0) || 0) -
          (Date.parse(a.createdAt || 0) || 0),
      );
  }, [
    improvements,
    agent,
    runEnvId,
    runId,
    agents,
    state.myEnvironments,
    state.byEnv,
  ]);

  const filterFields = useMemo(() => {
    const choicesOf = (pick) =>
      [...new Set(rows.map(pick).filter(Boolean))].sort();
    return [
      {
        value: "agentType",
        label: "Agent type",
        type: "enum",
        choices: choicesOf((r) => r.modality?.label),
      },
      {
        value: "status",
        label: "Status",
        type: "enum",
        choices: choicesOf((r) => r.status?.label),
      },
      {
        value: "trigger",
        label: "Trigger",
        type: "enum",
        choices: choicesOf((r) => r.trigger?.source),
      },
    ];
  }, [rows]);
  const activeFilterCount = Object.values(filters).filter(
    (v) => v?.length,
  ).length;
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return rows.filter((r) => {
      if (
        q &&
        !`${r.name} ${r.envName} ${r.agentName || ""} ${r.trigger?.label || ""} ${r.recordId || ""}`
          .toLowerCase()
          .includes(q)
      )
        return false;
      if (
        filters.agentType?.length &&
        !filters.agentType.includes(r.modality?.label)
      )
        return false;
      if (filters.status?.length && !filters.status.includes(r.status?.label))
        return false;
      if (
        filters.trigger?.length &&
        !filters.trigger.includes(r.trigger?.source)
      )
        return false;
      return true;
    });
  }, [rows, query, filters]);

  /* Level 2 comes back here — this page, or the agent's Improvements tab. */
  const back = {
    to: `${location.pathname}${location.search}`,
    label: run ? run.label : agent ? agent.name : "Improvements",
  };
  const openRow = (r) => navigate(r.href, { state: { back } });

  const toggleRow = (id) =>
    setSelectedIds((ids) =>
      ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id],
    );
  const selectedRows = rows.filter((r) => selectedIds.includes(r.id));
  /* A change that has shipped is still followed — the Error Feed watches
     it, the agent's versions name it — so its improvement stays. */
  const keptRows = selectedRows.filter((r) => !!r.output);
  const deletable = selectedRows.filter((r) => !r.output);
  const performDelete = () => {
    const byEnv = {};
    deletable
      .filter((r) => r.kind === "env")
      .forEach((r) => {
        byEnv[r.envId] = [...(byEnv[r.envId] || []), r.recordId];
      });
    Object.entries(byEnv).forEach(([envId, ids]) => {
      const es = state.byEnv?.[envId] || {};
      /* Its unshipped draft and landing records go with it. */
      const without = (map) =>
        Object.fromEntries(
          Object.entries(map || {}).filter(([k]) => !ids.includes(k)),
        );
      dispatch({
        type: "patchEnvState",
        envId,
        patch: {
          optimizations: (es.optimizations || []).filter(
            (o) => !ids.includes(o.id),
          ),
          ...(es.handoffDrafts && { handoffDrafts: without(es.handoffDrafts) }),
          ...(es.handoffLandings && {
            handoffLandings: without(es.handoffLandings),
          }),
        },
      });
    });
    setSelectedIds([]);
    setConfirmDelete(false);
  };

  const columns = [
    {
      id: "select",
      header: () => null,
      size: 44,
      enableSorting: false,
      cell: ({ row }) => (
        <Checkbox
          size="small"
          checked={selectedIds.includes(row.original.id)}
          onChange={(e) => {
            e.stopPropagation();
            toggleRow(row.original.id);
          }}
          onClick={(e) => e.stopPropagation()}
          sx={{ p: 0, ...neutralCheckboxSx }}
        />
      ),
    },
    {
      id: "name",
      header: "Name",
      meta: { flex: 1.6 },
      minSize: 260,
      enableSorting: false,
      cell: ({ row: { original: r } }) => (
        <Box minWidth={0}>
          <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
            {r.name}
          </Typography>
          {/* Agent · source · sub-source · what it was, then where it ran. */}
          <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
            {r.kind === "dataset" ? (
              `${r.envName} · ${r.sub}`
            ) : (
              <>
                {r.agentName || r.modality?.label}
                {r.duplicate && r.agentId && (
                  <Box
                    component="span"
                    sx={{ fontFamily: "ui-monospace, Menlo, monospace" }}
                  >{` · ${r.agentId}`}</Box>
                )}
                {r.trigger && ` · ${r.trigger.source} · ${r.trigger.label}`}
                {` · in ${r.envName}`}
              </>
            )}
          </Typography>
        </Box>
      ),
    },
    /* The PRD's columns: which agent, how it ended, what it proved, what it
       shipped and what it cost. The runs it tried live on its own page. */
    ...(agent || run
      ? []
      : [
          {
            id: "agent",
            header: "Agent",
            size: 190,
            enableSorting: false,
            cell: ({ row: { original: r } }) => (
              <Stack
                direction="row"
                alignItems="center"
                spacing={0.875}
                sx={{ minWidth: 0 }}
              >
                <Box
                  sx={{
                    width: 22,
                    height: 22,
                    borderRadius: "4px",
                    flexShrink: 0,
                    display: "grid",
                    placeItems: "center",
                    bgcolor: "background.neutral",
                    color: "text.secondary",
                  }}
                >
                  <Iconify icon={r.modality.icon} width={14} />
                </Box>
                <Typography noWrap sx={{ typography: "s2", minWidth: 0 }}>
                  {r.agentName || r.modality.label}
                </Typography>
              </Stack>
            ),
          },
        ]),
    {
      id: "status",
      header: "Status",
      size: 190,
      enableSorting: false,
      cell: ({ row: { original: r } }) => (
        <StatusPill ending={r.status} headline={r.headline} />
      ),
    },
    {
      id: "result",
      header: "Result",
      size: 170,
      enableSorting: false,
      cell: ({ row: { original: r } }) =>
        r.held ? (
          <Box minWidth={0}>
            <Typography
              noWrap
              sx={{ typography: "s2", fontVariantNumeric: "tabular-nums" }}
            >
              {r.held.text}
            </Typography>
            {(r.held.broke != null || r.held.note) && (
              <Typography
                noWrap
                sx={{
                  typography: "s3",
                  color: r.held.broke ? "status.danger.fg" : "text.subtitle",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {r.held.broke == null
                  ? r.held.note
                  : r.held.broke
                    ? `broke ${r.held.broke} held back`
                    : "broke none held back"}
              </Typography>
            )}
          </Box>
        ) : (
          <Typography sx={{ typography: "s3", color: "text.disabled" }}>
            —
          </Typography>
        ),
    },
    {
      /* What it handed over, and where that is now. */
      id: "output",
      header: "Hand-off",
      renderHeader: () => (
        <HeaderTip
          label="Hand-off"
          tip="What the improvement handed over — a pull request, a draft or test copy on the platform, a patch — and where it is now: in review, released, watching for 30 days."
        />
      ),
      size: 170,
      enableSorting: false,
      cell: ({ row: { original: r } }) =>
        r.output?.label ? (
          <Typography noWrap sx={{ typography: "s2", minWidth: 0 }}>
            {r.output.label}
          </Typography>
        ) : (
          <Typography sx={{ typography: "s3", color: "text.disabled" }}>
            —
          </Typography>
        ),
    },
    {
      id: "cost",
      header: "Cost",
      size: 120,
      enableSorting: false,
      cell: ({ row: { original: r } }) =>
        r.credits != null ? (
          <Typography
            noWrap
            sx={{ typography: "s2", fontVariantNumeric: "tabular-nums" }}
          >{`${r.credits.toLocaleString()} credits`}</Typography>
        ) : (
          <Typography sx={{ typography: "s3", color: "text.disabled" }}>
            —
          </Typography>
        ),
    },
    {
      id: "createdAt",
      header: "Created",
      size: 150,
      enableSorting: false,
      cell: ({ row: { original: r } }) => (
        <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
          {formatCreatedAt(r.createdAt)}
        </Typography>
      ),
    },
  ];

  const embedded = !!agent || !!run;
  return (
    <Box
      sx={
        embedded
          ? { display: "flex", flexDirection: "column", gap: 2 }
          : {
              height: "100%",
              display: "flex",
              flexDirection: "column",
              gap: 2,
              overflow: "hidden",
              minHeight: 0,
              p: 2,
            }
      }
    >
      {!embedded && (
        <PageHeader
          sx={{ mb: 0, flexShrink: 0 }}
          title="Improvements"
          description="Every improvement across your environments."
        />
      )}

      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{ flexShrink: 0 }}
      >
        <TextField
          size="small"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by name, environment or issue"
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Iconify
                  icon="solar:magnifer-linear"
                  width={16}
                  sx={{ color: "text.subtitle" }}
                />
              </InputAdornment>
            ),
          }}
          sx={{ width: 300, ...compactInputSx }}
        />
        <Button
          size="small"
          variant="outlined"
          startIcon={<Iconify icon="solar:filter-linear" width={16} />}
          endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={14} />}
          onClick={(e) => setFilterAnchorEl(e.currentTarget)}
          sx={{
            /* Selected reads neutral, never purple — purple is for actions. */
            borderColor: activeFilterCount ? "text.primary" : "divider",
            color: activeFilterCount ? "text.primary" : "text.secondary",
            bgcolor: activeFilterCount ? "action.selected" : "transparent",
            "&:hover": {
              borderColor: activeFilterCount ? "text.primary" : "text.disabled",
              bgcolor: activeFilterCount ? "action.selected" : "action.hover",
            },
          }}
        >
          {`Filter${activeFilterCount ? ` (${activeFilterCount})` : ""}`}
        </Button>
        <Box flex={1} />
        {/* RSI PRD Flow 4 — start one aimed at a goal. A run's Debug failures
            stays the other way in. The agent's tab has its own header. */}
        {!embedded && (
          <Button
            size="small"
            variant="contained"
            color="primary"
            startIcon={<Iconify icon="solar:add-linear" width={16} />}
            onClick={() => setNewOpen(true)}
          >
            New improvement
          </Button>
        )}
        <NewImprovement open={newOpen} onClose={() => setNewOpen(false)} />
      </Stack>

      {/* Acceptance rate, time to proposal, fix survival — over what's listed. */}
      {!run && <ImprovementMetricsStrip rows={rows} />}

      <ObjectFilterPanel
        anchorEl={filterAnchorEl}
        open={Boolean(filterAnchorEl)}
        onClose={() => setFilterAnchorEl(null)}
        filterFields={filterFields}
        currentFilters={filters}
        onApply={(f) => {
          setFilters(f || {});
          setFilterAnchorEl(null);
        }}
        placement="bottom-start"
      />

      {selectedIds.length > 0 && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.5}
          sx={{
            flexShrink: 0,
            px: 2,
            py: 1,
            borderRadius: "8px",
            border: "1px solid",
            borderColor: "divider",
            bgcolor: "background.paper",
          }}
        >
          <Typography sx={{ typography: "s2" }}>
            <Box component="span" sx={{ fontWeight: 600 }}>
              {selectedIds.length}
            </Box>
            <Box component="span" sx={{ color: "text.secondary" }}>
              {selectedIds.length === 1
                ? " improvement selected"
                : " improvements selected"}
            </Box>
          </Typography>
          <Box flex={1} />
          <Button
            size="small"
            onClick={() => setSelectedIds([])}
            sx={{ color: "text.secondary", minWidth: 0 }}
          >
            Clear
          </Button>
          <Button
            size="small"
            onClick={() => setConfirmDelete(true)}
            startIcon={
              <Iconify icon="solar:trash-bin-trash-linear" width={14} />
            }
            sx={{
              color: "status.danger.fg",
              minWidth: 0,
              "&:hover": { bgcolor: "status.danger.bg" },
            }}
          >
            Delete
          </Button>
        </Stack>
      )}

      <Box
        sx={
          embedded
            ? /* On the agent page it runs to the bottom of the screen, like the page's own list. */
              {
                display: "flex",
                overflow: "hidden",
                height: selectedIds.length
                  ? "calc(100vh - 266px)"
                  : "calc(100vh - 202px)",
                minHeight: HEADER_HEIGHT + 3 * ROW_HEIGHT,
              }
            : { flex: 1, minHeight: 0, display: "flex" }
        }
      >
        <DataTable
          columns={columns}
          data={filtered}
          rowCount={filtered.length}
          getRowId={(r) => r.id}
          onRowClick={openRow}
          rowHeight={ROW_HEIGHT}
          headerHeight={HEADER_HEIGHT}
          emptyMessage={
            query || activeFilterCount
              ? "Nothing matches."
              : run
                ? `Nothing has been improved from ${run.label} yet. Debug failures starts one from its failing scenarios.`
                : agent
                  ? `${agent.name} hasn't been improved yet. Start one from Debug failures on a finished run, or Fix on an Error Feed issue.`
                  : "No improvements yet. Start one with New improvement, from Debug failures on a finished run, or with Fix on an Error Feed issue."
          }
        />
      </Box>

      <Dialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        maxWidth={false}
        PaperProps={{ sx: { maxWidth: 400 } }}
        fullWidth
      >
        <DialogTitle sx={{ typography: "m2", fontWeight: 600 }}>
          {!deletable.length
            ? keptRows.length === 1
              ? `${keptRows[0].name} can't be deleted`
              : "These improvements can't be deleted"
            : deletable.length === 1
              ? `Delete ${deletable[0].name}?`
              : `Delete ${deletable.length} improvements?`}
        </DialogTitle>
        <DialogContent>
          {deletable.length > 0 && (
            <DialogContentText sx={{ typography: "s2" }}>
              {
                "Their trials, results and unreleased drafts go with them. The environments and their own runs stay as they are."
              }
            </DialogContentText>
          )}
          {keptRows.length > 0 && (
            <DialogContentText
              sx={{ typography: "s2", mt: deletable.length ? 1.5 : 0 }}
            >
              {`${keptRows.length === 1 ? `${keptRows[0].name} stays` : `${keptRows.length} stay`} — ${keptRows.map((r) => r.output.label).join(", ")}. A shipped change is still followed: the Error Feed watches it and the agent's versions point back to the improvement that made it.`}
            </DialogContentText>
          )}
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button
            onClick={() => setConfirmDelete(false)}
            sx={{ color: "text.secondary" }}
          >
            {deletable.length ? "Cancel" : "Close"}
          </Button>
          {deletable.length > 0 && (
            <Button variant="contained" onClick={performDelete} color="error">
              {keptRows.length ? `Delete ${deletable.length}` : "Delete"}
            </Button>
          )}
        </DialogActions>
      </Dialog>
    </Box>
  );
}

ImprovementSourcesList.propTypes = {
  agent: PropTypes.object,
  run: PropTypes.shape({
    envId: PropTypes.string,
    runId: PropTypes.string,
    label: PropTypes.string,
  }),
};

/* A column header that says what the column means on hover. */
function HeaderTip({ label, tip }) {
  return (
    <Stack direction="row" alignItems="center" spacing={0.5} component="span">
      {/* The grid's own title class, so it reads like the other headers. */}
      <span className="MuiDataGrid-columnHeaderTitle">{label}</span>
      <Tooltip arrow title={tip}>
        <Box component="span" sx={{ display: "flex" }}>
          <Iconify
            icon="solar:info-circle-linear"
            width={14}
            sx={{ color: "text.disabled" }}
          />
        </Box>
      </Tooltip>
    </Stack>
  );
}
HeaderTip.propTypes = { label: PropTypes.string, tip: PropTypes.string };
