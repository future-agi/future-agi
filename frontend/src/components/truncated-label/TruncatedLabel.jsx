import React, { useState } from "react";
import PropTypes from "prop-types";
import { Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip/CustomTooltip";

// Tooltip opens only when the text is actually truncated.
const TruncatedLabel = ({ text, sx }) => {
  const [open, setOpen] = useState(false);
  return (
    <CustomTooltip
      show
      title={text || ""}
      open={open}
      onClose={() => setOpen(false)}
      arrow
      placement="top"
      size="small"
    >
      <Typography
        variant="body2"
        noWrap
        onMouseEnter={(e) =>
          setOpen(e.currentTarget.scrollWidth > e.currentTarget.clientWidth)
        }
        onMouseLeave={() => setOpen(false)}
        sx={{
          fontSize: 14,
          lineHeight: "22px",
          color: "text.primary",
          flex: 1,
          minWidth: 0,
          ...sx,
        }}
      >
        {text}
      </Typography>
    </CustomTooltip>
  );
};

TruncatedLabel.propTypes = {
  text: PropTypes.string,
  sx: PropTypes.object,
};

export default TruncatedLabel;
