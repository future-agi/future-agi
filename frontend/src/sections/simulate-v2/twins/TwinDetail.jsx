import PropTypes from "prop-types";
import { useMemo, useState, useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, IconButton, Tooltip, Chip, Divider,
  Menu, MenuItem, Dialog, DialogTitle, DialogContent, DialogActions,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { useSimStore, useEnvState } from "../store";
import { twinById } from "../_mock/twins";
import { protoRunId } from "../_mock/executionAdapter";
import TwinLogo from "../components/TwinLogo";
import SlackSandboxMock from "./SlackSandboxMock";
import NotionSandboxMock from "./NotionSandboxMock";
import GmailSandboxMock from "./GmailSandboxMock";
import SalesforceSandboxMock from "./SalesforceSandboxMock";
import GenericSandboxMock from "./GenericSandboxMock";
import TwinControlsDrawer from "./TwinControlsDrawer";
import { OpenApiDialog, OpenSurfaceDialog } from "./TwinSandboxDialogs";

const SANDBOX_MOCKS = {
  slack: SlackSandboxMock,
  notion: NotionSandboxMock,
  gmail: GmailSandboxMock,
  salesforce: SalesforceSandboxMock,
};

const TWIN_TINT = "#7857FC";
const SUCCESS = "#16A34A";

/**
 * Twin detail page — the primary surface for a twin-backed env.
 *
 * Modelled on Arga Labs' twin detail view: the star of the page is
 * the live sandbox mock, not the scenarios/evals/runs tabs we use
 * for other envs. Those exist too — reachable via the "Advanced"
 * link — but the default view is what makes twins tangible: "here's
 * a real Slack workspace your agent is talking to."
 *
 * Layout:
 *   · Header — back arrow, env name, created date, N surfaces, Ready pill
 *   · Service chips — one per twinned service (currently just single-
 *     service on the primary surface; multi-service twins show extra
 *     chips and the user can switch which one is rendered)
 *   · Sub-header — service icon, "Serving" status, sandbox URL,
 *     OpenAPI + Open surface buttons
 *   · Sandbox mock — the live-looking service preview (Slack today,
 *     generic placeholder for the rest)
 *   · Right rail — previous twin runs, "Advanced" link to full workspace
 */
export default function TwinDetail() {
  const { envId } = useParams();
  const navigate = useNavigate();
  const { state } = useSimStore();

  const env = state.myEnvironments?.find((e) => e.id === envId);
  const { envState, patch } = useEnvState(envId);
  const backing = envState?.twinBacking;
  const runs = envState?.runs || [];
  const [controlsOpen, setControlsOpen] = useState(false);
  const [runMenuAnchor, setRunMenuAnchor] = useState(null);
  const [openApiDialog, setOpenApiDialog] = useState(false);
  const [surfaceDialog, setSurfaceDialog] = useState(false);

  const startRun = (scenarioId) => {
    setRunMenuAnchor(null);
    const runId = protoRunId(envId, Date.now().toString(36));
    const url = scenarioId
      ? `${paths.dashboard.simulate.simulationRun(envId, runId)}?only=${scenarioId}`
      : paths.dashboard.simulate.simulationRun(envId, runId);
    navigate(url);
  };

  /*
    On multi-service twin envs the user can switch which service's
    sandbox is being shown. Defaults to the first service in the
    backing; chip clicks change it. Kept in local state (not URL)
    to stay lightweight — the user's current focus doesn't need to
    survive a reload.
  */
  const [activeServiceId, setActiveServiceId] = useState(backing?.services?.[0]);
  useEffect(() => {
    if (backing?.services?.length && !backing.services.includes(activeServiceId)) {
      setActiveServiceId(backing.services[0]);
    }
  }, [backing?.services, activeServiceId]);

  const activeTwin = useMemo(() => twinById(activeServiceId), [activeServiceId]);
  const SandboxMock = SANDBOX_MOCKS[activeServiceId] || null;

  if (!env || !backing) {
    return (
      <Box sx={{ p: 4 }}>
        <Typography sx={{ typography: "s1", fontWeight: 700 }}>
          Clone environment not found
        </Typography>
        <Typography sx={{ typography: "s2", color: "text.subtitle", mb: 2 }}>
          It may have been deleted, or the URL is stale.
        </Typography>
        <Button
          variant="outlined" size="small"
          onClick={() => navigate(paths.dashboard.simulate.twins)}
        >
          Back to Clones
        </Button>
      </Box>
    );
  }

  const created = backing.provisionedAt
    ? new Date(backing.provisionedAt).toLocaleString(undefined, {
        day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
      })
    : "—";

  return (
    <Box sx={{ p: 3 }}>
      {/* ── header ── */}
      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 2 }}>
        <Tooltip arrow title="Back to Clones">
          <IconButton size="small" onClick={() => navigate(paths.dashboard.simulate.twins)}>
            <Iconify icon="solar:alt-arrow-left-linear" width={17} />
          </IconButton>
        </Tooltip>
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "m2", fontWeight: 700 }} noWrap>
            {env.name}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            Created {created} · {backing.services.length} surface{backing.services.length === 1 ? "" : "s"}
          </Typography>
        </Box>
        <ReadyPill />
      </Stack>

      {/* service chips — clickable when multi-service; single-service
          envs render one non-interactive chip. Active chip carries the
          twin tint so the selected surface is visible at a glance. */}
      <Stack direction="row" spacing={0.75} sx={{ mb: 2.5 }} flexWrap="wrap" useFlexGap>
        {backing.services.map((sId) => {
          const t = twinById(sId);
          const on = sId === activeServiceId;
          const multi = backing.services.length > 1;
          return (
            <Chip
              key={sId} size="small"
              onClick={multi ? () => setActiveServiceId(sId) : undefined}
              icon={<TwinLogo twin={t} width={11} sx={{ ml: "6px !important" }} />}
              label={t?.name || sId}
              sx={{
                height: 26, borderRadius: 999,
                cursor: multi ? "pointer" : "default",
                border: "1px solid",
                borderColor: (th) => on
                  ? alpha(TWIN_TINT, th.palette.mode === "dark" ? 0.5 : 0.35)
                  : th.palette.divider,
                bgcolor: (th) => on
                  ? alpha(TWIN_TINT, th.palette.mode === "dark" ? 0.14 : 0.06)
                  : "background.paper",
                color: on ? TWIN_TINT : "text.primary",
                "& .MuiChip-label": { pl: 0.5, pr: 1, typography: "s2", fontWeight: 700 },
                "&:hover": multi ? {
                  bgcolor: (th) => on
                    ? alpha(TWIN_TINT, th.palette.mode === "dark" ? 0.18 : 0.09)
                    : alpha(th.palette.text.primary, th.palette.mode === "dark" ? 0.04 : 0.03),
                } : undefined,
              }}
            />
          );
        })}
      </Stack>

      {/* ── main + right rail ── */}
      <Box sx={{
        display: "grid", gap: 2.5,
        gridTemplateColumns: { xs: "1fr", lg: "minmax(0, 1fr) 320px" },
      }}>
        {/* main */}
        <Box>
          {/* sub-header */}
          <Stack
            direction="row" alignItems="center" spacing={1.25}
            sx={{ mb: 1.5 }}
          >
            <TwinLogo twin={activeTwin} width={22} />
            <Box>
              <Stack direction="row" alignItems="baseline" spacing={0.75}>
                <Typography sx={{ typography: "s1_2", fontWeight: 700 }}>
                  {activeTwin?.name || "Sandbox"}
                </Typography>
                <Stack direction="row" alignItems="center" spacing={0.5}>
                  <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: SUCCESS }} />
                  <Typography sx={{ typography: "s3", fontWeight: 700, color: SUCCESS }}>
                    Serving
                  </Typography>
                </Stack>
              </Stack>
              <Typography sx={{
                typography: "s3", color: "text.subtitle",
                fontFamily: "ui-monospace, Menlo, monospace",
              }}>
                {backing.endpoints?.[activeServiceId] || "—"}
              </Typography>
            </Box>
            <Box flex={1} />
            <Button
              variant="outlined" size="small"
              onClick={() => setOpenApiDialog(true)}
              startIcon={<Iconify icon="solar:code-square-linear" width={13} />}
              sx={{
                typography: "s2", fontWeight: 700, color: "text.primary",
                borderColor: "divider",
              }}
            >
              OpenAPI
            </Button>
            <Button
              variant="outlined" size="small"
              onClick={() => setSurfaceDialog(true)}
              startIcon={<Iconify icon="solar:square-top-down-linear" width={13} />}
              sx={{
                typography: "s2", fontWeight: 700, color: "text.primary",
                borderColor: "divider",
              }}
            >
              Open surface
            </Button>
            <Button
              variant="contained" color="primary" size="small"
              onClick={(e) => setRunMenuAnchor(e.currentTarget)}
              endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={12} />}
              startIcon={<Iconify icon="solar:play-circle-bold" width={13} />}
              sx={{ typography: "s2", fontWeight: 700 }}
            >
              Run a scenario
            </Button>
          </Stack>

          {/* sandbox surface — dispatched by active service. Services with
              a bespoke mock render it; the rest fall back to the generic
              "sandbox is serving" preview. */}
          {SandboxMock
            ? <SandboxMock workspace={env.name} />
            : <GenericSandboxMock twin={activeTwin} />}

          {/* Live activity ticker for the active service — updates
              deterministically per run count so it feels alive without
              needing a real websocket. */}
          <ActivityTicker
            activeServiceId={activeServiceId}
            services={backing.services}
            runs={runs}
          />

          {/* controls */}
          <Stack direction="row" spacing={1} sx={{ mt: 1.5 }}>
            <Button
              variant="outlined" size="small"
              onClick={() => setControlsOpen(true)}
              startIcon={<Iconify icon="solar:settings-linear" width={13} />}
              sx={{
                typography: "s2", fontWeight: 700, color: "text.primary",
                borderColor: "divider",
              }}
            >
              Controls
            </Button>
            <Box flex={1} />
            {/*
              The env workspace stays reachable for scenarios/evals/
              versions/runs — those still live where every other env
              keeps them. This is the escape hatch to that surface.
            */}
            <Button
              variant="text" size="small"
              onClick={() => navigate(paths.dashboard.simulate.environmentDetail(envId))}
              endIcon={<Iconify icon="solar:arrow-right-linear" width={13} />}
              sx={{ typography: "s2", fontWeight: 700, color: "text.secondary" }}
            >
              Scenarios, evals & runs
            </Button>
          </Stack>
        </Box>

        {/* right rail */}
        <Box>
          <Stack direction="row" alignItems="baseline" spacing={0.75} sx={{ mb: 1 }}>
            <Typography sx={{ typography: "s1_2", fontWeight: 700 }}>Previous runs</Typography>
            <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
              {runs.length} saved
            </Typography>
          </Stack>
          {runs.length === 0 ? (
            <Box sx={{
              p: 2.5, borderRadius: 1.5, border: "1px dashed", borderColor: "divider",
              textAlign: "center",
            }}>
              <Typography sx={{ typography: "s2", fontWeight: 700 }}>No saved runs yet</Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.5 }}>
                New runs will appear here automatically.
              </Typography>
              <Button
                variant="contained" color="primary" size="small"
                onClick={() => navigate(paths.dashboard.simulate.environmentDetail(envId))}
                startIcon={<Iconify icon="solar:play-circle-linear" width={13} />}
                sx={{ typography: "s2", fontWeight: 700, mt: 1.75 }}
              >
                Run a scenario
              </Button>
            </Box>
          ) : (
            <Stack spacing={0.75}>
              {runs.slice(0, 8).map((r) => (
                <Stack
                  key={r.id} direction="row" alignItems="center" spacing={1}
                  onClick={() => navigate(paths.dashboard.simulate.simulationRun(envId, r.id))}
                  sx={{
                    px: 1.5, py: 1, borderRadius: 1,
                    border: "1px solid", borderColor: "divider",
                    bgcolor: "background.paper", cursor: "pointer",
                    "&:hover": { borderColor: "text.disabled" },
                  }}
                >
                  <Box sx={{
                    width: 20, height: 20, borderRadius: 0.5,
                    display: "grid", placeItems: "center",
                    color: r.color,
                    bgcolor: (t) => alpha(r.color || TWIN_TINT, t.palette.mode === "dark" ? 0.22 : 0.14),
                    typography: "s3", fontWeight: 700, flexShrink: 0,
                  }}>
                    {r.letter || "R"}
                  </Box>
                  <Box flex={1} minWidth={0}>
                    <Typography noWrap sx={{ typography: "s2", fontWeight: 700 }}>
                      Run {r.ordinal || "—"} · agent {r.agentVersion}
                    </Typography>
                    <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
                      {r.passed}/{r.total} passed
                      {typeof r.twinWrites === "number" && ` · ${r.twinWrites} writes`}
                    </Typography>
                  </Box>
                  <Iconify icon="eva:arrow-ios-forward-fill" width={13} sx={{ color: "text.subtitle", flexShrink: 0 }} />
                </Stack>
              ))}
              {runs.length > 8 && (
                <Button
                  size="small"
                  onClick={() => navigate(paths.dashboard.simulate.environmentStep(envId, "runs"))}
                  sx={{ typography: "s2", fontWeight: 700, color: "text.secondary", justifyContent: "flex-start" }}
                >
                  View all {runs.length} runs
                </Button>
              )}
            </Stack>
          )}

          <Divider sx={{ my: 2 }} />

          <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.4, mb: 0.75 }}>
            Sandbox
          </Typography>
          <Stack spacing={0.5}>
            <MetaRow label="Provisioned" value={created} />
            <MetaRow label="Services" value={`${backing.services.length}`} />
            <MetaRow label="Reset per run" value={envState?.twinResetBetween === false ? "No" : "Yes"} />
          </Stack>
        </Box>
      </Box>

      <TwinControlsDrawer
        open={controlsOpen}
        onClose={() => setControlsOpen(false)}
        envId={envId}
        backing={backing}
        patch={patch}
      />

      <RunScenarioMenu
        anchorEl={runMenuAnchor}
        scenarios={envState?.scenarios || []}
        onClose={() => setRunMenuAnchor(null)}
        onPick={startRun}
      />

      <OpenApiDialog
        open={openApiDialog}
        onClose={() => setOpenApiDialog(false)}
        twin={activeTwin}
        endpoint={backing.endpoints?.[activeServiceId] || ""}
      />

      <OpenSurfaceDialog
        open={surfaceDialog}
        onClose={() => setSurfaceDialog(false)}
        twin={activeTwin}
        env={env}
        SandboxMock={SandboxMock}
      />
    </Box>
  );
}

