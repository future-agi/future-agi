import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import { Box, Stack, Typography, Button } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { paths } from "src/routes/paths";
import { runSimulationTarget } from "src/api/simulate-environments/runs";
import SurfaceIcon from "../components/SurfaceIcon";
import LivePill from "./LivePill";
import EnvVersionPin from "./EnvVersionPin";
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
  envState,
  patch,
  canRun,
  runBlockedReason,
  locked = false,
  onFork,
}) {
  const navigate = useNavigate();

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
          onClick={() => navigate(paths.dashboard.simulate.environments.root)}
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
          <LivePill />
          <EnvVersionPin env={env} envState={envState} patch={patch} readOnly={locked} />
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

      {!locked && <ForkMenu onFork={onFork} />}
    </Stack>
  );
}

WorkspaceHeader.propTypes = {
  env: PropTypes.shape({
    id: PropTypes.string,
    name: PropTypes.string,
    tagline: PropTypes.string,
    surface: PropTypes.string,
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
  onFork: PropTypes.func,
};
