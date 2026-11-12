import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Typography, Tooltip } from "@mui/material";
import { formatDistanceToNow, parseISO } from "date-fns";
import { getDisplayTimeZone } from "src/utils/format-time";

// "MMM dd, yyyy HH:mm:ss UTC" in the viewer's zone ("GMT+5:30", "EST", ...).
// The digits and the zone name come from one Intl call, so they always agree.
const formatInZone = (date, timeZone) => {
  const parts = {};
  new Intl.DateTimeFormat("en-US", {
    timeZone,
    month: "short",
    day: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
    timeZoneName: "short",
  })
    .formatToParts(date)
    .forEach(({ type, value }) => {
      parts[type] = value;
    });
  const day = `${parts.month} ${parts.day}, ${parts.year}`;
  const time = `${parts.hour}:${parts.minute}:${parts.second}`;
  return `${day} ${time} ${parts.timeZoneName}`;
};

// Rendered inside the tooltip, so "3 minutes ago" is worked out when the
// tooltip opens, not when the row was first drawn.
const RelativeTime = ({ date }) =>
  formatDistanceToNow(date, { addSuffix: true });

RelativeTime.propTypes = {
  date: PropTypes.instanceOf(Date).isRequired,
};

const TimestampCell = ({ value }) => {
  const stamp = useMemo(() => {
    if (!value) return null;
    try {
      const date =
        typeof value === "string" ? parseISO(value) : new Date(value);
      if (isNaN(date.getTime())) return null;
      return {
        date,
        iso: date.toISOString(),
        text: formatInZone(date, getDisplayTimeZone()),
      };
    } catch {
      return null;
    }
  }, [value]);

  if (!stamp) {
    return (
      <Typography
        variant="body2"
        sx={{ fontSize: 13, color: "text.secondary", px: 1.5 }}
      >
        -
      </Typography>
    );
  }

  return (
    <Box
      sx={{
        width: "100%",
        height: "100%",
        px: 1.5,
        display: "flex",
        alignItems: "center",
        justifyContent: "flex-end",
        minWidth: 0,
      }}
    >
      <Tooltip
        title={
          <>
            <Box component="span" sx={{ display: "block" }}>
              <RelativeTime date={stamp.date} />
            </Box>
            <Box
              component="span"
              sx={{ display: "block", fontVariantNumeric: "tabular-nums" }}
            >
              {stamp.iso}
            </Box>
          </>
        }
        describeChild
        arrow
        placement="top"
      >
        <Typography
          component="time"
          dateTime={stamp.iso}
          tabIndex={0}
          variant="body2"
          sx={{
            fontSize: 13,
            color: "text.secondary",
            cursor: "default",
            // A column narrower than the text cuts the zone, never the date.
            minWidth: 0,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            borderRadius: "2px",
            "&:focus-visible": {
              outline: "2px solid",
              outlineColor: "primary.main",
              outlineOffset: 2,
            },
          }}
        >
          {stamp.text}
        </Typography>
      </Tooltip>
    </Box>
  );
};

TimestampCell.propTypes = {
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
};

export default React.memo(TimestampCell);