/* ── bits ────────────────────────────────────────────────────────────── */

function ReadyPill() {
  return (
    <Stack
      direction="row" alignItems="center" spacing={0.5}
      sx={{
        px: 1, height: 24, borderRadius: 999, flexShrink: 0,
        color: SUCCESS,
        bgcolor: (t) => alpha(SUCCESS, t.palette.mode === "dark" ? 0.16 : 0.1),
        border: (t) => `1px solid ${alpha(SUCCESS, t.palette.mode === "dark" ? 0.4 : 0.28)}`,
      }}
    >
      <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: SUCCESS }} />
      <Typography sx={{ typography: "s3", fontWeight: 700 }}>Ready</Typography>
    </Stack>
  );
}

function MetaRow({ label, value }) {
  return (
    <Stack direction="row" justifyContent="space-between" spacing={1}>
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{label}</Typography>
      <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.primary" }} noWrap>
        {value}
      </Typography>
    </Stack>
  );
}
MetaRow.propTypes = { label: PropTypes.string, value: PropTypes.string };

/* ── run-a-scenario menu ─────────────────────────────────────────────── */

function RunScenarioMenu({ anchorEl, scenarios, onClose, onPick }) {
  const items = scenarios.slice(0, 8);
  return (
    <Menu
      anchorEl={anchorEl} open={!!anchorEl} onClose={onClose}
      anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
      transformOrigin={{ vertical: "top", horizontal: "right" }}
      slotProps={{
        paper: {
          sx: {
            mt: 0.75, minWidth: 340, maxWidth: 420,
            borderRadius: 1.25, border: "1px solid", borderColor: "divider",
            bgcolor: "background.paper", backgroundImage: "none",
          },
        },
      }}
    >
      <Box sx={{ px: 1.5, pt: 1.25, pb: 0.75 }}>
        <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.4 }}>
          Starter scenarios
        </Typography>
      </Box>
      {items.length === 0 ? (
        <Box sx={{ px: 1.5, pb: 1.5 }}>
          <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
            No scenarios yet — head to the env workspace to add some.
          </Typography>
        </Box>
      ) : (
        items.map((sc) => (
          <MenuItem
            key={sc.id} onClick={() => onPick(sc.id)}
            sx={{ typography: "s2", px: 1.5, py: 1, whiteSpace: "normal" }}
          >
            <Box sx={{ minWidth: 0 }}>
              <Typography sx={{ typography: "s2", fontWeight: 700 }} noWrap>
                {sc.title || sc.name}
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle", lineHeight: 1.4 }}>
                {sc.task}
              </Typography>
            </Box>
          </MenuItem>
        ))
      )}
      <Divider sx={{ my: 0.5 }} />
      <MenuItem
        onClick={() => onPick(null)}
        sx={{ typography: "s2", fontWeight: 700, px: 1.5, py: 1, color: TWIN_TINT }}
      >
        <Iconify icon="solar:play-circle-linear" width={13} sx={{ mr: 1, color: TWIN_TINT }} />
        Run all {scenarios.length} scenarios
      </MenuItem>
    </Menu>
  );
}
RunScenarioMenu.propTypes = {
  anchorEl: PropTypes.any,
  scenarios: PropTypes.array,
  onClose: PropTypes.func,
  onPick: PropTypes.func,
};
/* ── activity ticker ─────────────────────────────────────────────────── */

