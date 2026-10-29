import PropTypes from "prop-types";
import { managedOf } from "../_mock/envManaged";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Avatar,
  AvatarGroup,
  Box,
  Button,
  Stack,
  Typography,
  Tooltip,
  IconButton,
  Menu,
  MenuItem,
  ListItemIcon,
  ListItemText,
  TextField,
  InputAdornment,
} from "@mui/material";
import { paths } from "src/routes/paths";
import { DataTable } from "src/components/data-table";
import DataTablePagination from "src/components/data-table/DataTablePagination";
import ListToolbar from "src/components/list-toolbar";
import Iconify from "src/components/iconify";
import StatusBadge from "src/components/status-badge/StatusBadge";
import { subGoalCatalogOf } from "../_mock/envModel";
import { runReadiness } from "../_mock/setupGaps";
import { runPlanHref } from "../_mock/runPlanLink";
import { useSimStore } from "../store";
import useRunFitCheck from "../run/useRunFitCheck";
import DeleteEnvironmentDialog from "./DeleteEnvironmentDialog";
import { runSummaries } from "../_mock/comparison";
import { runStatsOf } from "../_mock/runCompare";
import { currentEnvVersion, currentAgentVersion } from "../_mock/versions";
import { envLabelOf } from "../_mock/envVersionKinds";
import {
  agentSourceOf,
  connectedAgentsFrom,
  envAgentKey,
} from "../_mock/agentRegistry";
import { mainEnvOf, proofKindOf } from "../_mock/rsiModel";
import { catalogueOfEnv } from "../_mock/catalogue";

/* An environment's origin, for the list's filter. */
const ORIGINS = {
  world: "From a ready world",
  copy: "A copy or fork",
  agent: "Built from an agent",
};
const originOf = (env) =>
  env?.copyOf || env?.forkedFrom
    ? "copy"
    : catalogueOfEnv(env) || env?.templateId
      ? "world"
      : "agent";
import { identityOf } from "../agents/agentProfile";

/*
  Every environment is filed under the agent type it tests — the agent
  types Agents defines: voice and chat, with computer use for the browser
  worlds already built. Derived from the env's surface field, then a
  keyword scan of the name and description; anything else reads as chat
  (a code agent is a chat or voice agent whose source is code).
*/
const MODALITY = {
  voice: { id: "voice", label: "Voice", icon: "solar:microphone-3-linear" },
  chat: { id: "chat", label: "Chat", icon: "solar:chat-round-line-linear" },
  computer: {
    id: "computer",
    label: "Computer use",
    icon: "solar:monitor-linear",
  },
};

