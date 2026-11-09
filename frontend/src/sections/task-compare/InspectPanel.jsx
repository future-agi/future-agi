import React, { useRef } from "react";
import PropTypes from "prop-types";
import { Box, IconButton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import KeyHintTooltip from "src/sections/common/ux/KeyHintTooltip";

export const INSPECT_STRIP_H = 28;
export const INSPECT_MIN_H = 120;

/**
 * The Inspect panel (ONE-VIEW-SPEC §7.3, A4-09), VS Code's bottom panel:
 * a 28 px tab strip above the review bar — `Answers · Checks · Timeline ·
 * Tool calls · Prompts · Workspace · Scores · JSON` — that opens a panel
 * over the strip (drag its top edge to resize; the height is remembered).
 * Clicking the active tab or `Esc` closes it; `I` toggles the last tab.
 * On short windows the panel floats over the steps as a sheet, so the
 * review bar never leaves the screen. Closed by default.
 *
 * `strip` and `panel` render separately so the workspace can place the
 * panel in flow (tall windows) or as a sheet (short ones).
 */
export function InspectStrip({ tabs, tab, onTab, onOpen, aside }) {
  // IT-R1 G4: closed, the strip is one `Inspect ▸` button (the tabs show
  // once the panel is open), so the footer reads calm by default.
  if (!tab && onOpen)
    return (
      <Stack
        direction="row"
        alignItems="center"
        data-testid="tc-inspect-strip"
        sx={{
          height: INSPECT_STRIP_H,
          flexShrink: 0,
          px: 1,
          gap: 1,
          borderTop: "1px solid",
          borderColor: "divider",
          bgcolor: "background.neutral",
          minWidth: 0,
          overflow: "hidden",
        }}
      >
        <KeyHintTooltip
          title={`Open the Inspect panel: ${tabs.map((t) => t.label).join(", ")}`}
          action="inspect"
        >
          <Box
            component="button"
            type="button"
            onClick={onOpen}
            aria-expanded={false}
            data-testid="tc-inspect-open"
            sx={{
              all: "unset",
              boxSizing: "border-box",
              display: "inline-flex",
              alignItems: "center",
              gap: 0.5,
              height: INSPECT_STRIP_H - 6,
              px: 0.875,
              borderRadius: 0.5,
              typography: "s3",
              fontWeight: 700,
              color: "text.primary",
              cursor: "pointer",
              whiteSpace: "nowrap",
              flexShrink: 0,
              "&:hover": { bgcolor: "action.hover" },
              "&:focus-visible": {
                outline: "2px solid",
                outlineColor: "primary.main",
                outlineOffset: -2,
              },
            }}
          >
            Inspect
            <Iconify icon="mdi:chevron-up" width={16} />
          </Box>
        </KeyHintTooltip>
        <Typography
          typography="s3"
          color="text.secondary"
          noWrap
          sx={{ minWidth: 0 }}
        >
          {tabs
            .slice(0, 4)
            .map((t) => t.label)
            .join(" · ")}
          {tabs.length > 4 ? " …" : ""}
        </Typography>
        <Box sx={{ flex: 1, minWidth: 8 }} />
        {aside}
      </Stack>
    );
  return (
    <Stack
      direction="row"
      alignItems="center"
      role="tablist"
      aria-label="Inspect"
      data-testid="tc-inspect-strip"
      sx={{
        height: INSPECT_STRIP_H,
        flexShrink: 0,
        px: 1,
        gap: 0.25,
        borderTop: "1px solid",
        borderColor: "divider",
        bgcolor: "background.neutral",
        minWidth: 0,
        overflow: "hidden",
      }}
    >
      <KeyHintTooltip title="Inspect panel" action="inspect">
        <Typography
          typography="s3"
          color="text.secondary"
          fontWeight={700}
          sx={{ px: 0.5, flexShrink: 0, cursor: "default" }}
        >
          Inspect
        </Typography>
      </KeyHintTooltip>
      {tabs.map((t) => {
        const on = tab === t.value;
        return (
          <CustomTooltip
            key={t.value}
            describeChild
            show
            size="small"
            title={t.tip}
          >
            <Box
              component="button"
              type="button"
              role="tab"
              aria-selected={on}
              data-inspect-tab={t.value}
              onClick={() => onTab(on ? null : t.value)}
              sx={{
                all: "unset",
                boxSizing: "border-box",
                height: INSPECT_STRIP_H - 6,
                px: 0.875,
                borderRadius: 0.5,
                typography: "s3",
                fontWeight: on ? 700 : 500,
                color: on ? "text.primary" : "text.secondary",
                bgcolor: on ? "background.paper" : "transparent",
                boxShadow: on
                  ? (th) => `inset 0 -2px 0 ${th.palette.primary.main}`
                  : "none",
                cursor: "pointer",
                whiteSpace: "nowrap",
                flexShrink: 0,
                "&:hover": { color: "text.primary", bgcolor: "action.hover" },
                "&:focus-visible": {
                  outline: "2px solid",
                  outlineColor: "primary.main",
                  outlineOffset: -2,
                },
              }}
            >
              {t.label}
            </Box>
          </CustomTooltip>
        );
      })}
      <Box sx={{ flex: 1, minWidth: 8 }} />
      {aside}
      {tab ? (
        <KeyHintTooltip title="Close the panel" action="escape">
          <IconButton
            size="small"
            aria-label="Close the panel"
            onClick={() => onTab(null)}
            sx={{ p: 0.25, flexShrink: 0 }}
          >
            <Iconify icon="mdi:chevron-down" width={18} />
          </IconButton>
        </KeyHintTooltip>
      ) : null}
    </Stack>
  );
}
InspectStrip.propTypes = {
  tabs: PropTypes.array.isRequired,
  tab: PropTypes.string,
  onTab: PropTypes.func.isRequired,
  onOpen: PropTypes.func,
  aside: PropTypes.node,
};

/** The panel body with a drag handle on its top edge. */
export function InspectSheet({
  height,
  maxHeight,
  onHeight,
  sheet,
  label,
  children,
}) {
  const drag = useRef(null);
  const onDown = (e) => {
    e.preventDefault();
    drag.current = { y: e.clientY, h: height };
    const move = (ev) => {
      const d = drag.current;
      if (!d) return;
      const next = Math.round(d.h + (d.y - ev.clientY));
      onHeight(Math.max(INSPECT_MIN_H, Math.min(maxHeight, next)));
    };
    const up = () => {
      drag.current = null;
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };
  return (
    <Box
      role="tabpanel"
      aria-label={label}
      data-testid="tc-inspect-panel"
      onKeyDown={(e) => {
        if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
        if (e.target !== e.currentTarget.firstChild) return;
        e.preventDefault();
        const step = e.key === "ArrowUp" ? 24 : -24;
        onHeight(Math.max(INSPECT_MIN_H, Math.min(maxHeight, height + step)));
      }}
      sx={{
        height,
        flexShrink: 0,
        display: "flex",
        flexDirection: "column",
        bgcolor: "background.paper",
        borderTop: "1px solid",
        borderColor: "divider",
        ...(sheet
          ? {
              position: "absolute",
              left: 0,
              right: 0,
              bottom: 0,
              zIndex: 6,
              boxShadow: (th) =>
                `0 -8px 24px ${th.palette.mode === "dark" ? "rgba(0,0,0,0.5)" : "rgba(0,0,0,0.12)"}`,
            }
          : null),
      }}
    >
      <Box
        role="separator"
        aria-orientation="horizontal"
        aria-label="Resize the panel (↑ ↓)"
        tabIndex={0}
        onMouseDown={onDown}
        sx={{
          height: 6,
          mt: "-3px",
          flexShrink: 0,
          cursor: "row-resize",
          position: "relative",
          zIndex: 1,
          "&:hover, &:focus-visible": { bgcolor: "primary.main", opacity: 0.4 },
          outline: "none",
        }}
      />
      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>{children}</Box>
    </Box>
  );
}
InspectSheet.propTypes = {
  height: PropTypes.number.isRequired,
  maxHeight: PropTypes.number.isRequired,
  onHeight: PropTypes.func.isRequired,
  sheet: PropTypes.bool,
  label: PropTypes.string,
  children: PropTypes.node,
};
