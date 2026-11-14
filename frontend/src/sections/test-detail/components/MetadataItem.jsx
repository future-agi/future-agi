import { Box, Chip, Typography } from "@mui/material";
import React from "react";
import PropTypes from "prop-types";
import SvgColor from "src/components/svg-color";

/**
 * Reusable metadata item component
 * Handles rendering with divider and supports text or chip display
 */
const MetadataItem = ({
  condition = true,
  value,
  label: _label,
  showDivider = true,
  variant = "text", // "text" or "chip"
  sx = {},
  iconSrc = null,
  iconSx = {},
}) => {
  if (!condition || !value) return null;

  const divider = showDivider && (
    <Typography
      typography="s2_1"
      color="text.disabled"
      fontWeight="fontWeightRegular"
      sx={{ mx: 0.5 }}
    >
      |
    </Typography>
  );

  const content =
    variant === "chip" ? (
      <Chip
        label={value}
        icon={
          iconSrc ? (
            <SvgColor sx={{ width: 20, ...iconSx }} src={iconSrc} />
          ) : undefined
        }
        size="small"
        sx={{
          typography: "s1",
          color: "status.info.fg",
          bgcolor: "status.info.bg",
          "&:hover": {
            bgcolor: "status.info.bg",
          },
          "& .MuiChip-icon": {
            color: "status.info.fg",
          },
          ...sx,
        }}
      />
    ) : (
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
        {iconSrc && <SvgColor sx={{ width: 20, ...iconSx }} src={iconSrc} />}
        <Typography
          typography="s2_1"
          color="text.secondary"
          fontWeight="fontWeightRegular"
          sx={{ ...sx }}
        >
          {value}
        </Typography>
      </Box>
    );

  return (
    <Box sx={{ display: "flex", flexDirection: "row", alignItems: "center" }}>
      {showDivider && divider}
      {content}
    </Box>
  );
};

MetadataItem.propTypes = {
  condition: PropTypes.bool,
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  label: PropTypes.string,
  showDivider: PropTypes.bool,
  variant: PropTypes.oneOf(["text", "chip"]),
  sx: PropTypes.object,
  iconSrc: PropTypes.string,
  iconSx: PropTypes.object,
};

export default MetadataItem;
