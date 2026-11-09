import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Divider,
  IconButton,
  Stack,
  Tab,
  Tabs,
  Typography,
  useMediaQuery,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import ResizableSplit from "src/sections/annotations/queues/annotate/resizable-split";
import { withKeyHint } from "src/utils/keymap";
import { KeyHintTitle } from "src/sections/common/ux/KeyHintTooltip";
import { fmtCount } from "src/sections/common/format/fmt";

const MIN_LEFT = 360;

/** Small bordered icon button, the look of the dataset drawer's ▲ ▼. */
export const PanelIconButton = ({
  title,
  icon,
  onClick,
  disabled,
  label,
  bordered = false,
  active = false,
  action,
  hintTitle,
}) => (
  <CustomTooltip
    show
    size="small"
    placement="bottom"
    arrow
    disableInteractive
    title={
      action || hintTitle ? (
        <KeyHintTitle title={hintTitle ?? title} action={action} />
      ) : (
        title
      )
    }
  >
    <span>
      <IconButton
        size="small"
        aria-label={label ?? title}
        onClick={onClick}
        disabled={disabled}
        sx={{
          width: 28,
          height: 28,
          borderRadius: 0.5,
          color: active ? "primary.main" : "text.secondary",
          ...(bordered && {
            border: "1px solid",
            borderColor: "divider",
          }),
        }}
      >
        <Iconify icon={icon} width={18} />
      </IconButton>
    </span>
  </CustomTooltip>
);

PanelIconButton.propTypes = {
  title: PropTypes.node.isRequired,
  icon: PropTypes.string.isRequired,
  onClick: PropTypes.func,
  disabled: PropTypes.bool,
  label: PropTypes.string,
  bordered: PropTypes.bool,
  active: PropTypes.bool,
  /** R3 (P2): KEYMAP action → the tooltip reads `Label  [K]` with a cap. */
  action: PropTypes.string,
  /** R3 (P2): the label beside the cap (defaults to `title`). */
  hintTitle: PropTypes.node,
};

