import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Tooltip from "@mui/material/Tooltip";

import { EMPTY, formatExactTime, hibernatesIn, timeAgo } from "../lib/format";

// A focusable time shows the DS focus ring, as the list's rows do.
const FOCUSABLE_SX = {
  borderRadius: 0.5,
  "&:focus-visible": {
    outline: (theme) => `2px solid ${theme.palette.border.focus}`,
    outlineOffset: 2,
  },
};

/**
 * A time as people read it ("4 min ago", "in 28 min"), with the exact instant
 * and its time zone on hover. A focusable time also shows it on keyboard
 * focus, where a screen reader reads it as the time's description; a time
 * inside a button stays out of the tab order. A missing time is an em dash,
 * with no tooltip.
 */
export default function RelativeTime({
  iso,
  now,
  future = false,
  prefix = "",
  testId,
  focusable = false,
}) {
  const text = future ? hibernatesIn(iso, now) : timeAgo(iso, now);
  if (text === EMPTY) {
    return (
      <Box component="span" data-testid={testId}>
        {prefix}
        {EMPTY}
      </Box>
    );
  }
  return (
    <Tooltip title={formatExactTime(iso)} describeChild arrow>
      <Box
        component="time"
        dateTime={iso}
        data-testid={testId}
        tabIndex={focusable ? 0 : undefined}
        sx={focusable ? FOCUSABLE_SX : undefined}
      >
        {prefix}
        {text}
      </Box>
    </Tooltip>
  );
}

RelativeTime.propTypes = {
  iso: PropTypes.string,
  now: PropTypes.number,
  /** A time still to come, read as "in 28 min". */
  future: PropTypes.bool,
  /** Words before the time, inside the hover target: "created ". */
  prefix: PropTypes.string,
  testId: PropTypes.string,
  /** In the tab order, so a keyboard reaches the exact time. */
  focusable: PropTypes.bool,
};
