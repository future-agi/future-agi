import PropTypes from "prop-types";
import Tooltip from "@mui/material/Tooltip";

import {
  describeInstant,
  fDateLocal,
  fDateTimeLocal,
} from "src/utils/format-time";

export function LocalDateTime({ value, withTime = false, emptyText = "" }) {
  const instant = describeInstant(value);
  if (!instant) return <span>{emptyText}</span>;

  const title = `Local: ${instant.local}\nZone: ${instant.zone} (${instant.offset})\nUTC: ${instant.utc}`;

  return (
    <Tooltip
      title={title}
      describeChild
      componentsProps={{ tooltip: { sx: { whiteSpace: "pre-line" } } }}
    >
      <span tabIndex={0}>
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
