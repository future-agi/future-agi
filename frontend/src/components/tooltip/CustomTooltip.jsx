import Tooltip, { tooltipClasses } from "@mui/material/Tooltip";
import React from "react";
import { styled } from "@mui/material/styles";

const CTooltip = styled(({ className, slotProps, size, ...props }) => (
  <Tooltip {...props} slotProps={slotProps} classes={{ popper: className }} />
))(({ theme, size }) => ({
  [`& .${tooltipClasses.tooltip}`]: {
    padding: size === "small" ? "8px 12px" : "12px 16px",
    maxWidth: "400px",
    fontSize: size === "small" ? "12px" : "13px",
    fontWeight: 400,
    backgroundColor: theme.palette.background.default,
    color: theme.palette.text.primary,
    boxShadow: `0px 1px 4px ${theme.palette.divider}`,
    borderRadius: "8px",
    border: `1px solid ${theme.palette.divider}`,
  },
  [`& .${tooltipClasses.arrow}`]: {
    color: theme.palette.divider,
    "&::before": {
      backgroundColor: theme.palette.background.default,
      border: `1px solid ${theme.palette.divider}`,
    },
  },
}));

const EXPANDABLE_FLIP_PADDING = 300;

const CustomTooltip = ({
  show,
  type,
  slotProps = {},
  size = "medium",
  expandable = false,
  ...props
}) => {
  if (!show) return <>{props.children}</>;

  let updatedSlotProps = { ...slotProps };

  if (expandable) {
    const existingPopper = updatedSlotProps.popper || {};
    const existingModifiers = Array.isArray(existingPopper.modifiers)
      ? existingPopper.modifiers
      : [];
    const hasFlip = existingModifiers.some((m) => m.name === "flip");
    const flipModifier = {
      name: "flip",
      options: { padding: { bottom: EXPANDABLE_FLIP_PADDING } },
    };

    updatedSlotProps = {
      ...updatedSlotProps,
      popper: {
        ...existingPopper,
        modifiers: hasFlip
          ? existingModifiers.map((m) =>
              m.name === "flip"
                ? {
                    ...m,
                    options: {
                      ...m.options,
                      padding: {
                        ...(typeof m.options?.padding === "object"
                          ? m.options.padding
                          : {}),
                        bottom: EXPANDABLE_FLIP_PADDING,
                      },
                    },
                  }
                : m,
            )
          : [...existingModifiers, flipModifier],
      },
    };
  }

  if (type === "black") {
    // Always use a true dark background so the tooltip stays readable in
    // both light and dark modes. In dark mode, `text.secondary` resolves to
    // a light gray (#a1a1aa) which makes white tooltip content unreadable.
    const blackBg = (theme) =>
      theme.palette.mode === "dark"
        ? theme.palette.grey[900]
        : theme.palette.text.secondary;
    const blackFg = (theme) =>
      theme.palette.mode === "dark"
        ? theme.palette.common.white
        : theme.palette.background.paper;

    updatedSlotProps = {
      ...updatedSlotProps,
      tooltip: {
        ...updatedSlotProps.tooltip,
        sx: {
          ...(updatedSlotProps.tooltip?.sx || {}),
          backgroundColor: (theme) => `${blackBg(theme)} !important`,
          borderColor: (theme) => `${blackBg(theme)} !important`,
          color: (theme) => `${blackFg(theme)} !important`,
        },
      },
      arrow: {
        ...updatedSlotProps.arrow,
        sx: {
          ...(updatedSlotProps.arrow?.sx || {}),
          "&::before": {
            backgroundColor: (theme) => `${blackBg(theme)} !important`,
            borderColor: (theme) => `${blackBg(theme)} !important`,
          },
        },
      },
    };
  }
  return <CTooltip size={size} slotProps={updatedSlotProps} {...props} />;
};

CustomTooltip.propTypes = {
  ...Tooltip.propTypes,
};

export default CustomTooltip;
