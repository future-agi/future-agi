import PropTypes from "prop-types";
import { Box, Stack, Typography, Button } from "@mui/material";
import Iconify from "src/components/iconify";
import {
  currentAgentVersion,
  environmentVersions,
  nextEnvVersion,
} from "src/api/simulate-environments/_fixtures/versions";
import { ENV_SHAPE, ENV_STATE_SHAPE, REFRESH_COPY } from "./overview.constants";

// The agent can move ahead of the environment: a new agent version is attached,
// but the world was last derived against an older one. This banner offers to
// re-derive the world against the active agent — optional, since the current
// world still runs. It shows only when there is a divergence, so on a fresh env
// (no envDerivedForAgent, agent on v1) it stays hidden.
export default function AgentRefreshBanner({ env, envState, patch }) {
  const agentV = currentAgentVersion(envState);
  const derivedFor = envState?.envDerivedForAgent || "v1";
  if (!agentV || agentV.label === derivedFor) return null;

  const refresh = () => {
    const list = envState?.envVersions?.length
      ? envState.envVersions
      : [...environmentVersions(env, envState)].reverse();
    const version = nextEnvVersion(env, envState, {
      changed: ["seed", "checks", "contract"],
      note: REFRESH_COPY.note(agentV.label),
    });
    patch?.({
      envVersions: [...list, version],
      activeEnvVersion: version.label,
      envDerivedForAgent: agentV.label,
    });
  };

  return (
    <Stack
      direction={{ xs: "column", sm: "row" }}
      alignItems={{ sm: "center" }}
      spacing={1.5}
      sx={{
        mt: -0.5, mb: 2, p: 1.75, borderRadius: 1.5, border: "1px solid",
        borderColor: "divider", bgcolor: "background.neutral",
      }}
    >
      <Iconify icon="solar:refresh-circle-linear" width={20} sx={{ color: "text.subtitle", flexShrink: 0 }} />
      <Box flex={1} minWidth={0}>
        <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold" }}>
          {REFRESH_COPY.title(agentV.label)}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
          {REFRESH_COPY.body(agentV.label)}
        </Typography>
      </Box>
      <Button
        variant="outlined"
        size="small"
        onClick={refresh}
        startIcon={<Iconify icon="solar:refresh-linear" width={14} />}
        sx={{
          flexShrink: 0, typography: "s2", fontWeight: "fontWeightSemiBold",
          color: "text.primary", borderColor: "divider",
          "&:hover": { borderColor: "text.disabled", bgcolor: "action.hover" },
        }}
      >
        {REFRESH_COPY.action}
      </Button>
    </Stack>
  );
}
AgentRefreshBanner.propTypes = {
  env: ENV_SHAPE,
  envState: ENV_STATE_SHAPE,
  patch: PropTypes.func,
};
