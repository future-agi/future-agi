/* eslint-disable react-refresh/only-export-components */
import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { useOmegaPart } from "src/sections/omega/shell/useOmegaPart";

import HubIcon from "./HubIcon";
import { toneColor } from "./ToneChip";

function Notice({ icon, tone = "info", title, children, testId }) {
  return (
    <Box
      role="note"
      data-testid={testId}
      sx={{
        display: "flex",
        gap: 1.25,
        alignItems: "flex-start",
        px: 1.5,
        py: 1.25,
        borderRadius: 1,
        border: "1px solid",
        borderColor: (t) => toneColor(t, tone === "warning" ? "attention" : tone),
        bgcolor: "background.paper",
      }}
    >
      <Box sx={{ color: (t) => toneColor(t, tone === "warning" ? "attention" : tone), mt: 0.125, display: "flex" }}>
        <HubIcon name={icon} size={16} />
      </Box>
      <Box sx={{ minWidth: 0 }}>
        <Typography typography="s1" fontWeight={600} color="text.primary">
          {title}
        </Typography>
        {children && (
          <Typography component="div" typography="s2" color="text.secondary" sx={{ mt: 0.25 }}>
            {children}
          </Typography>
        )}
      </Box>
    </Box>
  );
}

Notice.propTypes = {
  icon: PropTypes.string,
  tone: PropTypes.string,
  title: PropTypes.node,
  children: PropTypes.node,
  testId: PropTypes.string,
};

// "Not ranked: <category>" (SPEC §3.3): still listed in search and still
// installable, but out of Trending, New and picks. "It still installs" only
// once entries can be installed (OMEGA_PART HUB_CLI, lib/installs.js).
export function UnrankedNotice({ reason }) {
  const cliReleased = useOmegaPart(OMEGA_PART.HUB_CLI);
  return (
    <Notice icon="info" tone="warning" title={`Not ranked: ${reason || "other"}`} testId="hub-unranked-notice">
      A Future AGI maintainer took this entry out of Trending, New and the picks.
      {cliReleased ? " It still installs and is still found by search." : " It is still found by search."}
    </Notice>
  );
}

UnrankedNotice.propTypes = { reason: PropTypes.string };

// "It still installs by its exact version; the CLI warns." only once a
// customer can install a CLI with `omega hub` (OMEGA_PART HUB_CLI). Until
// then nothing installs any version, so the notice says only what happened.
export function YankedNotice({ version, reason }) {
  const cliReleased = useOmegaPart(OMEGA_PART.HUB_CLI);
  let text = "The publisher or a Future AGI maintainer withdrew this version.";
  if (reason) text = `Yanked: ${reason}`;
  else if (cliReleased) text = "Yanked.";
  return (
    <Notice icon="alert" tone="warning" title={`Version ${version} was yanked`} testId="hub-yanked-notice">
      {text}
      {cliReleased && " It still installs by its exact version; the CLI warns."}
    </Notice>
  );
}

YankedNotice.propTypes = { version: PropTypes.string, reason: PropTypes.string };

export { Notice };
