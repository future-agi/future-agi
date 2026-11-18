import Tooltip from "@mui/material/Tooltip";
import React from "react";
import { styled } from "@mui/material/styles";

// The design-system tooltip (theme MuiTooltip: inverse surface, 12px, radius 4,
// 240 wide). `size` and `type` are kept for existing callers but no longer
// change the look. `card` (or `expandable`) renders the DS hover card for rich
// content: breakdowns, previews, small tables.
const CTooltip = styled(
  ({ className, slotProps, size, expandable, card, classes, ...props }) => (
    <Tooltip
      {...props}
      slotProps={slotProps}
      classes={{
        ...classes,
        popper: className,
        ...((card || expandable) && {
          tooltip: [classes?.tooltip, "ds-hovercard"].filter(Boolean).join(" "),
        }),
      }}
    />
  ),
)({});

const EXPANDABLE_FLIP_PADDING = 300;

const CustomTooltip = ({
  show,
  type,
  slotProps = {},
  size = "medium",
  expandable = false,
  card = false,
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

  return (
    <CTooltip
      size={size}
      expandable={expandable}
      card={card}
      slotProps={updatedSlotProps}
      {...props}
    />
  );
};

CustomTooltip.propTypes = {
  ...Tooltip.propTypes,
};

export default CustomTooltip;
