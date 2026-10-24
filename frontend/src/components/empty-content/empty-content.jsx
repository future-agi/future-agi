import React from "react";
import PropTypes from "prop-types";

import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import { alpha } from "@mui/material/styles";
import Typography from "@mui/material/Typography";

import Iconify from "src/components/iconify";

// ----------------------------------------------------------------------
// Design System empty state: a 40px rounded square holding a 20px icon, a
// 14/600 title, a 13px tertiary body (max 340px) and at most one action.
// ErrorState (src/components/error-state) reuses this layout.

export default function EmptyContent({
  title,
  icon = "solar:inbox-linear",
  img,
  imgUrl,
  action,
  filled,
  description,
  tone,
  sx,
  ...other
}) {
  const image = img || imgUrl;
  return (
    <Stack
      flexGrow={1}
      alignItems="center"
      justifyContent="center"
      spacing={1.5}
      sx={{
        px: 3,
        py: 4,
        height: 1,
        textAlign: "center",
        ...(filled && {
          borderRadius: 2,
          bgcolor: (theme) => alpha(theme.palette.text.disabled, 0.04),
          border: (theme) =>
            `dashed 1px ${alpha(theme.palette.text.disabled, 0.08)}`,
        }),
        ...sx,
      }}
      {...other}
    >
      <Box
        sx={{
          width: 40,
          height: 40,
          flexShrink: 0,
          borderRadius: 1,
          display: "grid",
          placeItems: "center",
          bgcolor: (theme) =>
            tone?.(theme)?.bg ?? theme.palette.background.muted,
          color: (theme) => tone?.(theme)?.fg ?? theme.palette.action.active,
        }}
      >
        {image ? (
          <Box
            component="img"
            alt=""
            src={image}
            sx={{ width: 24, height: 24, objectFit: "contain" }}
          />
        ) : typeof icon === "string" ? (
          <Iconify icon={icon} width={20} />
        ) : (
          icon
        )}
      </Box>

      {title && (
        <Typography
          component="span"
          sx={{
            fontSize: 13,
            lineHeight: "20px",
            fontWeight: 600,
            color: "text.primary",
          }}
        >
          {title}
        </Typography>
      )}

      {description && (
        <Typography
          component="span"
          sx={{
            fontSize: 13,
            lineHeight: "20px",
            fontWeight: 400,
            color: "text.tertiary",
            maxWidth: 340,
            mt: "4px !important",
          }}
        >
          {description}
        </Typography>
      )}

      {action && <Box sx={{ mt: "16px !important" }}>{action}</Box>}
    </Stack>
  );
}

EmptyContent.propTypes = {
  action: PropTypes.node,
  description: PropTypes.node,
  filled: PropTypes.bool,
  /** Iconify name, or a node sized 20px (e.g. <SvgColor />). */
  icon: PropTypes.oneOfType([PropTypes.string, PropTypes.node]),
  img: PropTypes.string,
  imgUrl: PropTypes.string,
  sx: PropTypes.object,
  /** (theme) => ({ bg, fg }) — colours the icon square (used by ErrorState). */
  tone: PropTypes.func,
  title: PropTypes.node,
};
