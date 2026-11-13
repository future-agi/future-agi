import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Typography, Tooltip } from "@mui/material";
import { formatDistanceToNow, format, parseISO } from "date-fns";

const TimestampCell = ({ value }) => {
  const { relative, absolute } = useMemo(() => {
    if (!value) return { relative: "-", absolute: "" };
    try {
      const date =
        typeof value === "string" ? parseISO(value) : new Date(value);
      if (isNaN(date.getTime())) return { relative: "-", absolute: "" };
      return {
        relative: formatDistanceToNow(date, { addSuffix: true }),
        absolute: format(date, "MMM dd, yyyy HH:mm:ss"),
      };
    } catch {
      return { relative: "-", absolute: "" };
    }
  }, [value]);

  if (!absolute) {
    return (
      <Typography
        variant="body2"
        sx={{ fontSize: 12, color: "text.secondary", px: 1.5 }}
      >
        -
      </Typography>
    );
  }

  return (
    <Tooltip title={relative} arrow placement="top">
      <Typography
        variant="body2"
        sx={{
          fontSize: 12,
          color: "text.secondary",
          textAlign: "right",
          width: "100%",
          px: 1.5,
          cursor: "default",
          display: "flex",
          alignItems: "center",
          justifyContent: "flex-end",
          height: "100%",
        }}
      >
        {absolute}
      </Typography>
    </Tooltip>
  );
};

TimestampCell.propTypes = {
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
};

export default React.memo(TimestampCell);
