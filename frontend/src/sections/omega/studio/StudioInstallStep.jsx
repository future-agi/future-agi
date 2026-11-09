import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

import CopyableCommand from "./CopyableCommand";
import { INSTALL } from "./studio-copy";

// Step 1 of "Start omega dev": how to get the Omega CLI. The command comes
// from the one value the release sets (release-config.js), never from this
// file. A build that names no package shows no install step at all (DEMO-FIX-2
// item 5): until the CLI is published there is no command a person can run,
// and a step that only says so is a dead end (StartOmegaDev.jsx renumbers).
export default function StudioInstallStep({ command }) {
  if (!command) return null;
  return (
    <Box data-testid="studio-install" data-state="command">
      <Typography
        variant="s1"
        color="text.secondary"
        component="p"
        sx={{ mb: 1 }}
      >
        {INSTALL.step}
      </Typography>
      <CopyableCommand command={command} testId="studio-install-command" />
      <Typography
        variant="s2"
        color="text.disabled"
        component="p"
        sx={{ mt: 0.5 }}
      >
        {INSTALL.requirement}
      </Typography>
    </Box>
  );
}

StudioInstallStep.propTypes = {
  command: PropTypes.string,
};
