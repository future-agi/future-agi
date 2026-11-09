import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

import { formatDate } from "../lib/format";

import { EntryGrid } from "./EntryCard";
import HubIcon from "./HubIcon";
import { toneSx } from "./ToneChip";

// "Maintainer picks this week" (SPEC §6.4): at most 6, each with the
// maintainer's one-line why. With no picks this week, nothing is shown.
export default function PicksStrip({ picks }) {
  if (!picks?.length) return null;
  const week = picks[0]?.pick?.week;
  return (
    <Box
      data-testid="hub-picks"
      sx={{ display: "flex", flexDirection: "column", gap: 1.25 }}
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.75 }}>
        <Box sx={{ ...toneSx("attention"), display: "flex" }}>
          <HubIcon name="star" size={16} />
        </Box>
        <Typography typography="s1" fontWeight={600}>
          Maintainer picks this week
        </Typography>
        {week && (
          <Typography typography="s2" color="text.tertiary">
            {`week of ${formatDate(week)}`}
          </Typography>
        )}
      </Box>
      <EntryGrid entries={picks.slice(0, 6)} />
    </Box>
  );
}

PicksStrip.propTypes = { picks: PropTypes.array };
