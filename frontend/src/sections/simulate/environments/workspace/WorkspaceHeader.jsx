import PropTypes from "prop-types";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import { Box, Stack, Typography, Button, IconButton } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import ConfirmDialog from "src/components/custom-dialog/confirm-dialog";
import { paths } from "src/routes/paths";
import { runSimulationTarget } from "src/api/simulate-environments/runs";
import { HARNESS_DETAIL_ENABLED } from "src/api/simulate-environments/environment";
import { useDeleteEnvironment } from "src/api/simulate-environments/environments";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import { ENTRY_TAB } from "../environmentOptions";
import { DELETE_DIALOG_COPY, DELETE_TONE } from "../myEnvironments.constants";
import SurfaceIcon from "../components/SurfaceIcon";
import LivePill from "./LivePill";
import RenameEnvironmentDialog from "./RenameEnvironmentDialog";
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
  // envState / patch are still passed by the parent for the version pin; re-add
  // them here when the commented-out <EnvVersionPin> below is restored.
  canRun,
  runBlockedReason,
  locked = false,
  backed = false,
  onFork,
}) {
  const navigate = useNavigate();
  const [renameOpen, setRenameOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const deleteEnv = useDeleteEnvironment();
  // Rename (§8) lands with the §6 detail path — its response is the §6 body and
  // the header only reflects the new name once the detail cache drives `env`.
  // Gate the affordance on that path so it never shows a control that 405s.
  const canRename = HARNESS_DETAIL_ENABLED && !locked;
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

      <CustomTooltip show={!canRun} title={runBlockedReason} size="small" arrow>
        <span>
          <Button
            variant="contained"
            color="primary"
            size="small"
            disabled={!canRun}
            onClick={() => navigate(runSimulationTarget(env))}
            startIcon={<Iconify icon="solar:play-bold" width={15} />}
            sx={{ typography: "s2", fontWeight: "fontWeightBold" }}
          >
            {WORKSPACE_COPY.run}
          </Button>
        </span>
      </CustomTooltip>

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
              }}
            >
              {DELETE_DIALOG_COPY.confirm}
            </Button>
          }
        />
      )}
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
};
