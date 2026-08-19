import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { alpha } from "@mui/material/styles";
import PropTypes from "prop-types";
import { Box, Stack, Typography, Button, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { getEnvironment } from "../_mock/environments";
import { getSurface } from "../_mock/surfaces";
import { BOOT_STEPS } from "../_mock/runStream";
import { useSimStore, useEnvState } from "../store";
import { SurfaceIcon, EmptyState } from "../components/primitives";
import { ProvisioningPanel } from "../components/loading";
import OverviewPanel from "./OverviewPanel";
import ConnectAgentStep from "./ConnectAgentStep";
import ScenariosStep from "./ScenariosStep";
import EvalsStep from "./EvalsStep";
import RunsPanel from "./RunsPanel";
import InstancesPanel from "./InstancesPanel";
import FilesPanel from "./FilesPanel";
import SettingsPanel from "./SettingsPanel";

/**
 * `setup` marks the items that gate a run — those carry a completion tick and
 * feed the progress counter. The rest are views of the environment itself, so
 * the rail splits them rather than presenting eight equal steps.
 */
const STEPS = [
  { id: "overview",  label: "Overview",  icon: "solar:widget-5-linear", group: "Setup" },
  { id: "agent",     label: "Agent",     icon: "solar:cpu-bolt-linear", group: "Setup", setup: true },
  { id: "scenarios", label: "Scenarios", icon: "solar:layers-minimalistic-linear", group: "Setup", setup: true },
  { id: "evals",     label: "Evals",     icon: "solar:shield-check-linear", group: "Setup", setup: true },
  { id: "runs",      label: "Runs",      icon: "solar:play-circle-linear", group: "Setup" },
  { id: "instances", label: "Instances", icon: "solar:server-square-linear", group: "Environment" },
  { id: "files",     label: "Files",     icon: "solar:folder-linear", group: "Environment" },
  { id: "settings",  label: "Settings",  icon: "solar:settings-minimalistic-linear", group: "Environment" },
];

const RAIL_GROUPS = ["Setup", "Environment"];

/**
 * The environment workspace.
 *
 * Once you are inside an environment, everything else is configuration of that
 * environment — so this is a persistent shell with a progress rail rather than
 * a linear wizard you fall out of. The rail doubles as navigation and as the
 * "what is still missing before I can run" answer.
 */
export default function EnvironmentWorkspace() {
  const { envId, step = "overview" } = useParams();
  const navigate = useNavigate();
  const { state, dispatch } = useSimStore();

  const env =
    getEnvironment(envId) || state.myEnvironments.find((e) => e.id === envId);

  const { envState, patch, steps, canRun } = useEnvState(envId);

  // Opening an environment goes straight to it — a boot sequence on every entry
  // is a delay the user did not ask for. The sequence is kept for "Reset state",
  // where re-provisioning is the whole point of pressing the button.
  const [booting, setBooting] = useState(false);

  // Adopt on direct navigation so a deep link works from a cold start.
  useEffect(() => {
    if (env && !state.myEnvironments.some((e) => e.id === env.id)) {
      dispatch({ type: "adoptEnvironment", env, now: new Date().toISOString() });
    }
  }, [env, state.myEnvironments, dispatch]);

  if (!env) {
    return (
      <Box sx={{ p: 2 }}>
        <EmptyState
          icon="solar:danger-triangle-linear"
          title="Environment not found"
          body="It may have been removed from your workspace."
          action={
            <Button variant="contained" color="primary" size="small" onClick={() => navigate(paths.dashboard.simulate.environments)}>
              Back to environments
            </Button>
          }
        />
      </Box>
    );
  }

  const surface = getSurface(env.surface);

  // Booting the environment is a real thing we do, so we show it happening.
  if (booting) {
    return (
      <Box sx={{ p: 2, height: "100%", minHeight: 420, display: "grid", placeItems: "center" }}>
        <Box sx={{ width: "100%", maxWidth: 520, border: "1px solid", borderColor: "divider", borderRadius: 2, bgcolor: "background.paper" }}>
          <ProvisioningPanel
            icon={surface.icon}
            accent={surface.color}
            title={`Starting ${env.name}`}
            subtitle="Restoring a clean copy of the environment for this session."
            steps={BOOT_STEPS[surface.stage] || BOOT_STEPS.voice}
            onDone={() => setBooting(false)}
          />
        </Box>
      </Box>
    );
  }

  const go = (s) => navigate(paths.dashboard.simulate.environmentStep(envId, s));

  // Unknown steps render Overview, and the rail highlights it, so the URL and
  // the highlighted item never disagree.
  /*
    Evals still need scenarios before anything can be added — an eval's
    variables map onto what a run produces — but that is enforced *on* the
    Evals step, which says so and offers the way forward. Redirecting to
    Scenarios instead made the rail item look broken: you click Evals and
    nothing appears to happen.
  */
  const panel = STEPS.some((s) => s.id === step) ? step : "overview";

  const doneById = Object.fromEntries(steps.map((s) => [s.id, s.done]));
  const counts = {
    scenarios: envState.scenarios.length || null,
    evals: envState.evals.length || null,
    runs: envState.runs.length || null,
  };


  const setupSteps = STEPS.filter((s) => s.setup);
  const setupDone = setupSteps.filter((s) => doneById[s.id]).length;
  const setupTotal = setupSteps.length;

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      {/* ── environment header ── */}
      <Stack
        direction="row"
        alignItems="center"
        spacing={2}
        sx={{ px: 3, py: 2, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
      >
        <Tooltip title="All environments" arrow>
          <Button
            onClick={() => navigate(paths.dashboard.simulate.environments)}
            sx={{ minWidth: 32, width: 32, height: 32, p: 0, color: "text.subtitle" }}
          >
            <Iconify icon="solar:alt-arrow-left-linear" width={18} />
          </Button>
        </Tooltip>

        <SurfaceIcon surface={env.surface} size={36} />

        <Box minWidth={0} flex={1}>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography noWrap sx={{ typography: "s1_2", fontWeight: 700 }}>{env.name}</Typography>
            <Stack
              direction="row" alignItems="center" spacing={0.5}
              sx={{
                px: 0.75, height: 22, borderRadius: 0.75,
                color: "#16A34A",
                bgcolor: (t) => alpha("#16A34A", t.palette.mode === "dark" ? 0.16 : 0.1),
                border: () => `1px solid ${alpha("#16A34A", 0.24)}`,
              }}
            >
              <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: "#16A34A" }} />
              <Typography sx={{ typography: "s3", fontWeight: 600 }}>Live</Typography>
            </Stack>
          </Stack>
          <Typography noWrap sx={{ typography: "s2", color: "text.subtitle" }}>{env.tagline}</Typography>
        </Box>

        <Button
          variant="outlined"
          size="small"
          startIcon={<Iconify icon="solar:restart-linear" width={16} />}
          onClick={() => setBooting(true)}
          sx={{ color: "text.primary", borderColor: "divider", typography: "s2", fontWeight: 600 }}
        >
          Reset state
        </Button>
        <Tooltip title={canRun ? "" : "Connect an agent and add scenarios first"} arrow>
          <span>
            <Button
              variant="contained"
              color="primary"
              size="small"
              disabled={!canRun}
              onClick={() => go("runs")}
              startIcon={<Iconify icon="solar:play-bold" width={15} />}
              sx={{ typography: "s2", fontWeight: 700 }}
            >
              Run simulation
            </Button>
          </span>
        </Tooltip>
      </Stack>

      {/* ── body: rail + panel ── */}
      <Box sx={{ display: "flex", flex: 1, minHeight: 0 }}>
        <SetupRail
          current={panel}
          onGo={go}
          accent={surface.color}
          doneById={doneById}
          counts={counts}
          setupDone={setupDone}
          setupTotal={setupTotal}
        />

        {/*
          Fall back to Overview for an unrecognised step rather than rendering
          an empty pane — a bad link should land somewhere, not nowhere.
        */}
        <Box sx={{ flex: 1, minWidth: 0, overflow: "auto" }}>
          {panel === "agent" ? (
            <ConnectAgentStep env={env} envState={envState} patch={patch} onGo={go} />
          ) : panel === "scenarios" ? (
            <ScenariosStep env={env} envState={envState} patch={patch} onGo={go} />
          ) : panel === "evals" ? (
            <EvalsStep env={env} envState={envState} patch={patch} onGo={go} />
          ) : panel === "runs" ? (
            <RunsPanel env={env} envState={envState} onGo={go} />
          ) : panel === "instances" ? (
            <InstancesPanel env={env} onGo={go} />
          ) : panel === "files" ? (
            <FilesPanel env={env} />
          ) : panel === "settings" ? (
            <SettingsPanel env={env} />
          ) : (
            <OverviewPanel env={env} envState={envState} patch={patch} onGo={go} agentConnected={!!envState.agent} />
          )}
        </Box>
      </Box>
    </Box>
  );
}

