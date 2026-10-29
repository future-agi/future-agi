import React from "react";
import PropTypes from "prop-types";
import { Tooltip, Typography } from "@mui/material";
import useIsTruncated from "src/hooks/useIsTruncated";

// Add a keyboard stop only when the label has hidden text to reveal. Keep the
// text node mounted as its width changes so resizing cannot discard focus.
export default function ClippedColumnLabel({
  label,
  children,
  placement = "left",
  variant = "body2",
  sx,
}) {
  const [textRef, isClipped] = useIsTruncated(label);
  return (
    <Tooltip
      title={isClipped ? label : ""}
      arrow
      placement={placement}
      describeChild
    >
      <Typography
        ref={textRef}
        component="span"
        variant={variant}
        noWrap
        tabIndex={isClipped ? 0 : undefined}
        aria-label={isClipped ? label : undefined}
        sx={{ display: "block", minWidth: 0, ...sx }}
      >
        {label}
        {children}
      </Typography>
    </Tooltip>
  );
}

ClippedColumnLabel.propTypes = {
  label: PropTypes.string.isRequired,
  children: PropTypes.node,
  placement: PropTypes.oneOf(["left", "top"]),
  variant: PropTypes.string,
  sx: PropTypes.object,
};
