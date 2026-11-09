import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Tooltip from "@mui/material/Tooltip";

import { TIER_TOOLTIP, TIERS } from "../constants";

import HubIcon from "./HubIcon";

const TONE = {
  official: { color: "primary.main", border: "primary.main", icon: "shieldCheck" },
  verified: { color: "info.main", border: "info.main", icon: "shieldCheck" },
  community: { color: "text.secondary", border: "divider", icon: "shield" },
};

// The trust tier (SPEC §2.4). It comes from the server's publisher record,
// never from the manifest. The tooltip says what every tier shares, and what
// review does not mean. "Community reviewed" is a claim about listed
// versions, so a community publisher with no listed entry (`entryCount` 0,
// when the page knows it) gets no badge.
export default function TierBadge({ tier, short = false, verifiedAt, entryCount }) {
  if ((!tier || tier === "community") && entryCount === 0) return null;
  const meta = TIERS[tier] || TIERS.community;
  const tone = TONE[tier] || TONE.community;
  const label = short ? meta.short : meta.label;
  const title = verifiedAt && tier === "verified" ? `${meta.label} · ${verifiedAt.slice(0, 10)}` : meta.label;
  return (
    <Tooltip title={`${title}. ${TIER_TOOLTIP}`} arrow>
      <Box
        component="span"
        data-testid="hub-tier-badge"
        data-tier={tier || "community"}
        sx={{
          display: "inline-flex",
          alignItems: "center",
          gap: 0.5,
          px: 0.75,
          py: 0.125,
          borderRadius: 0.75,
          border: "1px solid",
          borderColor: tone.border,
          color: tone.color,
          typography: "s3",
          fontWeight: 600,
          lineHeight: "18px",
          whiteSpace: "nowrap",
          bgcolor: "background.paper",
        }}
      >
        <HubIcon name={tone.icon} size={12} />
        {label}
      </Box>
    </Tooltip>
  );
}

TierBadge.propTypes = {
  tier: PropTypes.string,
  short: PropTypes.bool,
  verifiedAt: PropTypes.string,
  entryCount: PropTypes.number,
};
