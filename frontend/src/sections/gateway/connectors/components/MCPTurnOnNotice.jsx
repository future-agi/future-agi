import React from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

import { activatesSaved } from "../mcp-turn-on";

// Above a connect or OAuth app save, for an Owner or Admin: while MCP is off
// for the organization, that the save turns it on (`sentence`), and how many
// saved servers the turn-on activates; when the setting cannot be read, that
// this action turns MCP on if it is off (`unreadSentence`, each action its
// own). `id` lets the action's button name it as its description.
export default function MCPTurnOnNotice({ id, sentence, unreadSentence, mcp }) {
  if (mcp?.state === "unread") {
    return (
      <Typography id={id} typography="s2" color="text.secondary">
        {unreadSentence}
      </Typography>
    );
  }
  if (mcp?.state !== "off") return null;
  return (
    <Alert id={id} severity="info">
      <Box component="span" sx={{ display: "block" }}>
        {sentence}
      </Box>
      {mcp.saved > 0 && (
        <Box component="span" sx={{ display: "block" }}>
          {activatesSaved(mcp.saved)}
        </Box>
      )}
    </Alert>
  );
}

MCPTurnOnNotice.propTypes = {
  id: PropTypes.string.isRequired,
  sentence: PropTypes.string.isRequired,
  unreadSentence: PropTypes.string.isRequired,
  mcp: PropTypes.shape({
    state: PropTypes.oneOf(["loading", "unread", "off", "on"]).isRequired,
    saved: PropTypes.number,
  }),
};
