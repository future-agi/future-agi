import { useState } from "react";
import PropTypes from "prop-types";
import Tooltip from "@mui/material/Tooltip";

import {
  describeInstant,
  fDateLocal,
  fDateTimeLocal,
} from "src/utils/format-time";

export function LocalDateTime({ value, withTime = false, emptyText = "Unknown" }) {
  const instant = describeInstant(value);
  // Controlled from the first render. `open={flag || undefined}` would lock MUI's
  // tooltip uncontrolled, and MUI replaces the child's onTouchStart with its own
  // 700ms long-press handler, so touch has to open the disclosure itself.
  const [open, setOpen] = useState(false);
  if (!instant) return <span>{emptyText}</span>;

  const title = `Local: ${instant.local}\nZone: ${instant.zone} (${instant.offset})\nUTC: ${instant.utc}`;

  return (
    <Tooltip
      title={title}
      describeChild
      open={open}
      onOpen={() => setOpen(true)}
      onClose={() => setOpen(false)}
      enterTouchDelay={0}
      componentsProps={{ tooltip: { sx: { whiteSpace: "pre-line" } } }}
    >
      <span
        tabIndex={0}
        onTouchStart={() => setOpen(true)}
        onClick={() => setOpen((current) => !current)}
      >
        {withTime ? fDateTimeLocal(value) : fDateLocal(value)}
      </span>
    </Tooltip>
  );
}

LocalDateTime.propTypes = {
  value: PropTypes.oneOfType([
    PropTypes.instanceOf(Date),
    PropTypes.number,
    PropTypes.string,
  ]),
  withTime: PropTypes.bool,
  emptyText: PropTypes.string,
};
