import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

/**
 * The bordered card every Summary section sits in: the same frame as the
 * existing Summary charts (1px divider border, 8px radius), a 13px bold
 * header with an Iconify glyph, optional subtitle and right-side actions.
 */
const AnalysisCard = ({
  id,
  icon,
  title,
  titleAdornment,
  subtitle,
  actions,
  children,
  footer,
  dense = false,
  sx,
}) => (
  <Box
    id={id}
    component="section"
    sx={{
      border: "1px solid",
      borderColor: "divider",
      borderRadius: 1,
      bgcolor: "background.paper",
      display: "flex",
      flexDirection: "column",
      minWidth: 0,
      scrollMarginTop: 72,
      ...sx,
    }}
  >
    {(title || actions) && (
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        sx={{ px: 1.5, pt: 1.25, pb: subtitle ? 0.25 : 1, minHeight: 40 }}
      >
        {icon && (
          <Iconify
            icon={icon}
            width={18}
            sx={{ color: "text.secondary", flexShrink: 0 }}
          />
        )}
        <Stack
          direction="row"
          alignItems="center"
          gap={0.75}
          sx={{ minWidth: 0, flex: 1 }}
        >
          <Typography
            typography="s1"
            fontWeight="fontWeightSemiBold"
            color="text.primary"
            noWrap
          >
            {title}
          </Typography>
          {titleAdornment}
        </Stack>
        {actions}
      </Stack>
    )}
    {subtitle && (
      <Typography
        typography="s2"
        color="text.secondary"
        sx={{ px: 1.5, pb: 1 }}
      >
        {subtitle}
      </Typography>
    )}
    <Box sx={{ px: dense ? 0 : 1.5, pb: dense ? 0 : 1.5, minWidth: 0 }}>
      {children}
    </Box>
    {footer && (
      <Box
        sx={{
          borderTop: "1px solid",
          borderColor: "divider",
          px: 1.5,
          py: 1,
        }}
      >
        {footer}
      </Box>
    )}
  </Box>
);

AnalysisCard.propTypes = {
  id: PropTypes.string,
  icon: PropTypes.string,
  title: PropTypes.node,
  titleAdornment: PropTypes.node,
  subtitle: PropTypes.node,
  actions: PropTypes.node,
  children: PropTypes.node,
  footer: PropTypes.node,
  dense: PropTypes.bool,
  sx: PropTypes.object,
};

export default AnalysisCard;
