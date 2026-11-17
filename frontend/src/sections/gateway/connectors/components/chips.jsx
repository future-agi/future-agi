import React from "react";
import PropTypes from "prop-types";
import Chip from "@mui/material/Chip";

import NotInReleaseChip from "src/components/NotInRelease/NotInReleaseChip";

import { GatewayStatusChip } from "../../components/frame";
import { APP_STATUS, tierBadge } from "../catalog-model";

// An app's status on the gateway status chip, the one every gateway page
// uses (Providers, MCP, Keys), so a connected app reads like a healthy
// provider in light and dark.
const STATUS_CHIPS = {
  [APP_STATUS.CONNECTED]: { tone: "healthy", label: "Connected" },
  [APP_STATUS.NEEDS_KEY]: { tone: "warning", label: "Needs key" },
  [APP_STATUS.NOT_CONNECTED]: { tone: "warning", label: "Not connected" },
  [APP_STATUS.OFF]: { tone: "neutral", label: "Turned off" },
};

// An app this release does not connect carries the product's shared line on
// its own chip, with no promise about when. `short` where the whole line
// cannot fit (the grid's status column): the chip shows the short word and
// keeps the whole line for a screen reader and in its tooltip.
export function AppStatusChip({ status, short = false }) {
  if (status === APP_STATUS.COMING_SOON) {
    return <NotInReleaseChip short={short} />;
  }
  const chip = STATUS_CHIPS[status];
  if (!chip) return null;
  return <GatewayStatusChip tone={chip.tone} label={chip.label} />;
}

AppStatusChip.propTypes = { status: PropTypes.string, short: PropTypes.bool };

// The provider's quality tier, the only source of a badge: the gateway's
// outlined chip.
export function TierBadge({ provider }) {
  const badge = tierBadge(provider);
  if (!badge) return null;
  return (
    <Chip
      size="small"
      variant="outlined"
      color={badge.color}
      label={badge.label}
      sx={{ height: 22, typography: "s3" }}
    />
  );
}

TierBadge.propTypes = { provider: PropTypes.object };
