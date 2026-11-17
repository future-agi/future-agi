import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Tooltip from "@mui/material/Tooltip";

import { formatDate, timeAgo } from "../lib/format";

const EXACT = {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "UTC",
  timeZoneName: "short",
};

function parse(iso) {
  if (!iso) return null;
  const date = new Date(iso.length === 10 ? `${iso}T00:00:00Z` : iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

// A date as the day it fell on (UTC, like every Hub date), in a <time>
// element. Hovering it gives the exact time with its time zone and how long
// ago that was; `withAgo` also prints how long ago beside the day. "How long
// ago" is the Hub's one wording (lib/format timeAgo: "3 h ago", "12 days
// ago"), the same as on Submissions and Review.
export default function DateStamp({ iso, withAgo = false }) {
  const date = parse(iso);
  if (!date) return <span>—</span>;
  const ago = timeAgo(date.toISOString());
  return (
    <Tooltip title={`${date.toLocaleString("en-GB", EXACT)} · ${ago}`} arrow>
      <Box
        component="time"
        dateTime={date.toISOString()}
        sx={{ whiteSpace: "nowrap" }}
      >
        {withAgo ? `${formatDate(iso)} (${ago})` : formatDate(iso)}
      </Box>
    </Tooltip>
  );
}

DateStamp.propTypes = {
  iso: PropTypes.string,
  withAgo: PropTypes.bool,
};
