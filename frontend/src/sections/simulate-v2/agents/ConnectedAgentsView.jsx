import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Box, Button, Checkbox, Dialog, DialogActions, DialogContent, DialogContentText, DialogTitle,
  IconButton, Stack, Tooltip, Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import FilterPanel from "src/components/filter-panel/FilterPanel";
import { DataTable, DataTablePagination } from "src/components/data-table";
import { useSnackbar } from "notistack";
import { paths } from "src/routes/paths";
import { useConnectedAgents } from "../store";
import { neutralCheckboxSx } from "../components/primitives";
import { agentSourceOf } from "../_mock/agentRegistry";
import { branchesOf, commitsOn, isGitAgent, versionNameOf } from "../_mock/codeRefs";
import { observeProjectOf, pendingProjectOf } from "./observeProject";
import { deleteBlockOf } from "./agentDelete";
import AgentStateTags from "./AgentStateTags";
import BuildEnvironmentDialog from "./BuildEnvironmentDialog";
import { MONO, ago, buildPresetFor } from "./agentViewUtils";
import { MOVED_FROM_SIMULATE, identityOf } from "./agentProfile";
import { agentTypeLabelOf } from "./agentRecord";
import useMoveLegacyDefinitions from "./useMoveLegacyDefinitions";

/**
 * Every agent connected in the workspace — from the Agents page or while
 * building an environment. One row per agent, however many environments
 * test it; versions added anywhere show up here.
 */
