import React from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Stack from "@mui/material/Stack";

import {
  enforcementSentence,
  notEnforcedReason,
  sectionEnforcement,
} from "../sectionEnforcement";

// The notes above an Org config tab whose sections this gateway may not
// enforce, from the gateway's own org_sections_enforced: one for each
// section it does not enforce or does not report. Nothing while the config
// read is pending and nothing for an enforced section.
export default function SectionEnforcementNotes({
  sections,
  gatewayConfigQuery,
  sx,
}) {
  const notes = sections
    .map((section) => [
      section,
      sectionEnforcement(gatewayConfigQuery, section),
    ])
    .filter(([, state]) => state === "unknown" || state === "not_enforced");
  if (notes.length === 0) return null;
  return (
    <Stack spacing={1} sx={sx}>
      {notes.map(([section, state]) => (
        <Alert
          key={section}
          severity={state === "unknown" ? "info" : "warning"}
          variant="outlined"
        >
          {enforcementSentence(
            section,
            state,
            notEnforcedReason(gatewayConfigQuery, section),
          )}
        </Alert>
      ))}
    </Stack>
  );
}

SectionEnforcementNotes.propTypes = {
  // Org config section keys (sectionEnforcement.js ENFORCED_SECTION_LABELS).
  sections: PropTypes.arrayOf(PropTypes.string).isRequired,
  // The gateway config query (useGatewayConfig).
  gatewayConfigQuery: PropTypes.object,
  sx: PropTypes.object,
};
