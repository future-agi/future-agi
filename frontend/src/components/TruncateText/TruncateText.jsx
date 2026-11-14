import { useState } from "react";
import PropTypes from "prop-types";
import { Box, ButtonBase } from "@mui/material";

const TruncateText = ({ defaultTruncated = true, children, showLines = 2 }) => {
  const [isTruncated, setTruncated] = useState(defaultTruncated);

  // Convert children to string for truncation
  const textContent =
    typeof children === "string" ? children : String(children || "");

  // Calculate maxLength from showLines if maxLength not provided (approximate 50 chars per line)
  const effectiveMaxLength = showLines * 75;

  const shouldTruncate = textContent.length > effectiveMaxLength;
  const displayText =
    isTruncated && shouldTruncate
      ? textContent.slice(0, effectiveMaxLength)
      : textContent;

  return (
    <Box component="span" sx={{ display: "inline" }}>
      <Box component="span" sx={{ display: "inline" }}>
        {displayText}
      </Box>
      {shouldTruncate && (
        <ButtonBase
          onClick={(e) => {
            e.stopPropagation();
            setTruncated((s) => !s);
          }}
          component="span"
          sx={{
            color: "primary.main",
            font: "inherit",
            display: "inline",
            whiteSpace: "nowrap",
            ml: 0.5,
          }}
        >
          see {isTruncated ? "more" : "less"}
        </ButtonBase>
      )}
    </Box>
  );
};

TruncateText.propTypes = {
  defaultTruncated: PropTypes.bool,
  children: PropTypes.node,
  showLines: PropTypes.number,
  maxLength: PropTypes.number,
};

export default TruncateText;