export default function ConnectedAgentsView({ onConnect }) {
  const navigate = useNavigate();
  const { agents, hydrated, state, removeAgent } = useConnectedAgents();
  const { enqueueSnackbar } = useSnackbar();
  /* Old Simulate agent definitions join this list on their own. */
  useMoveLegacyDefinitions();
  const [query, setQuery] = useState("");
  /* Filters like the Improvements list: type, source, whether it's tested, its Observe project. */
  const [filters, setFilters] = useState({});
  const [filterAnchorEl, setFilterAnchorEl] = useState(null);
  const [params, setParams] = useSearchParams();
  const justConnected = agents.find((a) => a.id === params.get("connected"));
  const dismiss = () => setParams({ tab: "connected" }, { replace: true });
  /* Arrived from an old Simulate → Agent definitions link. */
  const moved = params.get("moved") === "agent-definitions";
  /* Same three choices as Build environment on the agent's page. */
  const [buildFor, setBuildFor] = useState(null);
  const startBuild = (choice) => navigate(paths.dashboard.simulate.environmentBuild, {
    state: { presetSource: buildPresetFor(buildFor, choice) },
  });

  /* A template's stand-in isn't anyone's agent — it lives in its environment. */
  const all = useMemo(() => agents
    .filter((a) => !a.placeholder)
    .map((a) => ({
      ...a, source: agentSourceOf(a), identity: identityOf(a), type: agentTypeLabelOf(a), project: observeProjectOf(a, state, agents), projectPending: !!pendingProjectOf(a, state, agents),
      tested: (a.usedIn || []).length ? "In an environment" : "No environment yet",
    })), [agents, state]);
  const filterFields = useMemo(() => {
    const choicesOf = (pick) => [...new Set(all.map(pick).filter(Boolean))].sort();
    return [
      { value: "type", label: "Type", type: "enum", choices: choicesOf((a) => a.type) },
      { value: "source", label: "Source", type: "enum", choices: choicesOf((a) => a.source.label) },
      { value: "tested", label: "Environments", type: "enum", choices: choicesOf((a) => a.tested) },
      { value: "observe", label: "Observe project", type: "enum", choices: ["Linked", "Waiting for first trace", "Not linked"] },
    ];
  }, [all]);
  const activeFilterCount = Object.values(filters).filter((v) => v?.length).length;
  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return all.filter((a) => {
      if (q && !`${a.name} ${a.type} ${a.source.label} ${a.source.where} ${a.identity.owner} ${a.identity.description}`.toLowerCase().includes(q)) return false;
      if (filters.type?.length && !filters.type.includes(a.type)) return false;
      if (filters.source?.length && !filters.source.includes(a.source.label)) return false;
      if (filters.tested?.length && !filters.tested.includes(a.tested)) return false;
      if (filters.observe?.length && !filters.observe.includes(a.project ? (a.projectPending ? "Waiting for first trace" : "Linked") : "Not linked")) return false;
      return true;
    });
  }, [all, query, filters]);

  /* Paged like the other lists; a search starts back on page one. */
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(25);
  useEffect(() => { setPage(0); }, [query, filters]);
  const currentPage = Math.min(page, Math.max(0, Math.ceil(rows.length / pageSize) - 1));
  const pageRows = rows.slice(currentPage * pageSize, (currentPage + 1) * pageSize);

  /* Select several and delete them. An agent can be deleted only once it
     has no environments left, so those are skipped — and said so. Selection
     is kept by id across pages. */
  const [selectedIds, setSelectedIds] = useState([]);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const pageAllOn = pageRows.length > 0 && pageRows.every((a) => selectedIds.includes(a.id));
  const pageSomeOn = pageRows.some((a) => selectedIds.includes(a.id));
  const toggleRow = (id) => setSelectedIds((ids) => (ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]));
  const togglePage = () => setSelectedIds((ids) => (pageAllOn
    ? ids.filter((id) => !pageRows.some((a) => a.id === id))
    : [...new Set([...ids, ...pageRows.map((a) => a.id)])]));
  /* Only ids still in the list count (one may have been deleted elsewhere). */
  const picked = agents.filter((a) => selectedIds.includes(a.id));
  /* Agents that still have environments can't be deleted — they're skipped. */
  const deletable = picked.filter((a) => !deleteBlockOf(a));
  const skipped = picked.filter((a) => deleteBlockOf(a));
  const performDelete = () => {
    deletable.forEach((a) => removeAgent(a.key));
    enqueueSnackbar(
      `${deletable.length === 1 ? `${deletable[0].name} deleted` : `${deletable.length} agents deleted`}.${skipped.length ? ` ${skipped.length === 1 ? `${skipped[0].name} was skipped` : `${skipped.length} were skipped`} — it still has environments.` : ""}`,
      { variant: "success" },
    );
    setSelectedIds(skipped.map((a) => a.id));
    setConfirmDelete(false);
  };

  /* Rebuilt each render — the select column reads the current selection. */
  const columns = [
    {
      id: "select", header: "", size: 48, enableSorting: false,
      renderHeader: () => (
        <Checkbox
          size="small"
          checked={pageAllOn}
          indeterminate={!pageAllOn && pageSomeOn}
          disabled={!pageRows.length}
          onChange={togglePage}
          inputProps={{ "aria-label": "Select every agent on this page" }}
          sx={{ p: 0, ...neutralCheckboxSx }}
        />
      ),
      cell: ({ row }) => {
        const a = row.original;
        return (
          <Box onClick={(e) => e.stopPropagation()} sx={{ display: "flex", alignItems: "center", height: "100%" }}>
            <Checkbox
              size="small"
              checked={selectedIds.includes(a.id)}
              onChange={() => toggleRow(a.id)}
              onClick={(e) => e.stopPropagation()}
              inputProps={{ "aria-label": `Select ${a.name}` }}
              sx={{ p: 0, ...neutralCheckboxSx }}
            />
          </Box>
        );
      },
    },
    {
      id: "name", accessorKey: "name", header: "Agent name", meta: { flex: 2 }, minSize: 280, enableSorting: false,
      cell: ({ row }) => {
        const a = row.original;
        return (
          <Stack direction="row" alignItems="center" spacing={1.25} sx={{ minWidth: 0, height: "100%" }}>
            <Box sx={{ minWidth: 0 }}>
              <Stack direction="row" alignItems="center" spacing={1.25} sx={{ minWidth: 0 }}>
                <Typography variant="body2" noWrap sx={{ fontWeight: 500, minWidth: 0 }}>{a.name}</Typography>
                <AgentStateTags agent={a} />
              </Stack>
              {a.identity.description && (
                <Typography noWrap title={a.identity.description} sx={{ typography: "s3", color: "text.subtitle", minWidth: 0 }}>{a.identity.description}</Typography>
              )}
            </Box>
          </Stack>
        );
      },
    },
    {
      /* Voice, Chat or Code — what kind of agent, before where it's read from. */
      id: "type", accessorKey: "type", header: "Type", size: 120, enableSorting: false,
      cell: ({ row }) => <Typography variant="body2" noWrap sx={{ fontSize: 13 }}>{row.original.type}</Typography>,
    },
    {
      id: "source", accessorKey: "source", header: "Source", size: 170, enableSorting: false,
      cell: ({ row }) => <Typography variant="body2" noWrap sx={{ fontSize: 13 }}>{row.original.source.label}</Typography>,
    },
    {
      id: "latest", accessorKey: "latest", header: "Versions", size: 200, enableSorting: false,
      cell: ({ row }) => {
        const a = row.original;
        /* A code agent: the version in production with its short commit (the
           branch is on the agent's page), how many versions, and fixes
           waiting on their own branches. */
        if (isGitAgent(a)) {
          const fixes = branchesOf(a).filter((x) => !x.release && x.count && commitsOn(a, x.name).some((c) => c.draft)).length;
          const sha = versionNameOf(a, a.production).ref?.sha;
          return (
            <Typography variant="body2" noWrap sx={{ fontSize: 13 }}>
              <Box component="span" sx={{ fontFamily: MONO, fontWeight: 600 }}>{a.production}</Box>
              {sha && <Box component="span" sx={{ color: "text.secondary" }}>{" · "}<Box component="span" sx={{ fontFamily: MONO }}>{sha}</Box></Box>}
              <Box component="span" sx={{ color: "text.secondary" }}>
                {` · ${a.versions.length} total${fixes ? ` · ${fixes} PR${fixes === 1 ? "" : "s"}` : ""}`}
              </Box>
            </Typography>
          );
        }
        return (
          <Typography variant="body2" noWrap sx={{ fontSize: 13 }}>
            <Box component="span" sx={{ fontFamily: MONO, fontWeight: 600 }}>{a.latest}</Box>
            <Box component="span" sx={{ color: "text.secondary" }}>
              {`${a.latestIsDraft ? ` draft · ${a.production} in production` : ""} · ${a.versions.length} total`}
            </Box>
          </Typography>
        );
      },
    },
    {
      /* The one Observe project its traces or call logs land in — what lets
         its Error Feed issues be fixed. */
      id: "observe", accessorKey: "project", header: "Observe project", size: 170, enableSorting: false,
      cell: ({ row }) => {
        const p = row.original.project;
        return (
          <Tooltip arrow title={p && row.original.projectPending ? `Waiting for the first trace in ${p.name} — Error Feed starts watching with it.` : p ? `Error Feed issues from ${p.name} can be fixed with this agent.` : "Not linked — its Error Feed issues can't be fixed. Link a project on its Connections tab."}>
            <Typography variant="body2" noWrap sx={{ fontSize: 13, color: p && !row.original.projectPending ? "text.primary" : "text.disabled" }}>
              {p ? (row.original.projectPending ? `${p.name} · waiting` : p.name) : "Not linked"}
            </Typography>
          </Tooltip>
        );
      },
    },
    {
      id: "usedIn", accessorKey: "usedIn", header: "Used in", meta: { flex: 1 }, minSize: 200, enableSorting: false,
      cell: ({ row }) => {
        const u = row.original.usedIn;
        const { production } = row.original;
        const behind = u.filter((x) => x.behind);
        /* An environment still testing an older version than the latest —
           said as the version it tests, not a bare "N behind". Muted like
           the "· N total" beside it; the tooltip says what to do. */
        const lag = behind.length === 0 ? null
          : u.length === 1 ? `tests ${versionNameOf(row.original, behind[0].running).main}`
            : `${behind.length} on older version${behind.length === 1 ? "" : "s"}`;
        const why = behind.length === 0 ? "" : (
          <Stack spacing={0.25} sx={{ py: 0.25 }}>
            {behind.map((x) => (
              <Typography key={x.envId} sx={{ typography: "s3", color: "inherit" }}>{`${x.envName} tests ${versionNameOf(row.original, x.running).main}`}</Typography>
            ))}
            <Typography sx={{ typography: "s3", color: "inherit", opacity: 0.75 }}>
              {`${versionNameOf(row.original, production).main} is in production. Switch ${behind.length === 1 ? "it" : "them"} from the agent's Environments tab.`}
            </Typography>
          </Stack>
        );
        return (
          <Typography variant="body2" noWrap sx={{ fontSize: 13, color: u.length ? "text.primary" : "text.disabled" }}>
            {u.length === 0 ? "No environments yet" : u.length === 1 ? u[0].envName : `${u.length} environments`}
            {lag && (
              <Tooltip arrow title={why}>
                <Box component="span" sx={{ color: "text.secondary", cursor: "help" }}>{` · ${lag}`}</Box>
              </Tooltip>
            )}
          </Typography>
        );
      },
    },
    {
      id: "updatedAt", accessorKey: "updatedAt", header: "Last updated", size: 170, enableSorting: false,
      cell: ({ row }) => (
        <Typography variant="body2" noWrap sx={{ fontSize: 13 }}>
          {row.original.lastSyncedAt ? `Synced ${ago(row.original.lastSyncedAt)}` : ago(row.original.updatedAt)}
        </Typography>
      ),
    },
    {
      /* When it was connected — a date, not a state. Every agent here is connected. */
      id: "connectedAt", accessorKey: "connectedAt", header: "Connected on", size: 140, enableSorting: false,
      cell: ({ row }) => <Typography variant="body2" noWrap sx={{ fontSize: 13 }}>{ago(row.original.connectedAt)}</Typography>,
    },
  ];

  if (hydrated && agents.length === 0) {
    return (
      <Box sx={{ p: 5, textAlign: "center", border: "1px dashed", borderColor: "divider", borderRadius: 1.5 }}>
        <Iconify icon="solar:link-circle-linear" width={28} sx={{ color: "text.subtitle" }} />
        <Typography sx={{ typography: "s1", fontWeight: 600, mt: 1 }}>No agents yet</Typography>
        <Typography sx={{ typography: "s2", color: "text.subtitle", mt: 0.5, mb: 2, maxWidth: 460, mx: "auto" }}>
          Bring one from code — GitHub or an upload — or a platform it runs on, or build it in Future AGI. Then build an environment to test it in.
        </Typography>
        <Button variant="contained" color="primary" size="small" onClick={onConnect} sx={{ typography: "s2", fontWeight: 700 }}>
          New agent
        </Button>
      </Box>
    );
  }

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5, flex: 1, minHeight: 0 }}>
      {justConnected && (
        <Stack
          direction="row" alignItems="center" spacing={1.25}
          sx={{
            px: 1.75, py: 1.25, borderRadius: 1.25, border: "1px solid",
            borderColor: alpha("#16A34A", 0.4),
            bgcolor: (t) => alpha("#16A34A", t.palette.mode === "dark" ? 0.1 : 0.06),
          }}
        >
          <Iconify icon="solar:check-circle-bold" width={18} sx={{ color: "#16A34A", flexShrink: 0 }} />
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "s2", fontWeight: 700 }}>
              {params.get("existing") ? `${justConnected.name} was already connected` : `${justConnected.name} is connected`}
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>
              {justConnected.usedIn.length
                ? `Used in ${justConnected.usedIn.length} environment${justConnected.usedIn.length === 1 ? "" : "s"}. Build another to test it under different conditions.`
                : "Next, build an environment for it — the simulated tools and data, scenarios and evaluations it will be tested against."}
            </Typography>
          </Box>
          <Button size="small" onClick={() => navigate(`${paths.dashboard.agents}/connected/${justConnected.id}`)} sx={{ typography: "s2", fontWeight: 700, color: "text.primary" }}>
            View agent
          </Button>
          <Button size="small" variant="contained" color="primary" onClick={() => setBuildFor(justConnected)} sx={{ typography: "s2", fontWeight: 700 }}>
            Build environment
          </Button>
          <IconButton size="small" onClick={dismiss} aria-label="Dismiss"><Iconify icon="eva:close-fill" width={16} /></IconButton>
        </Stack>
      )}
      {moved && (
        <Stack
          direction="row" alignItems="center" spacing={1.25}
          sx={{ px: 1.75, py: 1, borderRadius: 1.25, border: "1px solid", borderColor: "divider", bgcolor: "background.neutral" }}
        >
          <Iconify icon="solar:info-circle-linear" width={16} sx={{ color: "text.secondary", flexShrink: 0 }} />
          <Typography sx={{ typography: "s2", color: "text.secondary", flex: 1, minWidth: 0 }}>{MOVED_FROM_SIMULATE}</Typography>
          <IconButton size="small" onClick={dismiss} aria-label="Dismiss"><Iconify icon="eva:close-fill" width={16} /></IconButton>
        </Stack>
      )}
      <Stack direction="row" alignItems="center" spacing={1}>
        <FormSearchField
          searchQuery={query}
          onChange={(e) => setQuery(e.target.value)}
          size="small"
          placeholder="Search"
          sx={{ width: 250, "& .MuiOutlinedInput-root": { height: "30px" } }}
        />
        <Button
          size="small" variant="outlined"
          startIcon={<Iconify icon="mage:filter" width={16} />}
          endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={14} />}
          onClick={(e) => setFilterAnchorEl(e.currentTarget)}
          sx={{ textTransform: "none", fontSize: "13px", height: "30px", borderColor: activeFilterCount ? "primary.main" : "divider", color: activeFilterCount ? "primary.main" : "text.secondary" }}
        >
          {`Filter${activeFilterCount ? ` (${activeFilterCount})` : ""}`}
        </Button>
        <Box flex={1} />
        {/* What "connected" means, once, where the list starts. */}
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          Connected = we can reach it: its repo, platform, endpoint or traces.
        </Typography>
      </Stack>
      <FilterPanel
        anchorEl={filterAnchorEl} open={Boolean(filterAnchorEl)} onClose={() => setFilterAnchorEl(null)}
        filterFields={filterFields} currentFilters={filters}
        onApply={(f) => { setFilters(f || {}); setFilterAnchorEl(null); }}
        placement="bottom-start"
      />
      {picked.length > 0 && (
        <Stack direction="row" alignItems="center" spacing={1.5} sx={{ flexShrink: 0, px: 2, py: 1, borderRadius: 1.5, border: "1px solid", borderColor: "divider", bgcolor: "background.paper" }}>
          <Typography noWrap sx={{ typography: "s2", minWidth: 0 }}>
            <Box component="span" sx={{ fontWeight: 700 }}>{picked.length}</Box>
            <Box component="span" sx={{ color: "text.secondary" }}>{picked.length === 1 ? " agent selected" : " agents selected"}</Box>
          </Typography>
          <Box flex={1} />
          <Button size="small" onClick={() => setSelectedIds([])} sx={{ typography: "s2", fontWeight: 600, color: "text.secondary", minWidth: 0, px: 1 }}>Clear</Button>
          <Tooltip
            arrow
            title={!deletable.length
              ? `${picked.length === 1 ? "It still has environments" : "Each of these still has environments"}. Delete its environments first, or archive the agent.`
              : skipped.length ? `${skipped.length} with environments will be skipped.` : ""}
          >
            <span>
              <Button
                size="small" disabled={!deletable.length} onClick={() => setConfirmDelete(true)}
                startIcon={<Iconify icon="solar:trash-bin-trash-linear" width={13} />}
                sx={{ typography: "s2", fontWeight: 600, color: "#DC2626", px: 1, minWidth: 0, flexShrink: 0, "&:hover": { bgcolor: (t) => alpha("#DC2626", t.palette.mode === "dark" ? 0.12 : 0.06) } }}
              >
                {skipped.length && deletable.length ? `Delete ${deletable.length}` : "Delete"}
              </Button>
            </span>
          </Tooltip>
        </Stack>
      )}
      <DataTable
        columns={columns}
        data={pageRows}
        rowCount={rows.length}
        getRowId={(r) => r.id}
        rowHeight={rows.some((r) => r.identity.description) ? 52 : 44}
        onRowClick={(r) => navigate(`${paths.dashboard.agents}/connected/${r.id}`)}
        emptyMessage="No agents match your search"
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
      <Dialog open={confirmDelete} onClose={() => setConfirmDelete(false)} maxWidth="xs" fullWidth>
        <DialogTitle sx={{ typography: "m2", fontWeight: 700 }}>
          {deletable.length === 1 ? `Delete ${deletable[0].name}?` : `Delete ${deletable.length} agents?`}
        </DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ typography: "s2" }}>
            {`${deletable.length === 1 ? "It leaves" : "They leave"} Agents and every picker. ${deletable.length === 1 ? "Its" : "Their"} versions, and the runs, improvements and proofs that name ${deletable.length === 1 ? "it" : "them"}, are kept — they still open ${deletable.length === 1 ? "its" : "their"} history.`}
          </DialogContentText>
          {skipped.length > 0 && (
            <DialogContentText sx={{ typography: "s2", mt: 1.5 }}>
              {`Skipped: ${skipped.map((a) => a.name).join(", ")}. An agent can be deleted only once it has no environments left — delete ${skipped.length === 1 ? "its environments" : "theirs"} first, or archive ${skipped.length === 1 ? "it" : "them"}.`}
            </DialogContentText>
          )}
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={() => setConfirmDelete(false)} sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}>Cancel</Button>
          <Button variant="contained" onClick={performDelete} sx={{ typography: "s2", fontWeight: 700, bgcolor: "#DC2626", color: "#fff", "&:hover": { bgcolor: "#B91C1C", color: "#fff" } }}>
            Delete
          </Button>
        </DialogActions>
      </Dialog>
      {buildFor && (
        <BuildEnvironmentDialog
          open
          onClose={() => setBuildFor(null)}
          agent={buildFor}
          defaultVersion={buildFor.production || buildFor.latest}
          onBuild={startBuild}
        />
      )}
    </Box>
  );
}

ConnectedAgentsView.propTypes = { onConnect: PropTypes.func };