/** A tab label with a pill badge (hidden at 0). */
const TabLabel = ({ label, badge }) => (
  <Box
    component="span"
    sx={{ display: "inline-flex", alignItems: "center", gap: 0.75 }}
  >
    {label}
    {badge > 0 && (
      <Box
        component="span"
        sx={{
          minWidth: 20,
          height: 18,
          px: 0.625,
          borderRadius: 9,
          bgcolor: "action.selected",
          color: "text.secondary",
          typography: "s3",
          fontWeight: 600,
          lineHeight: "18px",
          textAlign: "center",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {fmtCount(badge)}
      </Box>
    )}
  </Box>
);

TabLabel.propTypes = { label: PropTypes.string, badge: PropTypes.number };

/** `12 of 71 filtered`, `– of 71 filtered` (outside the set), `3 of 248`. */
export const positionText = (position) => {
  if (!position || !Number.isFinite(position.total)) return "";
  const at = Number.isFinite(position.index) ? position.index : "–";
  return `${at} of ${fmtCount(position.total)}${
    position.filtered ? " filtered" : ""
  }`;
};

/**
 * RowPanelShell — the one non-modal side panel for a row: the dataset Row
 * view and the result drawer (PRODUCT-SPEC §4, C12). Wraps the annotate
 * workspace's ResizableSplit. The grid is always the split's left pane, so
 * opening, expanding and closing the panel never remounts it (the pane is
 * hidden instead). Keys come from useRowKeyboardNav (Esc: full → split →
 * closed is the caller's onClose / onToggleFull).
 *
 * Props (frozen):
 * @param {boolean} props.open  closed -> only `grid` shows
 * @param {string} [props.storageKey]  localStorage key for the panel width
 * @param {number} [props.initialWidth=640]
 * @param {number} [props.minWidth=440]
 * @param {number} [props.maxWidth=1100]
 * @param {boolean} [props.full]  panel alone, centred, max 1280 px
 * @param {Function} [props.onToggleFull]
 * @param {Function} props.onClose
 * @param {{title: React.ReactNode, position?: {index: number|null,
 *   total: number, filtered?: boolean}, onPrev?: Function, onNext?: Function,
 *   prevDisabled?: boolean, nextDisabled?: boolean, idChip?: React.ReactNode,
 *   actions?: React.ReactNode, line2?: React.ReactNode}} props.header
 *   Optional (L1): `header.menu` renders after Expand (the ⋯ menu).
 *   `position.index` is 1-based for display.
 *   R3 (P2, additive): `header.subtitle` (node) replaces the position text
 *   (`#32 of 248` with its own tooltip); `header.after` renders after the
 *   id chip (split chips, kept on line 1).
 * @param {Array<{value: string, label: string, count?: number}>} [props.tabs]
 *   R3 (P2, additive): a tab's `badge` (number) renders as a pill, hidden
 *   at 0 (`count` keeps the `(9)` text for older callers).
 * @param {React.ReactNode} [props.footer]  R3 (P2): a strip under the body
 *   (the Simple key hints).
 * @param {number} [props.overlayBelow]  R3 (P2, RV#16): below this viewport
 *   width the open panel covers the grid (full width; the grid stays
 *   mounted, hidden).
 * @param {boolean} [props.keyHints]  R3 (P2, RV#3): the shell's own buttons
 *   show `Label  [K]` tooltips with key caps.
 * @param {boolean} [props.compactTabs]  R3 (P2): tighter tab spacing and
 *   scroll arrows when the tabs overflow (many kind tabs).
 * @param {boolean} [props.bodyBusy]  R3 (P2, RV#44): dims the body while
 *   the next row loads (the previous one stays visible).
 * @param {boolean} [props.focusRing]  R3 (P2, RV#43): every focusable
 *   control in the panel shows the same 2 px primary ring on
 *   `:focus-visible`, in both themes.
 * @param {string} [props.tab]
 * @param {(value: string) => void} [props.onTab]
 * @param {React.ReactNode} props.children  the active tab's content
 * @param {React.ReactNode} props.grid  the grid the panel docks beside
 */
const RowPanelShell = ({
  open,
  storageKey,
  initialWidth = 640,
  minWidth = 440,
  maxWidth = 1100,
  full = false,
  onToggleFull,
  onClose,
  header = {},
  tabs,
  tab,
  onTab,
  children,
  grid,
  footer,
  overlayBelow,
  bodyBusy = false,
  keyHints = false,
  compactTabs = false,
  focusRing = false,
}) => {
  const hint = (label, action) =>
    keyHints ? { hintTitle: label, action } : {};
  const pos = positionText(header.position);
  const narrow = useMediaQuery(
    overlayBelow ? `(max-width:${overlayBelow - 0.02}px)` : "(max-width:0px)",
  );
  const forcedFull = Boolean(open && overlayBelow && narrow);
  const isFull = full || forcedFull;
  // RV#16: while it covers the grid on a narrow window, the panel ends at
  // the window's right edge even when the page around it is wider (so the
  // actions and Close never sit off-screen).
  const fitRef = useRef(null);
  const [fitWidth, setFitWidth] = useState(null);
  useEffect(() => {
    if (!forcedFull) {
      setFitWidth(null);
      return undefined;
    }
    const measure = () => {
      const el = fitRef.current;
      if (!el) return;
      const left = el.getBoundingClientRect().left + window.scrollX;
      const room = document.documentElement.clientWidth - left;
      setFitWidth(room > 320 ? Math.floor(room) : null);
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [forcedFull]);
  const panel = open ? (
    <Stack
      data-testid="row-panel"
      sx={{
        height: "100%",
        minHeight: 0,
        bgcolor: "background.paper",
        ...(focusRing && {
          "& :is(button, a, [role=tab], [role=button], [tabindex]):focus-visible":
            {
              outline: "2px solid",
              outlineColor: "primary.main",
              outlineOffset: 1,
              borderRadius: 0.5,
            },
        }),
      }}
    >
      <Box sx={{ px: 2, pt: 1.25, pb: header.line2 ? 1 : 0.5, flexShrink: 0 }}>
        <Stack
          direction="row"
          alignItems="center"
          gap={0.5}
          sx={{ minWidth: 0, minHeight: 32 }}
        >
          <PanelIconButton
            bordered
            title={
              header.prevDisabled
                ? "First row in this view"
                : withKeyHint("Previous row", "prev")
            }
            {...(!header.prevDisabled && hint("Previous row", "prev"))}
            label="Previous row"
            icon="mdi:chevron-up"
            onClick={header.onPrev}
            disabled={!header.onPrev || header.prevDisabled}
          />
          <PanelIconButton
            bordered
            title={
              header.nextDisabled
                ? "Last row in this view"
                : withKeyHint("Next row", "next")
            }
            {...(!header.nextDisabled && hint("Next row", "next"))}
            label="Next row"
            icon="mdi:chevron-down"
            onClick={header.onNext}
            disabled={!header.onNext || header.nextDisabled}
          />
          <Typography
            typography="m3"
            fontWeight="fontWeightSemiBold"
            color="text.primary"
            noWrap
            sx={{ ml: 1, minWidth: 0, flexShrink: 0 }}
          >
            {header.title}
          </Typography>
          {header.subtitle ??
            (pos && (
              <Typography
                typography="s2"
                color="text.secondary"
                noWrap
                sx={{
                  ml: 0.5,
                  flexShrink: 0,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {pos}
              </Typography>
            ))}
          {/* The id/splits area gives way first in a narrow panel so the
              actions and Close stay on screen. */}
          {header.idChip && (
            <Box sx={{ ml: 0.5, minWidth: 0, overflow: "hidden" }}>
              {header.idChip}
            </Box>
          )}
          {header.after}
          <Box sx={{ flex: 1, minWidth: 8 }} />
          {header.actions}
          {onToggleFull && !forcedFull && (
            <PanelIconButton
              title={withKeyHint(full ? "Restore" : "Expand", "full")}
              {...hint(
                full ? "Show the grid beside the row" : "Expand to full width",
                "full",
              )}
              label={full ? "Restore" : "Expand"}
              icon={full ? "mdi:arrow-collapse" : "mdi:arrow-expand"}
              onClick={onToggleFull}
            />
          )}
          {header.menu}
          <PanelIconButton
            title={withKeyHint("Close", "escape")}
            {...hint("Close", "escape")}
            label="Close"
            icon="mdi:close"
            onClick={onClose}
          />
        </Stack>
        {header.line2 && (
          <Stack
            direction="row"
            alignItems="center"
            gap={0.75}
            sx={{ mt: 1, minWidth: 0, flexWrap: "wrap" }}
          >
            {header.line2}
          </Stack>
        )}
      </Box>
      {tabs?.length > 0 && (
        <>
          <Tabs
            value={tab ?? tabs[0].value}
            onChange={(_, v) => onTab?.(v)}
            variant="scrollable"
            scrollButtons={compactTabs ? "auto" : false}
            sx={{
              px: compactTabs ? 1 : 2,
              minHeight: 40,
              flexShrink: 0,
              "& .MuiTabs-scrollButtons": { width: 28 },
              "& .MuiTab-root": {
                minHeight: 40,
                px: 0,
                mr: compactTabs ? 2.25 : 3,
                typography: "s1",
                fontWeight: "fontWeightMedium",
                color: "text.secondary",
              },
              "& .Mui-selected": { color: "primary.main" },
            }}
          >
            {tabs.map((t) => (
              <Tab
                key={t.value}
                value={t.value}
                data-testid={`row-tab-${t.value}`}
                label={
                  Number.isFinite(t.badge) ? (
                    <TabLabel label={t.label} badge={t.badge} />
                  ) : Number.isFinite(t.count) ? (
                    `${t.label} (${t.count})`
                  ) : (
                    t.label
                  )
                }
              />
            ))}
          </Tabs>
          <Divider />
        </>
      )}
      <Box
        data-testid="row-panel-body"
        aria-busy={bodyBusy || undefined}
        sx={{
          flex: 1,
          minHeight: 0,
          overflow: "auto",
          position: "relative",
          transition: "opacity 120ms",
          opacity: bodyBusy ? 0.55 : 1,
        }}
      >
        {children}
      </Box>
      {footer && (
        <Box
          sx={{
            flexShrink: 0,
            borderTop: "1px solid",
            borderColor: "divider",
            px: 2,
            py: 0.5,
          }}
        >
          {footer}
        </Box>
      )}
    </Stack>
  ) : null;

  return (
    <ResizableSplit
      left={grid ?? <Box />}
      right={
        // One wrapper in both sizes, so F never remounts the panel body.
        <Box
          sx={{
            height: "100%",
            display: "flex",
            justifyContent: forcedFull ? "flex-start" : "center",
            bgcolor: isFull ? "background.default" : "background.paper",
          }}
        >
          <Box
            ref={fitRef}
            sx={{
              width: "100%",
              maxWidth:
                forcedFull && fitWidth ? fitWidth : full ? 1280 : "none",
              height: "100%",
              minWidth: 0,
              borderLeft: full ? "1px solid" : 0,
              borderRight: full ? "1px solid" : 0,
              borderColor: "divider",
            }}
          >
            {panel}
          </Box>
        </Box>
      }
      rightHidden={!open}
      leftHidden={open && isFull}
      leftSx={{ display: "flex", minHeight: 0, overflow: "hidden" }}
      storageKey={storageKey}
      initialRightWidth={initialWidth}
      minRightWidth={minWidth}
      maxRightWidth={maxWidth}
      minLeftWidth={MIN_LEFT}
    />
  );
};

RowPanelShell.propTypes = {
  open: PropTypes.bool.isRequired,
  storageKey: PropTypes.string,
  initialWidth: PropTypes.number,
  minWidth: PropTypes.number,
  maxWidth: PropTypes.number,
  full: PropTypes.bool,
  onToggleFull: PropTypes.func,
  onClose: PropTypes.func.isRequired,
  header: PropTypes.shape({
    title: PropTypes.node,
    position: PropTypes.shape({
      index: PropTypes.number,
      total: PropTypes.number,
      filtered: PropTypes.bool,
    }),
    onPrev: PropTypes.func,
    onNext: PropTypes.func,
    prevDisabled: PropTypes.bool,
    nextDisabled: PropTypes.bool,
    idChip: PropTypes.node,
    actions: PropTypes.node,
    menu: PropTypes.node,
    line2: PropTypes.node,
    subtitle: PropTypes.node,
    after: PropTypes.node,
  }),
  tabs: PropTypes.arrayOf(
    PropTypes.shape({
      value: PropTypes.string.isRequired,
      label: PropTypes.string.isRequired,
      count: PropTypes.number,
      badge: PropTypes.number,
    }),
  ),
  tab: PropTypes.string,
  onTab: PropTypes.func,
  children: PropTypes.node,
  grid: PropTypes.node,
  footer: PropTypes.node,
  overlayBelow: PropTypes.number,
  bodyBusy: PropTypes.bool,
  focusRing: PropTypes.bool,
  keyHints: PropTypes.bool,
  compactTabs: PropTypes.bool,
};

export default RowPanelShell;
