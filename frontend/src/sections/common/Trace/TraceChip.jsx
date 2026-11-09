import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  IconButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Typography,
  keyframes,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";

const pulse = keyframes`
  0% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.35; transform: scale(0.75); }
  100% { opacity: 1; transform: scale(1); }
`;

/**
 * The look of each state (R2-SPEC §2.1): border style, tone, opacity.
 * Exported for tests and for callers that draw a matching legend.
 */
export const TRACE_CHIP_LOOK = Object.freeze({
  ok: { border: "solid", tone: "default", opacity: 1 },
  running: { border: "solid", tone: "primary", opacity: 1 },
  error: { border: "solid", tone: "error", opacity: 1 },
  none: { border: "dashed", tone: "muted", opacity: 1 },
  carried: { border: "dashed", tone: "default", opacity: 0.6 },
  infra: { border: "solid", tone: "warning", opacity: 1 },
});

const toneColors = (tone, t) => {
  const dark = t.palette.mode === "dark";
  switch (tone) {
    case "error":
      return {
        border: t.palette.error.main,
        fg: dark ? t.palette.error.light : t.palette.error.dark,
        icon: t.palette.error.main,
        bg: alpha(t.palette.error.main, 0.08),
      };
    case "warning":
      return {
        border: t.palette.warning.main,
        fg: dark ? t.palette.warning.light : t.palette.warning.dark,
        icon: t.palette.warning.main,
        bg: alpha(t.palette.warning.main, 0.12),
      };
    case "muted":
      return {
        border: t.palette.text.disabled,
        fg: t.palette.text.secondary,
        icon: t.palette.text.disabled,
        bg: "transparent",
      };
    case "primary":
      return {
        border: t.palette.divider,
        fg: t.palette.text.primary,
        icon: t.palette.primary.main,
        bg: t.palette.background.paper,
      };
    default:
      return {
        border: t.palette.divider,
        fg: t.palette.text.primary,
        icon: t.palette.text.secondary,
        bg: t.palette.background.paper,
      };
  }
};

/**
 * A result's trace as an always-visible chip (R2-SPEC §2.1):
 * `6 steps · 2 tool calls` · `mock` tag · ⋯ menu. Use cellTraceChipProps()
 * (traceChipModel.js) for state / label / mode / tooltip and
 * traceChipMenu() (traceChipMenu.js) for the standard menu.
 *
 * Keyboard: Enter opens (`onOpen`), Shift+F10 or the context-menu key opens
 * the menu. 20 px tall, the label never wraps (ellipsis + tooltip).
 *
 * Props (frozen, R2 manifest row 42):
 * @param {"ok"|"running"|"error"|"none"|"carried"|"infra"} props.state
 * @param {string} props.label
 * @param {"mock"|"recorded"|"live"|null} [props.mode]
 * @param {string} [props.tooltip]  full text (defaults to the label)
 * @param {Function} [props.onOpen]  click / Enter
 * @param {Array<{label: string, icon?: string, onClick: Function,
 *   disabled?: boolean}>} [props.menu]
 * @param {boolean} [props.compact]  narrower label (grid cells)
 */