const modalityForEnv = (env) => {
  const surface = String(env?.surface || "").toLowerCase();
  const hay =
    `${env?.name || ""} ${env?.description || ""} ${env?.tagline || ""}`.toLowerCase();

  if (surface === "voice" || /\bvoice|phone|call\b/.test(hay))
    return MODALITY.voice;
  if (surface === "browser" || /browser|screen|desktop|computer.use/.test(hay))
    return MODALITY.computer;
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
const compactInputSx = {
  "& .MuiInputBase-root": { typography: "s2", height: 32 },
  "& .MuiOutlinedInput-input": { py: 0 },
};
const selectSx = {
  ...compactInputSx,
  minWidth: 170,
  "& .MuiSelect-select": { typography: "s2", py: 0 },
};

export default function MyEnvironmentsTable({
  envs,
  onOpen,
  hideStatus = false,
  withToolbar = false,
}) {
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

  /*
    Run from a row goes through the same checks as the workspace's Run
    button: readiness first (agent, where it runs, scenarios, evaluations) —
    not ready opens the workspace on the tab with the gap — then the tool
    fit, then the header's default of 3 repeats.
  */
  const [runTarget, setRunTarget] = useState(null);
  const fitEnv = menuFor?.env || runTarget;
  const { check: checkRunFit, dialog: runFitDialog } = useRunFitCheck(
    fitEnv,
    fitEnv ? state.byEnv[fitEnv.id] : null,
    (p) =>
      fitEnv && dispatch({ type: "patchEnvState", envId: fitEnv.id, patch: p }),
  );
  const runSimulation = (env) => {
    const readiness = runReadiness(state.byEnv[env.id], env);
    if (!readiness.ready) {
      navigate(
        readiness.href ||
          paths.dashboard.simulate.environmentStep(env.id, readiness.step),
      );
      return;
    }
    setRunTarget(env);
    checkRunFit(() => navigate(runPlanHref(env.id, { trials: 3 })));
  };
  const deleteEnv = (env) => {
    dispatch({ type: "removeEnvironment", envId: env.id });
    setConfirmDelete(null);
  };

  /* Every environment belongs to one agent in Agents — the same merged list
     Agents shows, so the name and the main environment agree with it. */
  const agents = useMemo(() => connectedAgentsFrom(state), [state]);

  /*
    Flatten envState-derived counts into each row up front, so column
    accessors stay simple and MUI X can sort on them without a valueGetter.
  */
  const rows = useMemo(
    () =>
      envs.map((env) => {
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
            if (!lastRun)
              return env.buildStatus === "building"
                ? "building"
                : env.buildStatus === "canceled"
                  ? "canceled"
                  : env.buildStatus === "failed"
                    ? "build_failed"
                    : "not_run";
            if (lastRun.status === "running") return "running";
            if (lastRun.status === "failed" || lastRun.gate === "failed")
              return "failed";
            if (lastRun.status === "passed" || lastRun.gate === "clear")
              return "passed";
            return "completed";
          })(),
          buildProgress: env.buildProgress,
          /* Canonical agent type — voice / chat / computer use. Sortable and filterable
           because it's flattened onto the row instead of derived in cell. */
          agentType: modalityForEnv(env).id,
          /* Tools count. Templates + user-connected agents ship a
           `tools` array. Twin/clone envs don't populate `tools`; their
           tool surface is the services they wrap (each service is a
           tool boundary), so fall back to that service count when the
           direct tools list is empty. */
          tools:
            env.tools?.length ||
            envState?.twinBacking?.services?.length ||
            env.builtFrom?.services?.length ||
            0,
          /* Distinct named sub-goals, as the Sub-goals tab and the build log
           count them — not each scenario's checks added up. Proposals don't
           count until someone adds them. */
          subgoals: (() => {
            try {
              return subGoalCatalogOf(env, {
                ...envState,
                scenarios: scenarios.filter((r) => !r.proposed),
              }).length;
            } catch {
              return 0;
            }
          })(),
          scenarios: scenarios.length,
          /* Building the env counts as its own first run — the reducer
           seeds a synthetic entry in envState.runs when the env is
           adopted, so both this list and the workspace's Runs step
           read the same data. No display-side flooring here. */
          runsPassed: runs.filter(
            (r) => r.status === "passed" || r.gate === "clear",
          ).length,
          runsTotal: runs.length,
          updatedAt: env.adoptedAt,
          managed: managedOf(env, envState),
          /* The environment's version, beside its name — two rows of the same
           name read apart by it. */
          version: (() => {
            const v = currentEnvVersion(env, envState)?.label;
            return v ? envLabelOf(env, envState, v) : null;
          })(),
          /* The agent it belongs to — its name in Agents, whether this is its
           main environment (where Fix runs), and whether its proofs are
           Sealed or Live. A template's stand-in isn't anyone's agent. */
          ...(() => {
            const own =
              envState?.agent && !envState.agent.seeded ? envState.agent : null;
            const key = own ? envAgentKey(own, env.id) : null;
            const agent = key ? agents.find((a) => a.key === key) : null;
            if (!own)
              return {
                agentName: null,
                agentSource: null,
                isMain: false,
                proof: null,
              };
            const proof = proofKindOf(own);
            return {
              agentName: agent?.name || own.name || `${env.name} agent`,
              agentSource: agentSourceOf(own).label,
              isMain: !!agent && mainEnvOf(agent, state)?.envId === env.id,
              proof: proof
                ? { kind: proof.kind, label: proof.label, note: proof.note }
                : null,
            };
          })(),
          /* The latest finished run's scenario pass rate. How it moved against
           the run before lives on the environment's Runs tab, not here. */
          lastRun: (() => {
            if (!envState || !runs.some((r) => r.finishedAt)) return null;
            try {
              const finished = runSummaries(env, envState).filter(
                (r) => r.finishedAt && !r.synthetic,
              );
              const latest = finished[finished.length - 1];
              if (!latest) return null;
              return {
                rate: runStatsOf(latest).scenarioRate,
                ordinal: latest.ordinal,
              };
            } catch {
              return null;
            }
          })(),
          /* Who owns the agent it tests — the person to ask about it. */
          owner: identityOf(envState?.agent).owner,
          agentVersion: currentAgentVersion(envState)?.label || null,
          /* Twin backing flag + service count for the inline chip on the
           name column. Lets scanning the gallery tell you at a glance
           which envs are twin-backed vs mocked-integration. */
          twinBacking: envState?.twinBacking || null,
        };
      }),
    [envs, state, agents],
  );

  const columns = useMemo(
    () => [
      {
        id: "name",
        accessorKey: "name",
        header: "Name",
        meta: { flex: 1 },
        minSize: 280,
        cell: ({ getValue, row }) => (
          <Box sx={{ minWidth: 0, width: "100%" }}>
            <Stack
              direction="row"
              alignItems="center"
              spacing={0.75}
              sx={{ minWidth: 0 }}
            >
              <Typography
                noWrap
                sx={{ typography: "s2", fontWeight: 600, minWidth: 0 }}
              >
                {getValue()}
              </Typography>
              {row.original.version && (
                <Tooltip arrow title="Environment version">
                  <Typography
                    component="span"
                    sx={{
                      typography: "s3",
                      color: "text.tertiary",
                      flexShrink: 0,
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {row.original.version}
                  </Typography>
                </Tooltip>
              )}
              {row.original.managed === "code" && (
                <Tooltip
                  arrow
                  title="Managed in code — versions come from agent-learn env save"
                >
                  <Iconify
                    icon="solar:code-square-linear"
                    width={14}
                    sx={{ color: "text.tertiary", flexShrink: 0 }}
                  />
                </Tooltip>
              )}
            </Stack>
            {/* What it is, under its name — the full line on hover. */}
            {row.original.description && (
              <Tooltip arrow title={row.original.description} enterDelay={500}>
                <Typography
                  noWrap
                  sx={{ typography: "s3", color: "text.subtitle", minWidth: 0 }}
                >
                  {row.original.description}
                </Typography>
              </Tooltip>
            )}
          </Box>
        ),
      },
      /* Simulated Runs opts the Status column out via hideStatus — a
         universal "Ready" pill on every row on that page just reads
         as noise (every listed env is by definition built). Other
         callers keep the pill. */
      ...(hideStatus
        ? []
        : [
            {
              id: "status",
              accessorKey: "status",
              header: "Status",
              size: 128,
              cell: ({ getValue, row }) => (
                <StatusPill
                  status={getValue()}
                  progress={row.original.buildProgress}
                />
              ),
            },
          ]),
      {
        id: "agentType",
        accessorKey: "agentType",
        header: "Agent type",
        size: 150,
        cell: ({ getValue }) => {
          const m = MODALITY[getValue()] || MODALITY.chat;
          return (
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
                <Iconify icon={m.icon} width={14} />
              </Box>
              <Typography noWrap sx={{ typography: "s2", minWidth: 0 }}>
                {m.label}
              </Typography>
            </Stack>
          );
        },
      },
      {
        id: "agent",
        accessorKey: "agentName",
        header: "Agent",
        size: 220,
        cell: ({ row }) => {
          const r = row.original;
          if (!r.agentName)
            return (
              <Typography sx={{ typography: "s2", color: "text.disabled" }}>
                —
              </Typography>
            );
          /* Name on top, its role and proofs as one quiet line under it —
             the same two-line rhythm as the Name column, no chips. */
          const detail = [r.isMain && "Main", r.proof?.label].filter(Boolean);
          const why = [
            r.isMain &&
              "Main — the agent's main environment; Fix runs here by default.",
            r.proof?.note && `${r.proof.label} — ${r.proof.note}`,
          ].filter(Boolean);
          return (
            <Box sx={{ minWidth: 0, width: "100%" }}>
              <Tooltip arrow title={r.agentSource || ""} enterDelay={400}>
                <Typography
                  noWrap
                  sx={{ typography: "s2", color: "text.secondary" }}
                >
                  {r.agentName}
                </Typography>
              </Tooltip>
              {detail.length > 0 && (
                <Tooltip
                  arrow
                  title={
                    why.length ? (
                      <Box sx={{ whiteSpace: "pre-line" }}>
                        {why.join("\n")}
                      </Box>
                    ) : (
                      ""
                    )
                  }
                >
                  <Typography
                    noWrap
                    sx={{
                      typography: "s3",
                      color: "text.tertiary",
                      cursor: "default",
                      width: "fit-content",
                      maxWidth: "100%",
                    }}
                  >
                    {detail.join(" · ")}
                  </Typography>
                </Tooltip>
              )}
            </Box>
          );
        },
      },
      {
        id: "lastRun",
        accessorFn: (r) => r.lastRun?.rate ?? -1,
        header: "Last run",
        size: 100,
        cell: ({ row }) => <LastRunCell last={row.original.lastRun} />,
      },
      {
        id: "scenarios",
        accessorKey: "scenarios",
        header: "Scenarios",
        size: 84,
        cell: ({ getValue }) => (
          <Typography
            sx={{
              typography: "s2",
              color: "text.secondary",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {getValue()}
          </Typography>
        ),
      },
      {
        id: "subgoals",
        accessorKey: "subgoals",
        header: "Sub-goals",
        size: 84,
        cell: ({ getValue }) => (
          <Typography
            sx={{
              typography: "s2",
              color: "text.secondary",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {getValue()}
          </Typography>
        ),
      },
      {
        id: "runs",
        accessorKey: "runsTotal",
        header: "Runs",
        size: 64,
        cell: ({ row }) => (
          <Typography
            sx={{
              typography: "s2",
              color: "text.secondary",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {row.original.runsTotal}
          </Typography>
        ),
      },
      {
        id: "updated",
        accessorKey: "updatedAt",
        header: "Updated",
        size: 100,
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
        size: 48,
        enableSorting: false,
        cell: ({ row }) => (
          <Stack
            direction="row"
            alignItems="center"
            spacing={0.5}
            sx={{ width: "100%", justifyContent: "flex-end" }}
          >
            <IconButton
              size="small"
              aria-label="Row actions"
              onClick={(e) => {
                e.stopPropagation();
                setMenuFor({
                  env: row.original.env,
                  anchorEl: e.currentTarget,
                });
              }}
              sx={{ color: "text.subtitle" }}
            >
              <Iconify icon="solar:menu-dots-bold" width={16} />
            </IconButton>
          </Stack>
        ),
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [hideStatus, state.byEnv],
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
  /* Search, status, agent type and agent — the toolbar above the table. */
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [typeFilter, setTypeFilter] = useState("all");
  const [agentFilter, setAgentFilter] = useState("all");
  /* Where it came from: a ready world, a copy of another, or built from an agent. */
  const [originFilter, setOriginFilter] = useState("all");
  const statusCounts = useMemo(
    () =>
      rows.reduce((m, r) => ({ ...m, [r.status]: (m[r.status] || 0) + 1 }), {}),
    [rows],
  );
  const typeChoices = useMemo(
    () => [...new Set(rows.map((r) => r.agentType))],
    [rows],
  );
  const agentChoices = useMemo(
    () =>
      [...new Set(rows.map((r) => r.agentName).filter(Boolean))].sort((a, b) =>
        a.localeCompare(b),
      ),
    [rows],
  );
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return rows.filter(
      (r) =>
        (!q ||
          `${r.name} ${r.description} ${r.agentName || ""} ${r.agentSource || ""} ${STATUS_META[r.status]?.label || ""}`
            .toLowerCase()
            .includes(q)) &&
        (statusFilter === "all" || r.status === statusFilter) &&
        (typeFilter === "all" || r.agentType === typeFilter) &&
        (agentFilter === "all" || r.agentName === agentFilter) &&
        (originFilter === "all" || originOf(r.env) === originFilter),
    );
  }, [rows, query, statusFilter, typeFilter, agentFilter, originFilter]);
  const filtering =
    !!query.trim() ||
    statusFilter !== "all" ||
    typeFilter !== "all" ||
    agentFilter !== "all" ||
    originFilter !== "all";

  const [pageSize, setPageSize] = useState(25);
  const [page, setPage] = useState(0);
  const currentPage = Math.min(
    page,
    Math.max(0, Math.ceil(shown.length / pageSize) - 1),
  );
  const pageRows = shown.slice(
    currentPage * pageSize,
    (currentPage + 1) * pageSize,
  );
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
      {withToolbar && (
        <ListToolbar flexWrap="wrap" rowGap={1}>
          <TextField
            size="small"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setPage(0);
            }}
            placeholder="Search by name, agent or status"
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
            sx={{ width: 280, ...compactInputSx }}
          />
          {/* Status, with how many are in each — a menu like the two beside it. */}
          <TextField
            select
            size="small"
            value={statusFilter}
            onChange={(e) => {
              setStatusFilter(e.target.value);
              setPage(0);
            }}
            SelectProps={{
              renderValue: (v) =>
                v === "all"
                  ? "All statuses"
                  : STATUS_META[v].label.replace("…", ""),
            }}
            inputProps={{ "aria-label": "Status" }}
            sx={selectSx}
          >
            {[
              "all",
              "running",
              "failed",
              "not_run",
              "completed",
              "passed",
              "building",
            ]
              .filter((k) => k === "all" || statusCounts[k])
              .map((k) => (
                <MenuItem key={k} value={k} sx={{ typography: "s2", gap: 1.5 }}>
                  <Box component="span" sx={{ flex: 1 }}>
                    {k === "all"
                      ? "All statuses"
                      : STATUS_META[k].label.replace("…", "")}
                  </Box>
                  <Box
                    component="span"
                    sx={{
                      color: "text.subtitle",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {k === "all" ? rows.length : statusCounts[k]}
                  </Box>
                </MenuItem>
              ))}
          </TextField>
          <TextField
            select
            size="small"
            value={typeFilter}
            onChange={(e) => {
              setTypeFilter(e.target.value);
              setPage(0);
            }}
            sx={selectSx}
          >
            <MenuItem value="all" sx={{ typography: "s2" }}>
              All agent types
            </MenuItem>
            {typeChoices.map((t) => (
              <MenuItem key={t} value={t} sx={{ typography: "s2" }}>
                {(MODALITY[t] || MODALITY.chat).label}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            select
            size="small"
            value={agentFilter}
            onChange={(e) => {
              setAgentFilter(e.target.value);
              setPage(0);
            }}
            inputProps={{ "aria-label": "Agent" }}
            sx={selectSx}
          >
            <MenuItem value="all" sx={{ typography: "s2" }}>
              All agents
            </MenuItem>
            {agentChoices.map((c) => (
              <MenuItem key={c} value={c} sx={{ typography: "s2" }}>
                {c}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            select
            size="small"
            value={originFilter}
            onChange={(e) => {
              setOriginFilter(e.target.value);
              setPage(0);
            }}
            inputProps={{ "aria-label": "Origin" }}
            sx={selectSx}
          >
            <MenuItem value="all" sx={{ typography: "s2" }}>
              All origins
            </MenuItem>
            {Object.entries(ORIGINS).map(([id, l]) => (
              <MenuItem key={id} value={id} sx={{ typography: "s2" }}>
                {l}
              </MenuItem>
            ))}
          </TextField>
          {filtering && (
            <Button
              size="small"
              onClick={() => {
                setQuery("");
                setStatusFilter("all");
                setTypeFilter("all");
                setAgentFilter("all");
                setOriginFilter("all");
                setPage(0);
              }}
              sx={{ color: "text.secondary" }}
            >
              {`Clear · ${shown.length} of ${rows.length}`}
            </Button>
          )}
        </ListToolbar>
      )}
      <DataTable
        columns={columns}
        data={pageRows}
        rowCount={shown.length}
        getRowId={(row) => row.id}
        onRowClick={(row) => onOpen?.(row.env)}
        rowHeight={44}
        emptyMessage={
          filtering
            ? "No environments match these filters"
            : "No environments yet"
        }
      />
      {shown.length > 0 && (
        <DataTablePagination
          page={currentPage}
          pageSize={pageSize}
          total={shown.length}
          onPageChange={setPage}
          onPageSizeChange={(n) => {
            setPageSize(n);
            setPage(0);
          }}
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
          onClick={() => {
            onOpen?.(active);
            setMenuFor(null);
          }}
          sx={{ typography: "s2" }}
        >
          <ListItemIcon sx={{ minWidth: 28 }}>
            <Iconify icon="solar:arrow-right-linear" width={16} />
          </ListItemIcon>
          <ListItemText
            primary="Open"
            primaryTypographyProps={{ sx: { typography: "s2" } }}
          />
        </MenuItem>
        <MenuItem
          onClick={() => {
            navigate(
              `${paths.dashboard.simulate.environmentDetail(active.id)}?copy=1`,
            );
            setMenuFor(null);
          }}
          sx={{ typography: "s2" }}
        >
          <ListItemIcon sx={{ minWidth: 28 }}>
            <Iconify icon="solar:copy-linear" width={16} />
          </ListItemIcon>
          <ListItemText
            primary="Make a copy"
            primaryTypographyProps={{ sx: { typography: "s2" } }}
          />
        </MenuItem>
        <Tooltip
          arrow
          placement="left"
          title={
            buildingActive
              ? "Wait for setup to finish"
              : active && !runReadiness(state.byEnv[active.id], active).ready
                ? `${runReadiness(state.byEnv[active.id], active).reason} — opens ${runReadiness(state.byEnv[active.id], active).href ? "the agent" : "the environment"} there`
                : ""
          }
        >
          <Box>
            <MenuItem
              disabled={buildingActive}
              onClick={() => {
                runSimulation(active);
                setMenuFor(null);
              }}
              sx={{ typography: "s2" }}
            >
              <ListItemIcon sx={{ minWidth: 28 }}>
                <Iconify icon="solar:play-linear" width={16} />
              </ListItemIcon>
              <ListItemText
                primary={
                  active?.buildStatus === "ready" &&
                  (state.byEnv[active?.id]?.runs?.length || 0) > 0
                    ? "Re-run simulation"
                    : "Run simulation"
                }
                primaryTypographyProps={{ sx: { typography: "s2" } }}
              />
            </MenuItem>
          </Box>
        </Tooltip>
        <MenuItem
          onClick={() => {
            setConfirmDelete(active);
            setMenuFor(null);
          }}
          // eslint-disable-next-line no-restricted-syntax -- ds: destructive menu item
          sx={{ typography: "s2", color: "status.danger.fg" }}
        >
          <ListItemIcon sx={{ minWidth: 28, color: "status.danger.fg" }}>
            <Iconify icon="solar:trash-bin-trash-linear" width={16} />
          </ListItemIcon>
          <ListItemText
            primary="Delete"
            primaryTypographyProps={{
              sx: { typography: "s2", color: "status.danger.fg" },
            }}
          />
        </MenuItem>
      </Menu>

      <DeleteEnvironmentDialog
        env={confirmDelete}
        onCancel={() => setConfirmDelete(null)}
        onConfirm={deleteEnv}
      />
      {runFitDialog}
    </>
  );
}

MyEnvironmentsTable.propTypes = {
  envs: PropTypes.array.isRequired,
  onOpen: PropTypes.func,
  hideStatus: PropTypes.bool,
  /* Search, status, agent type and agent above the table. */
  withToolbar: PropTypes.bool,
};

/* ── status pill ─────────────────────────────────────────────────────────── */

/* Run state on the environments list, through the one status map's roles:
   building and running are in progress (info), passed and failed are the
   gate's verdict, and not run / completed / canceled carry no verdict. */
const STATUS_META = {
  building: { label: "Building", role: "info" },
  /* A build someone stopped, or one that failed part-way — open it to restart or retry. */
  canceled: { label: "Build canceled", role: "neutral" },
  build_failed: { label: "Build failed", role: "danger" },
  not_run: { label: "Not run yet", role: "neutral" },
  running: { label: "Running…", role: "info" },
  passed: { label: "Passed", role: "success" },
  failed: { label: "Failed", role: "danger" },
  /* A finished run with no gate verdict: neutral, like the Improvements
     table's endings that just stopped — nothing to celebrate or flag. */
  completed: { label: "Completed", role: "neutral" },
};

/* The design system's status badge; the build's progress on hover. */
function StatusPill({ status, progress }) {
  const meta = STATUS_META[status] || STATUS_META.not_run;
  const detail =
    status === "building" && progress
      ? `${progress.done}/${progress.total} steps`
      : "";
  return (
    <Tooltip arrow title={detail} disableHoverListener={!detail}>
      <StatusBadge status={status} role={meta.role} label={meta.label} />
    </Tooltip>
  );
}

StatusPill.propTypes = { status: PropTypes.string, progress: PropTypes.object };

/* ── last run ────────────────────────────────────────────────────────────── */

/* "88% ▲9" — the latest run's scenario pass rate and how it moved; grey
   when the move is inside the noise two runs of that size carry. */
function LastRunCell({ last }) {
  if (!last || last.rate == null)
    return (
      <Typography sx={{ typography: "s2", color: "text.disabled" }}>
        —
      </Typography>
    );
  return (
    <Tooltip arrow title={`Run ${last.ordinal} · scenarios passed`}>
      <Typography
        sx={{
          typography: "s2",
          fontWeight: 600,
          fontVariantNumeric: "tabular-nums",
          cursor: "default",
        }}
      >
        {`${last.rate}%`}
      </Typography>
    </Tooltip>
  );
}
LastRunCell.propTypes = { last: PropTypes.object };

/* ── owner ───────────────────────────────────────────────────────────────── */

/* The owner's initials, drawn exactly as the Dashboards list draws its
   people (the same AvatarGroup + Avatar, same size); their name on hover. */
const initialsOf = (name) => {
  const parts = String(name || "")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  if (!parts.length) return "?";
  return (
    parts.length >= 2 ? parts[0][0] + parts[1][0] : parts[0].slice(0, 2)
  ).toUpperCase();
};
/* ── runs pill ───────────────────────────────────────────────────────────── */

/* ── relative time ───────────────────────────────────────────────────────── */

function relativeTime(iso) {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const diffMs = Date.now() - then;
  const minutes = Math.floor(diffMs / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60)
    return `${minutes} ${minutes === 1 ? "minute" : "minutes"} ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} ${hours === 1 ? "hour" : "hours"} ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} ${days === 1 ? "day" : "days"} ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months} ${months === 1 ? "month" : "months"} ago`;
  const years = Math.floor(months / 12);
  return `${years} ${years === 1 ? "year" : "years"} ago`;
}
