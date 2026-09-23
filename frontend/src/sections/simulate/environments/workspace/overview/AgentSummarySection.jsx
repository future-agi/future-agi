import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { BUILD_TONES } from "../../buildEnvironment/buildTones";
import { ENV_STATE_SHAPE, AGENT_SUMMARY_COPY, OVERVIEW_COPY } from "./overview.constants";

/*
  Compact card that reflects the env-first model: the agent is a *test subject*
  attached to the env, not the env's identity. Shows the current agent's version
  + connection. The template ("seeded baseline") variant is locked and offers
  "Fork to edit". An editable env with an agent attached offers "Manage versions",
  which opens the AgentsPanel drawer via onManageVersions. "Attach agent" (no
  agent yet) stays deferred behind the coming-soon tooltip — the add-version
  drawer mints versions of an existing agent, not a first one.
*/
export default function AgentSummarySection({ envState, agentConnected, locked, onManageVersions }) {
  const agent = envState?.agent;
  const versions = envState?.agentVersions || [];
  const activeLabel = envState?.activeAgentVersion || versions[versions.length - 1]?.label || "v1";
  const versionCount = Math.max(1, versions.length);
  const endpoint = agent?.values?.sdkEndpoint || agent?.values?.endpoint || null;
  const manageLabel = agent ? AGENT_SUMMARY_COPY.manage : AGENT_SUMMARY_COPY.attach;

  let body = AGENT_SUMMARY_COPY.portableBody;
  if (locked) body = AGENT_SUMMARY_COPY.lockedBody;
  else if (agent) body = AGENT_SUMMARY_COPY.attachedBody(versionCount, endpoint);

  // A locked template offers no action here — the template banner owns the
  // single "Fork to edit" path, so this card just states why it's read-only. An
  // editable env with an agent gets a live "Manage versions" that opens the
  // AgentsPanel drawer; attaching a first agent stays deferred behind the
  // coming-soon tooltip.
  let action = null;
  if (locked) {
    action = null;
  } else if (agent) {
    action = (
      <Button
        variant="contained" size="small"
        onClick={onManageVersions}
        sx={{
          typography: "s2", fontWeight: "fontWeightBold",
          bgcolor: "common.white", color: "common.black",
          "&:hover": { bgcolor: alpha("#FFFFFF", 0.88) },
        }}
      >
        {manageLabel}
      </Button>
    );
  } else {
    action = (
      <CustomTooltip show size="small" title={OVERVIEW_COPY.agentVersionsSoon} arrow>
        <span>
          <Button
            variant="outlined" size="small"
            disabled
            aria-label={manageLabel}
            sx={{ typography: "s2", fontWeight: "fontWeightBold", color: "text.primary", borderColor: "divider" }}
          >
            {manageLabel}
          </Button>
        </span>
      </CustomTooltip>
    );
  }

  return (
    <Box sx={{ my: 2, p: 2, borderRadius: 1.5, border: "1px solid", borderColor: "divider" }}>
      <Stack direction={{ xs: "column", sm: "row" }} spacing={2} alignItems={{ sm: "center" }}>
        <Box
          sx={{
            width: 34, height: 34, borderRadius: 1, display: "grid", placeItems: "center",
            bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.06 : 0.04),
            color: "text.secondary", flexShrink: 0,
          }}
        >
          <Iconify icon="solar:cpu-bolt-linear" width={18} />
        </Box>
        <Box flex={1} minWidth={0}>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography sx={{ typography: "s3", color: "text.subtitle", fontWeight: "fontWeightBold", textTransform: "uppercase", letterSpacing: 0.4 }}>
              {AGENT_SUMMARY_COPY.label}
            </Typography>
            {agentConnected && (
              <Box
                sx={{
                  height: 18, px: 0.75, borderRadius: 0.75,
                  display: "inline-flex", alignItems: "center",
                  bgcolor: (t) => alpha(BUILD_TONES.green, t.palette.mode === "dark" ? 0.16 : 0.1),
                  color: BUILD_TONES.green,
                }}
              >
                <Typography sx={{ typography: "s3", fontWeight: "fontWeightBold" }}>{AGENT_SUMMARY_COPY.connected}</Typography>
              </Box>
            )}
          </Stack>
          <Typography sx={{ typography: "s1", fontWeight: "fontWeightBold", mt: 0.25 }}>
            {agent ? AGENT_SUMMARY_COPY.agentTitle(activeLabel) : AGENT_SUMMARY_COPY.noAgentTitle}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>{body}</Typography>
        </Box>
        <Stack direction="row" spacing={1} sx={{ flexShrink: 0 }}>
          {action}
        </Stack>
      </Stack>
    </Box>
  );
}
AgentSummarySection.propTypes = {
  envState: ENV_STATE_SHAPE,
  agentConnected: PropTypes.bool,
  locked: PropTypes.bool,
  onManageVersions: PropTypes.func,
};
