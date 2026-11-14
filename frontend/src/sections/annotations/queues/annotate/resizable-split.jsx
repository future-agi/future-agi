import { useCallback, useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box } from "@mui/material";
import { alpha } from "@mui/material/styles";

const DEFAULT_HANDLE_WIDTH = 6;

/**
 * Horizontal two-pane layout with a draggable splitter between left and
 * right children. The splitter persists its position to localStorage
 * under ``storageKey`` so the same review pair re-opens at the user's
 * preferred ratio.
 *
 * Width semantics:
 *   - ``initialRightWidth`` / persisted value drives the right pane.
 *   - ``minLeftWidth`` and ``minRightWidth`` clamp the drag so neither
 *     pane collapses below something usable.
 *   - When the container narrows past ``minLeftWidth + minRightWidth``,
 *     the right pane gives up width first; its overflow becomes a
 *     vertical scroll inside the child rather than horizontal page
 *     scroll on the workspace itself.
 *
 * The handle area is wider than the visible line (8px hit target vs 1px
 * border) so users don't have to pixel-hunt to grab it.
 */
export default function ResizableSplit({
  left,
  right,
  initialRightWidth = 400,
  minLeftWidth = 480,
  minRightWidth = 340,
  maxRightWidth = 720,
  storageKey,
  rightBackground = "background.paper",
}) {
  const containerRef = useRef(null);
  const [rightWidth, setRightWidth] = useState(() => {
    if (typeof window === "undefined" || !storageKey) return initialRightWidth;
    const stored = Number(window.localStorage.getItem(storageKey));
    return Number.isFinite(stored) && stored > 0 ? stored : initialRightWidth;
  });
  const [isDragging, setIsDragging] = useState(false);
  const [hoverHandle, setHoverHandle] = useState(false);

  // Clamp the right width whenever the container resizes — without this,
  // a window resize past min thresholds leaves the panes in an awkward
  // state.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return undefined;
    const observer = new ResizeObserver((entries) => {
      const containerWidth = entries[0]?.contentRect?.width || 0;
      if (!containerWidth) return;
      const cap = Math.max(
        minRightWidth,
        Math.min(
          maxRightWidth,
          containerWidth - minLeftWidth - DEFAULT_HANDLE_WIDTH,
        ),
      );
      setRightWidth((prev) => Math.min(prev, cap));
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [minLeftWidth, minRightWidth, maxRightWidth]);

  const persist = useCallback(
    (value) => {
      if (!storageKey || typeof window === "undefined") return;
      window.localStorage.setItem(storageKey, String(value));
    },
    [storageKey],
  );

  const onPointerDown = useCallback((event) => {
    event.preventDefault();
    event.target.setPointerCapture?.(event.pointerId);
    setIsDragging(true);
  }, []);

  const onPointerMove = useCallback(
    (event) => {
      if (!isDragging || !containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      // Drag X is measured from the container's right edge so dragging
      // left grows the right pane, matching the visual affordance.
      const candidate = rect.right - event.clientX;
      const containerWidth = rect.width;
      const cap = Math.max(
        minRightWidth,
        Math.min(
          maxRightWidth,
          containerWidth - minLeftWidth - DEFAULT_HANDLE_WIDTH,
        ),
      );
      const clamped = Math.max(minRightWidth, Math.min(cap, candidate));
      setRightWidth(clamped);
    },
    [isDragging, minLeftWidth, minRightWidth, maxRightWidth],
  );

  const onPointerUp = useCallback(
    (event) => {
      if (!isDragging) return;
      event.target.releasePointerCapture?.(event.pointerId);
      setIsDragging(false);
      persist(rightWidth);
    },
    [isDragging, persist, rightWidth],
  );

  // Keyboard nudges for accessibility — arrow keys when the handle is focused.
  const onKeyDown = useCallback(
    (event) => {
      const step = event.shiftKey ? 40 : 12;
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        setRightWidth((prev) => {
          const next = Math.min(maxRightWidth, prev + step);
          persist(next);
          return next;
        });
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        setRightWidth((prev) => {
          const next = Math.max(minRightWidth, prev - step);
          persist(next);
          return next;
        });
      }
    },
    [maxRightWidth, minRightWidth, persist],
  );

  return (
    <Box
      ref={containerRef}
      sx={{
        display: "flex",
        flex: 1,
        overflow: "hidden",
        minWidth: 0,
        flexDirection: { xs: "column", md: "row" },
        // Block native text selection while dragging — otherwise the
        // pointer sweep selects content as it crosses the page.
        userSelect: isDragging ? "none" : "auto",
        cursor: isDragging ? "col-resize" : "default",
      }}
    >
      <Box
        sx={{
          flex: { xs: "1 1 auto", md: 1 },
          minWidth: 0,
          overflow: "auto",
          borderBottom: { xs: 1, md: 0 },
          borderColor: "divider",
        }}
      >
        {left}
      </Box>

      {/* Splitter — visible 1px line centered in a 6px hit area. The
          hit area is wider so the handle is easy to grab without
          drawing a chunky vertical bar across the page. */}
      <Box
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize panels"
        tabIndex={0}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onMouseEnter={() => setHoverHandle(true)}
        onMouseLeave={() => setHoverHandle(false)}
        onKeyDown={onKeyDown}
        sx={{
          display: { xs: "none", md: "flex" },
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
          width: DEFAULT_HANDLE_WIDTH,
          cursor: "col-resize",
          position: "relative",
          touchAction: "none",
          "&:focus-visible": { outline: "none" },
          "&:focus-visible::before": {
            outline: (theme) => `2px solid ${theme.palette.primary.main}`,
            outlineOffset: 1,
          },
          // Visible line — a 1px column that brightens on hover/drag.
          "&::before": {
            content: '""',
            display: "block",
            width: 1,
            height: "100%",
            bgcolor: (theme) =>
              isDragging || hoverHandle
                ? theme.palette.primary.main
                : theme.palette.divider,
            transition: "background-color 120ms ease",
          },
          // Soft glow around the handle on hover so it's discoverable.
          "&:hover::after": {
            content: '""',
            position: "absolute",
            top: 0,
            bottom: 0,
            left: 0,
            right: 0,
            bgcolor: (theme) => alpha(theme.palette.primary.main, 0.08),
          },
        }}
      />

      <Box
        sx={{
          flex: { xs: "1 1 auto", md: "0 0 auto" },
          width: { xs: "100%", md: rightWidth },
          minWidth: 0,
          overflow: "auto",
          bgcolor: rightBackground,
        }}
      >
        {right}
      </Box>
    </Box>
  );
}

ResizableSplit.propTypes = {
  left: PropTypes.node.isRequired,
  right: PropTypes.node.isRequired,
  initialRightWidth: PropTypes.number,
  minLeftWidth: PropTypes.number,
  minRightWidth: PropTypes.number,
  maxRightWidth: PropTypes.number,
  storageKey: PropTypes.string,
  rightBackground: PropTypes.string,
};
