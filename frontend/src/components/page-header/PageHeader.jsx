import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Tooltip, Typography } from "@mui/material";

import Iconify from "src/components/iconify";

// ----------------------------------------------------------------------
// Future AGI Design System · Page header. One header for every page: a 16px
// title (with an optional status badge next to it), one line of description
// in fg.tertiary, actions on the right — at most one primary button. No icon
// tiles, no coloured titles. `info` puts the description in a tooltip instead.
// Page rhythm: header → tabs 12px, tabs → toolbar 12px, toolbar → table 8px.
// ----------------------------------------------------------------------

export default function PageHeader({ title, badge, description, info, actions, back, sx }) {
  return (
    <Stack
      direction="row"
      alignItems="flex-start"
      justifyContent="space-between"
      spacing={2}
      sx={{ mb: 1.5, ...sx }}
    >
      <Stack direction="row" alignItems="flex-start" spacing={1} sx={{ minWidth: 0 }}>
        {back}
        <Box sx={{ minWidth: 0 }}>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography
              component="h1"
              noWrap
              sx={{ fontSize: 16, lineHeight: "24px", fontWeight: 600, color: "text.primary" }}
            >
              {title}
            </Typography>
            {info && (
              <Tooltip title={info} arrow>
                <Iconify icon="solar:info-circle-linear" width={14} sx={{ color: "text.disabled" }} />
              </Tooltip>
            )}
            {badge}
          </Stack>
          {description && (
            <Typography sx={{ fontSize: 13, lineHeight: "20px", color: "text.tertiary" }}>
              {description}
            </Typography>
          )}
        </Box>
      </Stack>
      {actions && (
        <Stack direction="row" spacing={1} alignItems="center" sx={{ flexShrink: 0 }}>
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
