import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";

// ----------------------------------------------------------------------
// Future AGI Design System · Section. A titled block of a page: 14px/600
// title, optional one-line description and actions, then the content.
// `variant="plain"` (default) sits on the page with no box; `"panel"` adds
// the one panel style (1px border.default, radius 8, no shadow). Never nest
// a panel inside a panel — use plain inside. Pass `onClick` for a whole-panel
// click target (hover tint, keyboard activation).
// ----------------------------------------------------------------------

export default function Section({
  title,
  description,
  actions,
  variant,
  onClick,
  disabled,
  children,
  sx,
}) {
  const panel = variant === "panel";
  const clickable = Boolean(onClick) && !disabled;
  return (
    <Box
      {...(onClick && {
        role: "button",
        tabIndex: disabled ? -1 : 0,
        "aria-disabled": disabled || undefined,
        onClick: clickable ? onClick : undefined,
        onKeyDown: (e) => {
          if (
            clickable &&
            e.target === e.currentTarget &&
            (e.key === "Enter" || e.key === " ")
          ) {
            e.preventDefault();
            onClick(e);
          }
        },
      })}
      sx={{
        ...(onClick && {
          cursor: clickable ? "pointer" : "default",
          "&:hover": clickable ? { bgcolor: "action.hover" } : undefined,
        }),
        ...(panel && {
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "8px",
          p: 2,
        }),
        ...sx,
      }}
    >
      {(title || actions) && (
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
          sx={{ mb: 1.5 }}
        >
          <Box sx={{ minWidth: 0 }}>
            {title && (
              <Typography
                sx={{
                  fontSize: 14,
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
                sx={{
                  fontSize: 13,
                  lineHeight: "20px",
                  color: "text.tertiary",
                }}
              >
                {description}
              </Typography>
            )}
          </Box>
          {actions && (
            <Stack direction="row" spacing={1}>
              {actions}
            </Stack>
          )}
        </Stack>
      )}
      {children}
    </Box>
  );
}

Section.propTypes = {
  title: PropTypes.node,
  description: PropTypes.node,
  actions: PropTypes.node,
  variant: PropTypes.oneOf(["plain", "panel"]),
  onClick: PropTypes.func,
  disabled: PropTypes.bool,
  children: PropTypes.node,
  sx: PropTypes.object,
};

Section.defaultProps = { variant: "plain" };