/* ── left rail ───────────────────────────────────────────────────────────── */

function SetupRail({ current, onGo, accent, doneById, counts, setupDone, setupTotal }) {
  return (
    <Box
      sx={{
        width: 200, flexShrink: 0, borderRight: "1px solid", borderColor: "divider",
        display: "flex", flexDirection: "column", bgcolor: "background.paper",
      }}
    >
      <Box sx={{ p: 2, flex: 1 }}>
        {RAIL_GROUPS.map((group) => (
          <Box key={group} sx={{ mb: 2 }}>
            <Typography
              sx={{
                typography: "s3", fontWeight: 700, color: "text.subtitle",
                letterSpacing: .4, textTransform: "uppercase", mb: 1,
              }}
            >
              {group}
            </Typography>
            <Stack spacing={0.25}>
              {STEPS.filter((s) => s.group === group).map((s) => {
                const active = current === s.id;
                const done = doneById[s.id];
                return (
                  <Stack
                    key={s.id}
                    direction="row"
                    alignItems="center"
                    spacing={1.25}
                    onClick={() => onGo(s.id)}
                    sx={{
                      px: 1.25, py: 0.875, borderRadius: 1,
                      cursor: "pointer",
                      // Same selected treatment as the app's left nav
                      // (nav-section/vertical/nav-item): brand primary at 0.1
                      // for fill and border, primary.dark for the label. The
                      // rail used the environment's own accent, so selection
                      // changed colour depending on which environment you were
                      // in — two different answers to "what does selected look
                      // like" on one screen.
                      color: active ? "primary.dark" : "text.secondary",
                      bgcolor: active ? (t) => alpha(t.palette.primary.main, 0.1) : "transparent",
                      border: "1px solid",
                      borderColor: active ? (t) => alpha(t.palette.primary.main, 0.1) : "transparent",
                      transition: "background-color .15s ease",
                      "&:hover": { bgcolor: active ? undefined : "action.hover" },
                    }}
                  >
                    <Iconify icon={s.icon} width={17} sx={{ color: active ? "primary.main" : "text.subtitle", flexShrink: 0 }} />
                    <Typography sx={{ flex: 1, typography: "s2", fontWeight: active ? 600 : 500, color: "inherit" }}>
                      {s.label}
                    </Typography>
                    {counts[s.id] != null && !done && (
                      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{counts[s.id]}</Typography>
                    )}
                    {done && (
                      <Iconify icon="solar:check-circle-bold" width={15} sx={{ color: "#16A34A", flexShrink: 0 }} />
                    )}
                  </Stack>
                );
              })}
            </Stack>
          </Box>
        ))}
      </Box>

      {/* Progress summary — the rail's second job. */}
      <Box sx={{ p: 2, borderTop: "1px solid", borderColor: "divider" }}>
        <Stack direction="row" justifyContent="space-between" sx={{ mb: 0.75 }}>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>Setup progress</Typography>
          <Typography sx={{ typography: "s3", fontWeight: 700 }}>{setupDone}/{setupTotal}</Typography>
        </Stack>
        <Box sx={{ height: 4, borderRadius: 2, bgcolor: "background.neutral", overflow: "hidden" }}>
          <Box
            sx={{
              height: "100%", borderRadius: 2, bgcolor: accent,
              width: `${(setupDone / setupTotal) * 100}%`,
              transition: "width .4s cubic-bezier(.4,0,.2,1)",
            }}
          />
        </Box>
      </Box>
    </Box>
  );
}

SetupRail.propTypes = {
  current: PropTypes.string,
  onGo: PropTypes.func,
  accent: PropTypes.string,
  doneById: PropTypes.object,
  counts: PropTypes.object,
  setupDone: PropTypes.number,
  setupTotal: PropTypes.number,
};


