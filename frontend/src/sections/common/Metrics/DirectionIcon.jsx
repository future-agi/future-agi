import React from "react";
import PropTypes from "prop-types";
import { Box } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";

/**
 * DirectionIcon — ↑ / ↓ after a metric name (D4).
 * Renders nothing for a metric without a direction.
 *
 * Props (frozen):
 * @param {boolean|null} props.higherIsBetter
 * @param {number} [props.size=14]
 */
const DirectionIcon = ({ higherIsBetter, size = 14 }) => {
  if (higherIsBetter == null) return null;
  return (
    <CustomTooltip
      show
      size="small"
      placement="top"
      arrow
      title={higherIsBetter ? "Higher is better" : "Lower is better"}
    >
      <Box
        component="span"
        role="img"
        aria-label={higherIsBetter ? "Higher is better" : "Lower is better"}
        sx={{ display: "inline-flex", flexShrink: 0, color: "text.disabled" }}
      >
        <Iconify
          icon={
            higherIsBetter
              ? "material-symbols:arrow-upward-rounded"
              : "material-symbols:arrow-downward-rounded"
          }
          width={size}
        />
      </Box>
    </CustomTooltip>
  );
};

DirectionIcon.propTypes = {
  higherIsBetter: PropTypes.bool,
  size: PropTypes.number,
};

export default DirectionIcon;
