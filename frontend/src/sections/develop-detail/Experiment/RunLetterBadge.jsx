import React from "react";
import PropTypes from "prop-types";
import { Box } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { runColorAt } from "src/sections/common/Runs/runColors";

/**
 * The run's letter in its colour (the square the experiment grid shows on
 * each column group), so `A` here is `#9A` everywhere after the run starts.
 */
const RunLetterBadge = ({ letter, index, ordinal, size = 20 }) => {
  const color = runColorAt(index ?? Math.max(0, letter.charCodeAt(0) - 65));
  const badge = (
    <Box
      component="span"
      sx={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        flexShrink: 0,
        width: size,
        height: size,
        borderRadius: 0.5,
        bgcolor: color.solid,
        color: color.fg,
        typography: "s3",
        fontWeight: "fontWeightSemiBold",
        lineHeight: 1,
      }}
    >
      {letter}
    </Box>
  );
  if (ordinal == null) return badge;
  return (
    <CustomTooltip
      show
      title={`Runs as #${ordinal}${letter}`}
      size="small"
      type="black"
      arrow
    >
      {badge}
    </CustomTooltip>
  );
};

RunLetterBadge.propTypes = {
  letter: PropTypes.string.isRequired,
  index: PropTypes.number,
  ordinal: PropTypes.number,
  size: PropTypes.number,
};

export default RunLetterBadge;
