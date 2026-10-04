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
  const [touchOpen, setTouchOpen] = useState(false);
  if (!instant) return <span>{emptyText}</span>;

  const title = `Local: ${instant.local}\nZone: ${instant.zone} (${instant.offset})\nUTC: ${instant.utc}`;

  return (
    <Tooltip
      title={title}
      describeChild
      open={touchOpen || undefined}
      onClose={() => setTouchOpen(false)}
      componentsProps={{ tooltip: { sx: { whiteSpace: "pre-line" } } }}
    >
      <span
        tabIndex={0}
        onTouchStart={() => setTouchOpen(true)}
        onClick={() => setTouchOpen((open) => !open)}
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
