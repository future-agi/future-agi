import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { dsTypography } from "src/theme/ds-tokens";

// Adapted from Vel 05a5f9f77a. One title, one description, one primary action.
export default function PageHeader({
  title,
  badge,
  description,
  info,
  actions,
  back,
  sx,
}) {
  return (
    <Stack
      direction={{ xs: "column", sm: "row" }}
      alignItems="flex-start"
      justifyContent="space-between"
      spacing={1.5}
      sx={{ mb: 1.5, minWidth: 0, ...sx }}
    >
      <Stack
        direction="row"
        alignItems="flex-start"
        spacing={1}
        sx={{ minWidth: 0, maxWidth: "100%" }}
      >
        {back}
        <Box sx={{ minWidth: 0 }}>
          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{ flexWrap: "wrap" }}
          >
            <Typography
              component="h1"
              sx={{
                ...dsTypography.title_md,
                fontSize: 15,
                lineHeight: "22px",
                fontWeight: 600,
                color: "text.primary",
                overflowWrap: "anywhere",
              }}
            >
              {title}
            </Typography>
            {info && (
              <Tooltip title={info} arrow>
                <Box
                  component="span"
                  tabIndex={0}
                  aria-label="More information"
                  sx={{ display: "inline-flex" }}
                >
                  <Iconify
                    icon="solar:info-circle-linear"
                    width={14}
                    sx={{ color: "text.secondary" }}
                  />
                </Box>
              </Tooltip>
            )}
            {badge}
          </Stack>
          {description && (
            <Typography
              sx={{
                ...dsTypography.body_sm,
                fontSize: 13,
                lineHeight: "20px",
                color: "text.tertiary",
              }}
            >
              {description}
            </Typography>
          )}
        </Box>
      </Stack>
      {actions && (
        <Stack
          direction="row"
          spacing={1}
          alignItems="center"
          sx={{ flexWrap: "wrap", maxWidth: "100%" }}
        >
          {actions}
        </Stack>
      )}
    </Stack>
  );
}

PageHeader.propTypes = {
  title: PropTypes.node.isRequired,
  badge: PropTypes.node,
  description: PropTypes.node,
  info: PropTypes.node,
  actions: PropTypes.node,
  back: PropTypes.node,
  sx: PropTypes.object,
};
