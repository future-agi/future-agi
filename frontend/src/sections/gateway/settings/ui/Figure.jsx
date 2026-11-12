import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

// One labelled number, as on Settings > Billing (AI Gateway credits).
export default function Figure({
  label,
  value,
  caption,
  large = false,
  testId,
  sx,
}) {
  return (
    <Box data-testid={testId} sx={{ minWidth: 0, ...sx }}>
      <Typography typography="s2" color="text.secondary" noWrap>
        {label}
      </Typography>
      <Typography
        typography={large ? "m1" : "m3"}
        fontWeight={large ? "fontWeightMedium" : "fontWeightRegular"}
        color="text.primary"
        noWrap
      >
        {value}
      </Typography>
      {caption && (
        <Typography typography="s2" color="text.secondary">
          {caption}
        </Typography>
      )}
    </Box>
  );
}

Figure.propTypes = {
  label: PropTypes.node.isRequired,
  value: PropTypes.node.isRequired,
  caption: PropTypes.node,
  large: PropTypes.bool,
  testId: PropTypes.string,
  sx: PropTypes.object,
};
