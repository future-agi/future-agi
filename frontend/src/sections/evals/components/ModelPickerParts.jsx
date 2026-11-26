import React from "react";
import PropTypes from "prop-types";
import { Box, MenuItem, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";

// Shared pieces of the evaluation model picker, so every section (Future AGI,
// Decision models, Your models) reads as one menu:
//   section label  11/500 in fg.tertiary, sentence case, optional count
//   row            16px icon · 12px name (500 when chosen) · an 11px meta
//                  line in fg.tertiary · an optional 11px status line (key or
//                  plan state, kept whole) · trailing check / lock / key / arrow

export function PickerSectionLabel({ children, count, adornment }) {
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 0.75,
        px: 1.5,
        pt: 1.25,
        pb: 0.5,
      }}
    >
      <Typography
        component="div"
        sx={{
          fontSize: 11,
          lineHeight: "16px",
          fontWeight: 500,
          color: "text.disabled",
        }}
      >
        {children}
      </Typography>
      {count ? (
        <Typography
          component="div"
          sx={{ fontSize: 11, lineHeight: "16px", color: "text.disabled" }}
        >
          {count}
        </Typography>
      ) : null}
      {adornment}
    </Box>
  );
}

PickerSectionLabel.propTypes = {
  children: PropTypes.node,
  count: PropTypes.number,
  adornment: PropTypes.node,
};

const TRAILING_ICONS = {
  check: "solar:check-linear",
  lock: "solar:lock-linear",
  key: "solar:key-linear",
  arrow: "solar:alt-arrow-right-linear",
};

// The Future AGI mark, for Turing models.
export function FagiMark() {
  return (
    <SvgColor
      src="/favicon/logo.svg"
      sx={{ width: 16, height: 16, color: "text.secondary", flexShrink: 0 }}
    />
  );
}

export function PickerRow({
  icon,
  label,
  badge,
  meta,
  status,
  selected = false,
  disabled = false,
  trailing,
  tooltip = "",
  onClick,
  menuItemProps,
}) {
  const leading =
    typeof icon === "string" ? (
      <Iconify
        icon={icon}
        width={16}
        sx={{ color: "text.secondary", flexShrink: 0 }}
      />
    ) : (
      icon
    );

  return (
    <CustomTooltip
      show={Boolean(tooltip)}
      title={tooltip}
      placement="right"
      size="small"
      arrow
    >
      <span>
        <MenuItem
          selected={selected}
          disabled={disabled}
          onClick={onClick}
          {...menuItemProps}
          sx={{
            mx: 0.5,
            px: 1,
            py: 0.5,
            gap: 1,
            borderRadius: "6px",
            alignItems: meta || status ? "flex-start" : "center",
            "&.Mui-selected, &.Mui-selected:hover": {
              bgcolor: "action.selected",
            },
            "&.Mui-disabled": { opacity: 1 },
            ...menuItemProps?.sx,
          }}
        >
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              height: 18,
              flexShrink: 0,
              opacity: disabled ? 0.5 : 1,
            }}
          >
            {leading}
          </Box>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Box sx={{ display: "flex", alignItems: "baseline", gap: 0.75 }}>
              <Typography
                noWrap
                sx={{
                  fontSize: 12,
                  lineHeight: "18px",
                  fontWeight: selected ? 500 : 400,
                  color: disabled ? "text.disabled" : "text.primary",
                }}
              >
                {label}
              </Typography>
              {badge ? (
                <Typography
                  component="span"
                  noWrap
                  sx={{
                    fontSize: 11,
                    lineHeight: "16px",
                    color: "text.disabled",
                    flexShrink: 0,
                  }}
                >
                  {badge}
                </Typography>
              ) : null}
            </Box>
            {meta ? (
              <Typography
                noWrap
                sx={{
                  fontSize: 11,
                  lineHeight: "16px",
                  color: "text.disabled",
                }}
              >
                {meta}
              </Typography>
            ) : null}
            {status ? (
              <Typography
                sx={{
                  fontSize: 11,
                  lineHeight: "16px",
                  color: "text.disabled",
                }}
              >
                {status}
              </Typography>
            ) : null}
          </Box>
          {trailing ? (
            <Box sx={{ display: "flex", alignItems: "center", height: 18 }}>
              <Iconify
                icon={TRAILING_ICONS[trailing]}
                width={16}
                sx={{
                  color:
                    trailing === "check" ? "text.primary" : "text.disabled",
                  flexShrink: 0,
                }}
              />
            </Box>
          ) : null}
        </MenuItem>
      </span>
    </CustomTooltip>
  );
}

PickerRow.propTypes = {
  icon: PropTypes.oneOfType([PropTypes.string, PropTypes.node]),
  label: PropTypes.node.isRequired,
  badge: PropTypes.node,
  meta: PropTypes.node,
  status: PropTypes.node,
  selected: PropTypes.bool,
  disabled: PropTypes.bool,
  trailing: PropTypes.oneOf(["check", "lock", "key", "arrow"]),
  tooltip: PropTypes.node,
  onClick: PropTypes.func,
  menuItemProps: PropTypes.object,
};
