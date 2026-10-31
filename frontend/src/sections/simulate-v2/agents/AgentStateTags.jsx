import PropTypes from "prop-types";
import { Box, Stack, Tooltip } from "@mui/material";
import {
  COMING_SOON_NOTE,
  agentStatusOf,
  isComingSoon,
} from "../_mock/rsiModel";
import { QuietTag } from "./AgentBits";

/**
 * The quiet tags an agent row carries wherever agents are listed: Coming
 * soon for browser and computer-use agents, Disconnected or Archived
 * with the reason on hover.
 */
export default function AgentStateTags({ agent }) {
  const status = agentStatusOf(agent);
  const soon = isComingSoon(agent);
  if (!soon && status.status === "connected") return null;
  return (
    <Stack direction="row" spacing={0.5} sx={{ flexShrink: 0 }}>
      {soon && (
        <Tooltip arrow title={COMING_SOON_NOTE}>
          <Box component="span">
            <QuietTag>Coming soon</QuietTag>
          </Box>
        </Tooltip>
      )}
      {status.status !== "connected" && (
        <Tooltip arrow title={status.reason || ""}>
          <Box component="span">
            <QuietTag
              tone={status.status === "disconnected" ? "error" : undefined}
            >
              {status.label}
            </QuietTag>
          </Box>
        </Tooltip>
      )}
    </Stack>
  );
}
AgentStateTags.propTypes = { agent: PropTypes.object.isRequired };