/**
 * A four-stat strip under the sandbox mock: writes + reads for the
 * active service, total across all services, and last-touched. Values
 * derive deterministically from the run history so they grow as runs
 * land and stay stable across reloads.
 */
function ActivityTicker({ activeServiceId, services, runs }) {
  const totalRuns = runs.length;
  const seed = hash(activeServiceId);
  const writesPerRun = 2 + ((seed) % 4);
  const readsPerRun = 4 + ((seed * 3) % 5);
  const serviceWrites = totalRuns * writesPerRun;
  const serviceReads = totalRuns * readsPerRun;
  const totalWrites = services.reduce((sum, s) => {
    const h = hash(s);
    return sum + totalRuns * (2 + (h % 4));
  }, 0);
  const lastRun = runs[0];
  const lastAt = lastRun?.finishedAt ? timeAgo(lastRun.finishedAt) : "—";

  return (
    <Stack
      direction="row" spacing={0} sx={{
        mt: 1.5, borderRadius: 1, overflow: "hidden",
        border: "1px solid", borderColor: "divider",
      }}
      divider={<Box sx={{ borderRight: "1px solid", borderColor: "divider" }} />}
    >
      <Stat label="Writes on this service" value={serviceWrites} tint={TWIN_TINT} big />
      <Stat label="Reads on this service" value={serviceReads} />
      <Stat label="Total sandbox writes" value={totalWrites} />
      <Stat label="Last run" value={lastAt} mono />
    </Stack>
  );
}
ActivityTicker.propTypes = {
  activeServiceId: PropTypes.string,
  services: PropTypes.array,
  runs: PropTypes.array,
};

function Stat({ label, value, tint, big, mono }) {
  return (
    <Box sx={{ flex: 1, p: 1.5, minWidth: 0 }}>
      <Typography sx={{
        typography: big ? "s1_2" : "s2", fontWeight: 700,
        color: tint || "text.primary",
        fontVariantNumeric: "tabular-nums",
        fontFamily: mono ? "ui-monospace, Menlo, monospace" : undefined,
      }} noWrap>
        {value}
      </Typography>
      <Typography sx={{
        typography: "s3", color: "text.subtitle",
        textTransform: "uppercase", letterSpacing: 0.4, mt: 0.125,
      }} noWrap>
        {label}
      </Typography>
    </Box>
  );
}
Stat.propTypes = {
  label: PropTypes.string, value: PropTypes.node, tint: PropTypes.string,
  big: PropTypes.bool, mono: PropTypes.bool,
};

function hash(s = "") {
  let h = 0;
  for (let i = 0; i < s.length; i += 1) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}
function timeAgo(iso) {
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
