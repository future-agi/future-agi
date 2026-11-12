import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";

import { runtimeLabel, stateLabel } from "../lib/format";

const STATE_TONE = {
  active: "success.main",
  hibernated: "text.disabled",
  recovery_required: "orange.500",
  destroyed: "text.disabled",
};

const pill = {
  display: "inline-flex",
  alignItems: "center",
  gap: 0.75,
  height: 22,
  px: 1,
  borderRadius: "11px",
  border: "1px solid",
  borderColor: "divider",
  typography: "s2",
  fontWeight: 500,
  color: "text.primary",
  whiteSpace: "nowrap",
  bgcolor: "background.paper",
};

/** Lifecycle state with a coloured dot: Running, Hibernated, Needs recovery, Deleted. */
export function StateChip({ state }) {
  return (
    <Box
      component="span"
      sx={pill}
      data-testid="sandbox-state"
      data-state={state}
    >
      <Box
        component="span"
        sx={{
          width: 7,
          height: 7,
          borderRadius: "50%",
          bgcolor: STATE_TONE[state] || "text.disabled",
        }}
      />
      {stateLabel(state)}
    </Box>
  );
}

StateChip.propTypes = { state: PropTypes.string };

/**
 * Where the sandbox runs (the provider kind). The provider's binding id is an
 * internal name (COPY1), so it is never shown, not even on hover.
 */
export function RuntimeChip({ provider }) {
  if (!provider) return null;
  return (
    <Box
      component="span"
      sx={{ ...pill, color: "text.secondary" }}
      data-testid="sandbox-runtime"
    >
      {runtimeLabel(provider.kind)}
    </Box>
  );
}

RuntimeChip.propTypes = {
  provider: PropTypes.shape({
    kind: PropTypes.string,
    bindingId: PropTypes.string,
  }),
};

/** A muted tag such as "Agent run" or "Restored". */
export function QuietTag({ children }) {
  return (
    <Box
      component="span"
      sx={{ ...pill, color: "text.secondary", bgcolor: "transparent" }}
    >
      {children}
    </Box>
  );
}

QuietTag.propTypes = { children: PropTypes.node };
