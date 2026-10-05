import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, IconButton, Stack } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip/CustomTooltip";

import { BUILD_TONES } from "../buildEnvironment/buildTones";
import { ENV_TAB_RAIL_HEIGHT } from "../environmentOptions";
import {
  CHAT_PANE_COPY as COPY,
  CHAT_PANE_DEFAULT_WIDTH,
  CHAT_PANE_DIVIDER_WIDTH as DIVIDER_WIDTH,
  CHAT_PANE_DRAG_THRESHOLD as DRAG_THRESHOLD,
  CHAT_PANE_KEY_STEP as KEY_STEP,
  CHAT_PANE_KEY_STEP_LARGE as KEY_STEP_LARGE,
  CHAT_PANE_MAX_WIDTH,
  CHAT_PANE_MIN_RIGHT_WIDTH as MIN_RIGHT_WIDTH,
  CHAT_PANE_MIN_WIDTH,
  CHAT_PANE_RAIL_WIDTH as RAIL_WIDTH,
  CHAT_PANE_STORAGE_KEYS,
} from "./chatSplitPane.constants";

// localStorage can be missing or throw (private windows, blocked site data), so
// every access is guarded and the layout falls back to its defaults.
const readStored = (key) => {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
};

const writeStored = (key, value) => {
  try {
    window.localStorage.setItem(key, String(value));
  } catch {
    // Not persisting is fine; the layout still works for this visit.
  }
};

// A zero container width means it hasn't been measured yet (or jsdom), so only
// the absolute max applies. Floored, so a fractional container (browser zoom)
// still gives a whole-pixel max that the shown width can never round past.
const maxWidthFor = (containerWidth) =>
  containerWidth > 0
    ? Math.max(
        CHAT_PANE_MIN_WIDTH,
        Math.min(
          CHAT_PANE_MAX_WIDTH,
          Math.floor(containerWidth - MIN_RIGHT_WIDTH - DIVIDER_WIDTH),
        ),
      )
    : CHAT_PANE_MAX_WIDTH;

const clampWidth = (width, containerWidth = 0) =>
  Math.round(
    Math.min(maxWidthFor(containerWidth), Math.max(CHAT_PANE_MIN_WIDTH, width)),
  );

const initialWidth = () => {
  const stored = Number(readStored(CHAT_PANE_STORAGE_KEYS.width));
  return Number.isFinite(stored) && stored > 0
    ? clampWidth(stored)
    : CHAT_PANE_DEFAULT_WIDTH;
};

/**
 * The builder chat and the environment panes as one flush surface.
 *
 * The chat sits on the left at a draggable width; a 1px line separates it from
 * the right pane and doubles as the resize handle. Collapsing swaps the chat
 * for a slim rail (an expand arrow over a chat icon), but the chat stays
 * mounted (only hidden), so a streaming reply, an in-flight turn or a
 * half-typed draft survives it.
 * Width and collapsed state are remembered per browser.
 *
 * `chat` is a render prop, `({ collapse, open, collapseRef }) => node`, so the
 * chat owns its collapse button (attach `collapseRef` to it so focus can move
 * between it and the rail's expand arrow) and knows when it is visible again.
 */
