import PropTypes from "prop-types";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import { Box, Stack, Typography, Button, IconButton } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import ConfirmDialog from "src/components/custom-dialog/confirm-dialog";
import { paths } from "src/routes/paths";
import { useDeleteEnvironment } from "src/api/simulate-environments/environments";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import { ENTRY_TAB } from "../environmentOptions";
import { DELETE_DIALOG_COPY, DELETE_TONE, BUILD_STATUS } from "../myEnvironments.constants";
import CancelBuildControl from "../buildEnvironment/building/CancelBuildControl";
import SurfaceIcon from "../components/SurfaceIcon";
import LivePill from "./LivePill";
import RenameEnvironmentDialog from "./RenameEnvironmentDialog";
import TrialsPicker from "./scenarios/TrialsPicker";
import RunConfigDialog from "./scenarios/RunConfigDialog";
// EnvVersionPin renders a mock "env v3" version from a fixture fallback
// (_fixtures/versions.js) — there is no real version field in the environments
// contract yet. Hidden in the header until the contract exposes one.
// import EnvVersionPin from "./EnvVersionPin";
import ForkMenu from "./ForkMenu";
import { WORKSPACE_COPY } from "./workspace.constants";

// The environment workspace header: back to the list, the channel icon, the
// name + Live pill + env-version pin, the primary "Run simulation" action, and
// (for a regular, unlocked env) the Fork overflow. Run simulation is the
// product's own navigation — a built env opens its execution detail, otherwise
// the product's run entry. `locked` = template-seeded until forked: the pin is
// read-only and the overflow is hidden (Fork lives on the Overview card there).
export default function WorkspaceHeader({
  env,
  // patch is still passed by the parent for the version pin; re-add it here when
  // the commented-out <EnvVersionPin> below is restored.
  envState,
  canRun,
  runBlockedReason,
  locked = false,
  backed = false,
  onFork,
  onStartRun,
  selectionActive = false,
}) {
  const navigate = useNavigate();
  const [renameOpen, setRenameOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  // Run configuration: the header's Repeats pill (k for a run-all) and the
  // modal the Run button opens. A scenario selection on the Scenarios tab takes
  // over the primary Run, so the header yields these while one is active.
  const [headerTrials, setHeaderTrials] = useState(1);
  const [runConfigOpen, setRunConfigOpen] = useState(false);
  const scenarioCount = envState?.scenarios?.length ?? 0;
  // Cancel is offered only while the build is actually running (not once it has
  // failed/canceled). CancelBuildControl self-hides otherwise.
  const building = env?.buildStatus === BUILD_STATUS.BUILDING;
  const deleteEnv = useDeleteEnvironment();
  // Rename (§8) is live for a real backend-backed env. Its response is the §6
  // body, which the mutation writes back into the §6 cache; the workspace
  // overlays that name onto `env`, so the header reflects the new name at once.
  // Gate on `backed` (like Delete) — a forked/template env has no row to PATCH.
  const canRename = backed && !locked;
  // Delete (§2) is live today, but only a real backend-backed env has a row to
  // remove — a forked/template env has none, so it is offered only when backed.
  const canDelete = backed && !locked;
  const onDelete = () =>
    deleteEnv.mutate(env.id, {
      onSuccess: () =>
        navigate(`${paths.dashboard.simulate.environments.root}?tab=${ENTRY_TAB.MY}`),
      onError: (error) => enqueueSnackbar(errorMessage(error), { variant: "error" }),
    });

  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={2}
      sx={{ px: 3, py: 2, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
    >
      <CustomTooltip show title={WORKSPACE_COPY.back} size="small" arrow>
        <Button
          aria-label={WORKSPACE_COPY.back}
          // Back to the environment list on its My Environments tab — the root
          // alone defaults to Build, which dropped you on an empty builder.
          onClick={() => navigate(`${paths.dashboard.simulate.environments.root}?tab=${ENTRY_TAB.MY}`)}
          sx={{ minWidth: 32, width: 32, height: 32, p: 0, color: "text.subtitle" }}
        >
          <Iconify icon="solar:alt-arrow-left-linear" width={18} />
        </Button>
      </CustomTooltip>

      <SurfaceIcon surface={env.surface} size={36} />

      <Box minWidth={0} flex={1}>
        <Stack direction="row" alignItems="center" spacing={1}>
          <Typography noWrap sx={{ typography: "s1_2", fontWeight: "fontWeightBold" }}>
            {env.name}
          </Typography>
          <LivePill env={env} />
          {canRename && (
            <CustomTooltip show title="Rename environment" size="small" arrow>
              <IconButton
                aria-label="Rename environment"
                size="small"
                onClick={() => setRenameOpen(true)}
                sx={{ color: "text.subtitle" }}
              >
                <Iconify icon="solar:pen-linear" width={15} />
              </IconButton>
            </CustomTooltip>
          )}
          {/* Mock version pin hidden until the contract exposes a real version:
              <EnvVersionPin env={env} envState={envState} patch={patch} readOnly={locked} /> */}
        </Stack>
        <Typography noWrap sx={{ typography: "s2", color: "text.subtitle" }}>
          {env.tagline}
        </Typography>
      </Box>

      {/* Run controls yield to the in-table selection bar: while a scenario
          selection is active it owns the primary Run, so the header hides its
          Repeats pill + Run to keep one primary action at a time. */}
      {!selectionActive && (
        <Stack direction="row" alignItems="center" spacing={1}>
          {canRun && (
            <TrialsPicker
              trials={headerTrials}
              onChange={setHeaderTrials}
              scenarioCount={scenarioCount}
            />
          )}
          <CustomTooltip show={!canRun} title={runBlockedReason} size="small" arrow>
            <span>
              <Button
                variant="contained"
                color="primary"
                size="small"
                disabled={!canRun}
                onClick={() => setRunConfigOpen(true)}
                startIcon={<Iconify icon="solar:play-bold" width={15} />}
                sx={{ typography: "s2", fontWeight: "fontWeightBold" }}
              >
                {WORKSPACE_COPY.run}
              </Button>
            </span>
          </CustomTooltip>
        </Stack>
      )}

      {/* Cancel build sits to the right of Run simulation while building; it
          self-hides once the build is no longer running. */}
      <CancelBuildControl envId={env?.id} building={building} />

      {!locked && (
        <ForkMenu
          onFork={onFork}
          onDelete={canDelete ? () => setConfirmDelete(true) : undefined}
        />
      )}

      {canRename && (
        <RenameEnvironmentDialog
          open={renameOpen}
          env={env}
          onClose={() => setRenameOpen(false)}
        />
      )}

      {canDelete && (
        <ConfirmDialog
          open={confirmDelete}
          onClose={() => setConfirmDelete(false)}
          title={DELETE_DIALOG_COPY.title}
          content={
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              <b>{env.name}</b> {DELETE_DIALOG_COPY.body}
            </Typography>
          }
          action={
            <Button
              size="small"
              variant="contained"
              disabled={deleteEnv.isPending}
              onClick={() => {
                setConfirmDelete(false);
                onDelete();
              }}
              sx={{
                bgcolor: DELETE_TONE.main,
                "&:hover": { bgcolor: DELETE_TONE.hover },
                typography: "s2",
                fontWeight: "fontWeightBold",
                paddingX: "24px",
              }}
            >
              {DELETE_DIALOG_COPY.confirm}
            </Button>
          }
        />
      )}

      {/* Run-all config: pick repeats, see the estimate, then start a run over
          every scenario × k. Runs via the parent's scoped-run target (no ids =
          all); trials ride ?trials=k (honoured once the live-run route lands). */}
      <RunConfigDialog
        open={runConfigOpen}
        onClose={() => setRunConfigOpen(false)}
        scenarioCount={scenarioCount}
        defaultTrials={headerTrials}
        onConfirm={(k) => { setHeaderTrials(k); onStartRun?.(undefined, k); }}
      />
    </Stack>
  );
}

WorkspaceHeader.propTypes = {
  env: PropTypes.shape({
    id: PropTypes.string,
    name: PropTypes.string,
    tagline: PropTypes.string,
    surface: PropTypes.string,
    buildStatus: PropTypes.string,
    platform: PropTypes.shape({
      runTestId: PropTypes.string,
      testExecutionId: PropTypes.string,
    }),
  }).isRequired,
  envState: PropTypes.shape({
    envVersions: PropTypes.arrayOf(PropTypes.shape({ label: PropTypes.string })),
    activeEnvVersion: PropTypes.string,
    scenarios: PropTypes.arrayOf(PropTypes.any),
  }).isRequired,
  patch: PropTypes.func.isRequired,
  canRun: PropTypes.bool,
  runBlockedReason: PropTypes.string,
  locked: PropTypes.bool,
  backed: PropTypes.bool,
  onFork: PropTypes.func,
  // Starts a run — (ids, trials); the header run-all passes ids = undefined.
  onStartRun: PropTypes.func,
  // True while a scenario selection owns the primary Run; hides the header's.
  selectionActive: PropTypes.bool,
};
