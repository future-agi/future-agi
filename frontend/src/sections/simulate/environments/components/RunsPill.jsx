import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";

export default function RunsPill({ total }) {
  return (
    <Box
      sx={{
        display: "inline-flex",
        alignItems: "center",
        px: 0.75,
        py: 0.25,
        borderRadius: 0.75,
        border: "1px solid",
        borderColor: "divider",
      }}
    >
      <Typography
        sx={{
          typography: "s3",
          fontWeight: "fontWeightBold",
          fontVariantNumeric: "tabular-nums",
          color: "text.secondary",
        }}
      >
        {total}
      </Typography>
    </Box>
  );
}

RunsPill.propTypes = { total: PropTypes.number };
