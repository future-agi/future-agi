import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";

import OmegaEmptyState from "./OmegaEmptyState";

// Shown at every Omega path when the Omega switch is off: VITE_OMEGA_CONSOLE
// "0" or "false", or unset and no Omega gateway answered (flags.js). The paths
// stay registered with this page (routes/sections/omega.jsx), so a deep link
// says why instead of falling to the 404. It names what an admin does and
// offers nothing to press: the app links no Omega setup page to send them to.
export const OMEGA_NOT_ENABLED_TITLE =
  "Omega Console isn't enabled on this installation";
export const OMEGA_NOT_ENABLED_LINE =
  "An admin turns it on by running the Omega gateway with this app, or by setting VITE_OMEGA_CONSOLE to 1.";

export default function OmegaNotEnabledPage({ standalone = false }) {
  const state = (
    <OmegaEmptyState
      icon="console"
      title={OMEGA_NOT_ENABLED_TITLE}
      description={OMEGA_NOT_ENABLED_LINE}
      headingComponent="h1"
    />
  );
  if (!standalone) return state;
  // /dashboard/console/* renders outside DashboardLayout: the plain page.
  return (
    <Box
      sx={{
        minHeight: "100vh",
        display: "flex",
        flexDirection: "column",
        bgcolor: "background.default",
      }}
    >
      {state}
    </Box>
  );
}

OmegaNotEnabledPage.propTypes = {
  standalone: PropTypes.bool,
};