export default function ChatSplitPane({ chat, children, busy = false }) {
  const containerRef = useRef(null);
  const dragRef = useRef(null);
  const chatLayerRef = useRef(null);
  const railRef = useRef(null);
  const collapseRef = useRef(null);
  const expandRef = useRef(null);
  // Set when a toggle hides the layer that holds focus, so focus follows to the
  // counterpart button instead of falling back to the page.
  const handOffFocus = useRef(false);
  // The width the user chose, and the width the container allows right now.
  // The shown width is derived from both, so narrowing the window never
  // overwrites the choice and widening it again brings the choice back.
  const [preferredWidth, setPreferredWidth] = useState(initialWidth);
  const [containerWidth, setContainerWidth] = useState(0);
  const [collapsed, setCollapsed] = useState(
    () => readStored(CHAT_PANE_STORAGE_KEYS.collapsed) === "true",
  );
  const [dragging, setDragging] = useState(false);

  const width = clampWidth(preferredWidth, containerWidth);

  // Measure before the first paint, then follow window resizes.
  useLayoutEffect(() => {
    setContainerWidth(containerRef.current?.getBoundingClientRect().width || 0);
  }, []);
  useEffect(() => {
    const el = containerRef.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver((entries) => {
      const next = entries[0]?.contentRect?.width || 0;
      if (next) setContainerWidth(next);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // Arrow keys: a step that doesn't change what's shown (pressing past the max
  // in a narrow window) leaves the chosen width alone.
  const resize = useCallback(
    (next) => {
      const clamped = clampWidth(next, containerWidth);
      if (clamped === width) return;
      setPreferredWidth(clamped);
      writeStored(CHAT_PANE_STORAGE_KEYS.width, clamped);
    },
    [containerWidth, width],
  );

  // Double-click resets the choice itself, not to whatever fits right now.
  const resetWidth = useCallback(() => {
    setPreferredWidth(CHAT_PANE_DEFAULT_WIDTH);
    writeStored(CHAT_PANE_STORAGE_KEYS.width, CHAT_PANE_DEFAULT_WIDTH);
  }, []);

  const setOpen = useCallback((open) => {
    const hiding = open ? railRef.current : chatLayerRef.current;
    handOffFocus.current = !!hiding?.contains(document.activeElement);
    // Collapsing removes the separator; don't leave a drag behind with it.
    if (!open) {
      dragRef.current = null;
      setDragging(false);
    }
    setCollapsed(!open);
    writeStored(CHAT_PANE_STORAGE_KEYS.collapsed, !open);
  }, []);

  // The newly shown layer is visible as soon as this commits, so its button can
  // take focus before the old layer fades out and hides.
  useLayoutEffect(() => {
    if (!handOffFocus.current) return;
    handOffFocus.current = false;
    (collapsed ? expandRef : collapseRef).current?.focus();
  }, [collapsed]);

  const collapse = useCallback(() => setOpen(false), [setOpen]);
  const expand = useCallback(() => setOpen(true), [setOpen]);

  const onPointerDown = (event) => {
    // Primary button only: a right-click opens the context menu, which can
    // swallow the pointerup and leave a drag running.
    if (event.button !== 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    dragRef.current = {
      startX: event.clientX,
      startWidth: width,
      active: false,
    };
  };

  const onPointerMove = (event) => {
    const drag = dragRef.current;
    if (!drag) return;
    const dx = event.clientX - drag.startX;
    if (!drag.active) {
      if (Math.abs(dx) < DRAG_THRESHOLD) return;
      drag.active = true;
      setDragging(true);
    }
    const next = clampWidth(drag.startWidth + dx, containerWidth);
    // Pushing past the max in a narrow window changes nothing on screen, so it
    // must not replace the chosen width either.
    if (drag.width == null && next === drag.startWidth) return;
    drag.width = next;
    setPreferredWidth(next);
  };

  // Ends on pointerup, pointercancel and lost capture alike.
  const endDrag = (event) => {
    if (!dragRef.current) return;
    const el = event.currentTarget;
    if (el?.hasPointerCapture?.(event.pointerId)) {
      el.releasePointerCapture(event.pointerId);
    }
    const { width: dragged } = dragRef.current;
    dragRef.current = null;
    setDragging(false);
    if (dragged != null) writeStored(CHAT_PANE_STORAGE_KEYS.width, dragged);
  };

  const onKeyDown = (event) => {
    const step = event.shiftKey ? KEY_STEP_LARGE : KEY_STEP;
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      resize(width - step);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      resize(width + step);
    }
  };

  // Rendered once per open/close, not per frame: a drag only changes the
  // column width, so the conversation isn't re-rendered on every pointer move.
  const chatNode = useMemo(
    () => chat({ collapse, open: !collapsed, collapseRef }),
    [chat, collapse, collapsed],
  );

  // One column eases between the chat width and the rail; the chat and the rail
  // crossfade inside it. The chat keeps its own width while the column narrows,
  // so it is clipped rather than reflowed, and the right pane grows smoothly.
  // Dragging and reduced motion skip the animation.
  const columnWidth = (collapsed ? RAIL_WIDTH : width) + DIVIDER_WIDTH;
  const animate = (t, props) =>
    dragging
      ? "none"
      : t.transitions.create(props, {
          duration: collapsed
            ? t.transitions.duration.leavingScreen
            : t.transitions.duration.enteringScreen,
          easing: t.transitions.easing.easeInOut,
        });
  // Fade out, then hide (visibility waits for the fade), so a hidden layer is
  // skipped by focus and screen readers while staying mounted.
  const layer = (shown) => (t) => ({
    opacity: shown ? 1 : 0,
    visibility: shown ? "visible" : "hidden",
    transition: dragging
      ? "none"
      : `opacity ${t.transitions.duration.shorter}ms ${t.transitions.easing.easeInOut}, visibility 0ms linear ${shown ? 0 : t.transitions.duration.shorter}ms`,
    "@media (prefers-reduced-motion: reduce)": { transition: "none" },
  });

  return (
    <Box
      ref={containerRef}
      sx={{
        display: "flex",
        height: "100%",
        minHeight: 0,
        bgcolor: "background.paper",
        overflow: "clip",
        // Stop text selection across both panes while dragging.
        userSelect: dragging ? "none" : undefined,
      }}
    >
      <Box
        sx={{
          position: "relative",
          width: columnWidth,
          flexShrink: 0,
          borderRight: "1px solid",
          borderColor: "divider",
          transition: (t) => animate(t, "width"),
          "@media (prefers-reduced-motion: reduce)": { transition: "none" },
        }}
      >
        {/* `clip`, not `hidden`: a clip box can't be scrolled, so nothing inside
            (focus, a scroll-to-latest) can shift the chat sideways mid-animation. */}
        <Box sx={{ position: "absolute", inset: 0, overflow: "clip" }}>
          {/* Hidden with visibility (after the fade) rather than aria-hidden,
              which would hide a subtree that still holds focus mid-toggle. */}
          <Box
            ref={chatLayerRef}
            data-testid="chat-split-chat"
            sx={[
              {
                display: "flex",
                flexDirection: "column",
                width,
                height: "100%",
                minHeight: 0,
                overflow: "clip",
              },
              layer(!collapsed),
            ]}
          >
            {chatNode}
          </Box>

          <Stack
            ref={railRef}
            alignItems="center"
            sx={[
              {
                position: "absolute",
                top: 0,
                left: 0,
                bottom: 0,
                width: RAIL_WIDTH,
              },
              layer(collapsed),
            ]}
          >
            {/* Same height as the tab rail, so the expand arrow lines up with the tabs. */}
            <Box
              sx={{
                height: ENV_TAB_RAIL_HEIGHT,
                boxSizing: "content-box",
                borderBottom: "1px solid",
                borderColor: "transparent",
                display: "grid",
                placeItems: "center",
              }}
            >
              {/* Tooltips only while collapsed, so one can't linger over the
                  rail as it fades out after a click. */}
              <CustomTooltip
                show={collapsed}
                size="small"
                arrow
                placement="right"
                title={COPY.expand}
              >
                <IconButton
                  ref={expandRef}
                  aria-label={COPY.expand}
                  onClick={expand}
                  size="small"
                  sx={{
                    width: 26,
                    height: 26,
                    borderRadius: 1,
                    color: "text.subtitle",
                    "&:hover": {
                      bgcolor: "action.hover",
                      color: "text.primary",
                    },
                  }}
                >
                  <Iconify icon="lucide:chevrons-right" width={16} />
                </IconButton>
              </CustomTooltip>
            </Box>

            <IconButton
              aria-label={COPY.open}
              onClick={expand}
              sx={{
                position: "relative",
                mt: 0.5,
                width: 28,
                height: 28,
                borderRadius: 1,
                color: BUILD_TONES.accent,
                bgcolor: (t) =>
                  alpha(
                    BUILD_TONES.accent,
                    t.palette.mode === "dark" ? 0.16 : 0.1,
                  ),
                "&:hover": {
                  bgcolor: (t) =>
                    alpha(
                      BUILD_TONES.accent,
                      t.palette.mode === "dark" ? 0.26 : 0.18,
                    ),
                },
              }}
            >
              <Iconify icon="solar:chat-round-line-linear" width={15} />
              {busy && collapsed && (
                <Box
                  data-testid="chat-split-busy"
                  sx={{
                    position: "absolute",
                    top: 3,
                    right: 3,
                    width: 7,
                    height: 7,
                    borderRadius: 999,
                    bgcolor: BUILD_TONES.accent,
                    border: "1.5px solid",
                    borderColor: "background.paper",
                  }}
                />
              )}
            </IconButton>
          </Stack>
        </Box>

        {!collapsed && (
          // Sits over the column's right border with a wider invisible hit area;
          // the line itself lights up on hover, focus and drag.
          <Box
            role="separator"
            aria-orientation="vertical"
            aria-label={COPY.resize}
            aria-valuenow={width}
            aria-valuemin={CHAT_PANE_MIN_WIDTH}
            aria-valuemax={maxWidthFor(containerWidth)}
            tabIndex={0}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={endDrag}
            onPointerCancel={endDrag}
            onLostPointerCapture={endDrag}
            onKeyDown={onKeyDown}
            onDoubleClick={resetWidth}
            sx={{
              position: "absolute",
              top: 0,
              bottom: 0,
              right: -5,
              width: 9,
              zIndex: 1,
              cursor: "col-resize",
              outline: "none",
              "&::after": {
                content: '""',
                position: "absolute",
                top: 0,
                bottom: 0,
                left: 4,
                width: "1px",
                bgcolor: dragging ? BUILD_TONES.accent : "transparent",
                transition: "background-color 0.15s ease",
              },
              "&:hover::after, &:focus-visible::after": {
                bgcolor: BUILD_TONES.accent,
              },
            }}
          />
        )}
      </Box>

      <Box
        sx={{
          flex: 1,
          minWidth: 0,
          minHeight: 0,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
      >
        {children}
      </Box>
    </Box>
  );
}

ChatSplitPane.propTypes = {
  // ({ collapse, open }) => node
  chat: PropTypes.func.isRequired,
  children: PropTypes.node,
  // Shows a dot on the collapsed rail while the builder is working or waiting
  // on an answer from the user.
  busy: PropTypes.bool,
};
