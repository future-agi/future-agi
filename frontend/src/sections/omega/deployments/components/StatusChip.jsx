import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";

import { healthLabel, isBusy, statusLabel } from "../lib/format";
import { HOST_ONLY_STATUS } from "../lib/reach";

const TONE = {
  live: "success.main",
  failed: "error.main",
  queued: "text.disabled",
  building: "primary.main",
  deploying: "primary.main",
  superseded: "text.disabled",
};

const HEALTH_TONE = {
  ok: "success.main",
  starting: "primary.main",
  draining: "orange.500",
  unavailable: "error.main",
  unreachable: "text.disabled",
};

export const pill = {
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

function Dot({ tone, pulse }) {
  return (
    <Box
      component="span"
      sx={{
        width: 7,
        height: 7,
        flexShrink: 0,
        borderRadius: "50%",
        bgcolor: tone,
        ...(pulse && {
          "@keyframes deployPulse": {
            "0%, 100%": { opacity: 1 },
            "50%": { opacity: 0.35 },
          },
          animation: "deployPulse 1.4s ease-in-out infinite",
          "@media (prefers-reduced-motion: reduce)": { animation: "none" },
        }),
      }}
    />
  );
}

Dot.propTypes = { tone: PropTypes.string, pulse: PropTypes.bool };

/**
 * Queued, Building, Deploying, Live, Failed or Superseded, with a dot. A live
 * deployment only this host can reach says "Running on this host" instead of
 * "Live" (`hostOnly`, lib/reach.js).
 */
export function StatusChip({ status, testId = "deployment-status", hostOnly }) {
  const local = hostOnly === true && status === "live";
  const label = local ? HOST_ONLY_STATUS : statusLabel(status);
  return (
    <Box
      component="span"
      sx={{ ...pill, maxWidth: "100%", overflow: "hidden" }}
      data-testid={testId}
      data-status={status}
      {...(local ? { "data-reach": "deployment-host" } : {})}
    >
      <Dot tone={TONE[status] || "text.disabled"} pulse={isBusy(status)} />
      <Box
        component="span"
        title={label}
        sx={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}
      >
        {label}
      </Box>
    </Box>
  );
}

StatusChip.propTypes = {
  status: PropTypes.string,
  testId: PropTypes.string,
  hostOnly: PropTypes.bool,
};

export function HealthChip({ status }) {
  return (
    <Box
      component="span"
      sx={pill}
      data-testid="deployment-health"
      data-health={status}
    >
      <Dot tone={HEALTH_TONE[status] || "text.disabled"} />
      {healthLabel(status)}
    </Box>
  );
}

HealthChip.propTypes = { status: PropTypes.string };

/** A muted tag such as "Revision 2" or "Studio off". */
export function QuietTag({ children, testId }) {
  return (
    <Box
      component="span"
      data-testid={testId}
      sx={{ ...pill, color: "text.secondary", bgcolor: "transparent" }}
    >
      {children}
    </Box>
  );
}

QuietTag.propTypes = { children: PropTypes.node, testId: PropTypes.string };
