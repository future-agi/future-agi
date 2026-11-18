import PropTypes from "prop-types";
import { Box, Stack, Tooltip } from "@mui/material";
import { agentStatusOf } from "../_mock/rsiModel";
import { QuietTag } from "./AgentBits";

/**
 * The quiet tag an agent row carries wherever agents are listed:
 * Disconnected or Archived, with the reason on hover.
 */
export default function AgentStateTags({ agent }) {
  const status = agentStatusOf(agent);
  if (status.status === "connected") return null;
  return (
    <Stack direction="row" spacing={0.5} sx={{ flexShrink: 0 }}>
      <Tooltip arrow title={status.reason || ""}>
        <Box component="span">
          <QuietTag
            tone={status.status === "disconnected" ? "error" : undefined}
          >
            {status.label}
          </QuietTag>
        </Box>
      </Tooltip>
    </Stack>
  );
}
AgentStateTags.propTypes = { agent: PropTypes.object.isRequired };
