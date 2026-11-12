import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box } from "@mui/material";
import CustomTooltip from "src/components/tooltip";

/**
 * One line of text with a CSS ellipsis and the full text on hover when it is
 * cut. Used where G0's single-line `Truncate` can measure a 0-width flex box
 * on first layout and stay cut at one character (its ResizeObserver keeps
 * the pre-tooltip node; reported to G0). Multi-line text still uses Truncate.
 */
export default function OneLine({ text, mono, sx }) {
  const ref = useRef(null);
  const [cut, setCut] = useState(false);
  const value = String(text ?? "");
  return (
    <CustomTooltip
      describeChild
      show
      size="small"
      enterDelay={400}
      title={cut ? value.slice(0, 600) : ""}
    >
      <Box
        ref={ref}
        component="span"
        onMouseEnter={() => {
          const el = ref.current;
          if (el) setCut(el.scrollWidth > el.clientWidth + 1);
        }}
        sx={{
          display: "block",
          minWidth: 0,
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
          fontFamily: mono ? "monospace" : undefined,
          ...sx,
        }}
      >
        {value}
      </Box>
    </CustomTooltip>
  );
}

OneLine.propTypes = {
  text: PropTypes.any,
  mono: PropTypes.bool,
  sx: PropTypes.object,
};
