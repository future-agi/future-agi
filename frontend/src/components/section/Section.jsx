import React, { createContext, useContext } from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { dsTypography, dsRadius, dsSize } from "src/theme/ds-tokens";

// Adapted from Vel 05a5f9f77a; v0.2 panels use 36px headers/12px bodies.
const PanelContext = createContext(false);
export default function Section({
  title,
  description,
  actions,
  variant = "plain",
  onClick,
  disabled,
  children,
  sx,
}) {
  const insidePanel = useContext(PanelContext);
  const panel = variant === "panel" && !insidePanel;
  const clickable = Boolean(onClick) && !disabled;
  return (
    <PanelContext.Provider value={insidePanel || panel}>
      <Box
        {...(onClick && {
          role: "button",
          tabIndex: disabled ? -1 : 0,
          "aria-disabled": disabled || undefined,
          onClick: clickable ? onClick : undefined,
          onKeyDown: (event) => {
            if (
              clickable &&
              event.target === event.currentTarget &&
              ["Enter", " "].includes(event.key)
            ) {
              event.preventDefault();
              onClick(event);
            }
          },
        })}
        sx={{
          minWidth: 0,
          ...(onClick && {
            cursor: clickable ? "pointer" : "default",
            "&:hover": clickable ? { bgcolor: "action.hover" } : undefined,
          }),
          ...(panel && {
            border: "1px solid",
            borderColor: "divider",
            borderRadius: `${dsRadius.lg}px`,
            bgcolor: "background.paper",
          }),
          ...sx,
        }}
      >
        {(title || description || actions) && (
          <Stack
            direction="row"
            alignItems="center"
            justifyContent="space-between"
            sx={{
              gap: 1,
              flexWrap: "wrap",
              ...(panel
                ? {
                    minHeight: dsSize.control.lg,
                    px: 1.5,
                    py: 0.5,
                    borderBottom: "1px solid",
                    borderColor: "divider",
                  }
                : { mb: 1.5 }),
            }}
          >
            <Box sx={{ minWidth: 0 }}>
              {title && (
                <Typography
                  component="h2"
                  sx={{ ...dsTypography.title_xs, color: "text.primary" }}
                >
                  {title}
                </Typography>
              )}
              {description && (
                <Typography
                  sx={{ ...dsTypography.body_sm, color: "text.tertiary" }}
                >
                  {description}
                </Typography>
              )}
            </Box>
            {actions && (
              <Stack direction="row" spacing={1} sx={{ flexWrap: "wrap" }}>
                {actions}
              </Stack>
            )}
          </Stack>
        )}
        <Box sx={panel ? { p: 1.5, minWidth: 0 } : { minWidth: 0 }}>
          {children}
        </Box>
      </Box>
    </PanelContext.Provider>
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
