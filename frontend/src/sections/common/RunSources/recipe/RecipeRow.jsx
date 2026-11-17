// One labelled row of a run config (lane Rb): a fixed label column and the
// control(s), so the recipe, voice, endpoint and sandbox configs line up.
import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";

export const LABEL_W = 116;

export default function RecipeRow({
  label,
  hint,
  children,
  align = "center",
  sx,
  testId,
}) {
  const title = (
    <Typography
      typography="s2"
      color="text.secondary"
      noWrap
      sx={{
        width: LABEL_W,
        flexShrink: 0,
        pt: align === "flex-start" ? 0.5 : 0,
        ...(hint
          ? {
              textDecoration: "underline dotted",
              textDecorationColor: "var(--text-disabled)",
              textUnderlineOffset: 3,
            }
          : null),
      }}
    >
      {label}
    </Typography>
  );
  return (
    <Stack
      direction="row"
      alignItems={align}
      gap={1}
      sx={{ minWidth: 0, minHeight: 30, ...sx }}
      data-testid={testId}
    >
      {hint ? (
        <CustomTooltip show title={hint} size="small" placement="top-start">
          <span>{title}</span>
        </CustomTooltip>
      ) : (
        title
      )}
      <Box
        sx={{
          flex: 1,
          minWidth: 0,
          display: "flex",
          alignItems: "center",
          gap: 1,
          flexWrap: "wrap",
        }}
      >
        {children}
      </Box>
    </Stack>
  );
}

RecipeRow.propTypes = {
  label: PropTypes.string.isRequired,
  hint: PropTypes.string,
  children: PropTypes.node,
  align: PropTypes.string,
  sx: PropTypes.object,
  testId: PropTypes.string,
};

/** A small outlined segmented choice (MUI ToggleButtonGroup look, s2). */
export const segmentedSx = {
  "& .MuiToggleButton-root": {
    py: 0.25,
    px: 1,
    typography: "s2",
    textTransform: "none",
    lineHeight: 1.6,
  },
};