const TraceChip = ({
  state = "none",
  label,
  mode,
  tooltip,
  onOpen,
  menu,
  compact = false,
}) => {
  const look = TRACE_CHIP_LOOK[state] ?? TRACE_CHIP_LOOK.none;
  const rootRef = useRef(null);
  const [anchor, setAnchor] = useState(null);
  const hasMenu = Array.isArray(menu) && menu.length > 0;
  const clickable = Boolean(onOpen);
  const text = label || "No trace";

  const openMenu = (el) => {
    if (hasMenu) setAnchor(el ?? rootRef.current);
  };

  return (
    <>
      {/* Always mounted (an unmounting tooltip would remount the chip and
          detach the menu anchor); an empty title hides it while the menu is
          open. */}
      <CustomTooltip
        show
        title={anchor ? "" : tooltip || text}
        placement="top"
        size="small"
        arrow
        enterDelay={300}
      >
        <Box
          ref={rootRef}
          role={clickable ? "button" : "group"}
          tabIndex={clickable || hasMenu ? 0 : undefined}
          aria-label={text}
          aria-haspopup={hasMenu ? "menu" : undefined}
          className={`trace-chip trace-chip--${state}`}
          data-trace-state={state}
          onClick={(e) => {
            if (!clickable) return;
            e.stopPropagation();
            onOpen(e);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && clickable) {
              e.preventDefault();
              e.stopPropagation();
              onOpen(e);
            } else if (
              (e.key === "F10" && e.shiftKey) ||
              e.key === "ContextMenu"
            ) {
              e.preventDefault();
              e.stopPropagation();
              openMenu(rootRef.current);
            }
          }}
          onContextMenu={
            hasMenu
              ? (e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  openMenu(rootRef.current);
                }
              : undefined
          }
          sx={(t) => {
            const c = toneColors(look.tone, t);
            return {
              display: "inline-flex",
              alignItems: "center",
              gap: 0.5,
              height: 20,
              maxWidth: compact ? 200 : 280,
              minWidth: 0,
              pl: 0.75,
              pr: hasMenu ? 0.25 : 0.75,
              border: `1px ${look.border} ${c.border}`,
              borderRadius: 0.5,
              bgcolor: c.bg,
              color: c.fg,
              opacity: look.opacity,
              cursor: clickable ? "pointer" : "default",
              verticalAlign: "middle",
              transition: "border-color 120ms, background-color 120ms",
              "&:hover": clickable
                ? {
                    borderColor:
                      look.tone === "default" || look.tone === "primary"
                        ? t.palette.text.disabled
                        : c.border,
                    bgcolor:
                      look.tone === "muted" ? t.palette.action.hover : c.bg,
                  }
                : undefined,
              "&:focus-visible": {
                outline: `2px solid ${t.palette.primary.main}`,
                outlineOffset: 1,
              },
              "& .trace-chip__icon": { color: c.icon },
            };
          }}
        >
          {state === "running" ? (
            <Box
              component="span"
              className="trace-chip__icon"
              sx={{
                width: 6,
                height: 6,
                flexShrink: 0,
                borderRadius: "50%",
                bgcolor: "primary.main",
                animation: `${pulse} 1.2s ease-in-out infinite`,
                "@media (prefers-reduced-motion: reduce)": {
                  animation: "none",
                },
              }}
            />
          ) : (
            <Iconify
              className="trace-chip__icon"
              icon={
                state === "error"
                  ? "mdi:alert-circle-outline"
                  : state === "infra"
                    ? "mdi:server-off"
                    : "mdi:transit-connection-variant"
              }
              width={12}
              sx={{ flexShrink: 0 }}
            />
          )}
          <Typography
            component="span"
            typography="s3"
            noWrap
            sx={{
              minWidth: 0,
              color: "inherit",
              fontWeight: 500,
              lineHeight: "18px",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {text}
          </Typography>
          {mode ? (
            <Box
              component="span"
              sx={{
                flexShrink: 0,
                px: 0.5,
                border: "1px solid",
                borderColor: "divider",
                borderRadius: "3px",
                typography: "s3",
                fontSize: 10,
                lineHeight: "14px",
                color: "text.secondary",
              }}
            >
              {mode}
            </Box>
          ) : null}
          {hasMenu ? (
            <IconButton
              size="small"
              aria-label={`${text} menu`}
              onClick={(e) => {
                e.stopPropagation();
                openMenu(rootRef.current);
              }}
              sx={{ p: 0, ml: 0.25, color: "text.secondary" }}
              tabIndex={-1}
            >
              <Iconify icon="mdi:dots-horizontal" width={14} />
            </IconButton>
          ) : null}
        </Box>
      </CustomTooltip>
      {hasMenu ? (
        <Menu
          anchorEl={anchor}
          open={Boolean(anchor)}
          onClose={() => {
            setAnchor(null);
            rootRef.current?.focus?.();
          }}
          onClick={(e) => e.stopPropagation()}
          anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
          transformOrigin={{ vertical: "top", horizontal: "left" }}
          slotProps={{ paper: { sx: { minWidth: 200, mt: 0.5 } } }}
        >
          {menu.map((item) => (
            <MenuItem
              key={item.label}
              dense
              disabled={item.disabled}
              onClick={(e) => {
                setAnchor(null);
                item.onClick?.(e);
              }}
            >
              {item.icon ? (
                <ListItemIcon>
                  <Iconify icon={item.icon} width={16} />
                </ListItemIcon>
              ) : null}
              <ListItemText primaryTypographyProps={{ typography: "s1" }}>
                {item.label}
              </ListItemText>
            </MenuItem>
          ))}
        </Menu>
      ) : null}
    </>
  );
};

TraceChip.propTypes = {
  state: PropTypes.oneOf([
    "ok",
    "running",
    "error",
    "none",
    "carried",
    "infra",
  ]),
  label: PropTypes.string,
  mode: PropTypes.oneOf(["mock", "recorded", "live", null]),
  tooltip: PropTypes.string,
  onOpen: PropTypes.func,
  menu: PropTypes.arrayOf(
    PropTypes.shape({
      label: PropTypes.string.isRequired,
      icon: PropTypes.string,
      onClick: PropTypes.func,
      disabled: PropTypes.bool,
    }),
  ),
  compact: PropTypes.bool,
};

export default TraceChip;
