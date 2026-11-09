import React, {
  forwardRef,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";

export const LANE_MIN = 240;
export const GUTTER = 20;

/** CSS grid columns: lanes equal width (min 280), a gutter before each
 * non-baseline lane when `gutters`. */
export function laneTemplate(n, gutters = false) {
  const lane = `minmax(${LANE_MIN}px, 1fr)`;
  if (!gutters) return `repeat(${n}, ${lane})`;
  return [
    lane,
    ...Array.from({ length: Math.max(0, n - 1) }, () => `${GUTTER}px ${lane}`),
  ].join(" ");
}
export const gridMinWidth = (n, gutters = false) =>
  n * LANE_MIN + (gutters ? (n - 1) * GUTTER : 0);

/**
 * The lanes' scroll container: one vertical scroll for every lane (a row is a
 * row), sticky lane headers, and — when the lanes don't fit — horizontal
 * scroll inside this container with edge shadows and `Showing 3 of 4 lanes`.
 */
const LanesGrid = forwardRef(function LanesGrid(
  { header, children, laneCount, gutters, toolbar, footer, sx },
  ref,
) {
  const inner = useRef(null);
  const [edges, setEdges] = useState({
    left: false,
    right: false,
    visible: laneCount,
  });
  const measure = useCallback(() => {
    const el = inner.current;
    if (!el) return;
    const left = el.scrollLeft > 2;
    const right = el.scrollLeft + el.clientWidth < el.scrollWidth - 2;
    const per = el.scrollWidth / Math.max(1, laneCount);
    const visible = Math.max(
      1,
      Math.min(laneCount, Math.floor((el.clientWidth + 8) / per)),
    );
    setEdges((e) =>
      e.left === left && e.right === right && e.visible === visible
        ? e
        : { left, right, visible },
    );
  }, [laneCount]);
  useEffect(() => {
    measure();
    const el = inner.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [measure]);
  const setRef = (el) => {
    inner.current = el;
    if (typeof ref === "function") ref(el);
    else if (ref) ref.current = el;
  };
  const shadow = (side) => ({
    position: "absolute",
    top: 0,
    bottom: 0,
    [side]: 0,
    width: 14,
    pointerEvents: "none",
    zIndex: 4,
    background: (t) =>
      `linear-gradient(to ${side === "left" ? "right" : "left"}, ${t.palette.mode === "dark" ? "rgba(0,0,0,0.45)" : "rgba(0,0,0,0.12)"}, transparent)`,
  });
  return (
    <Box
      sx={{
        position: "relative",
        flex: 1,
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
        ...sx,
      }}
    >
      {toolbar}
      <Box sx={{ position: "relative", flex: 1, minHeight: 0 }}>
        {edges.left ? <Box sx={shadow("left")} /> : null}
        {edges.right ? <Box sx={shadow("right")} /> : null}
        <Box
          ref={setRef}
          onScroll={measure}
          data-lanes-scroller
          sx={{
            position: "absolute",
            inset: 0,
            overflow: "auto",
            overscrollBehavior: "contain",
          }}
        >
          {/* IT-R2 G4 P8: a 16 px right gutter (the last lane's text and
              header never touch the window edge). */}
          <Box sx={{ minWidth: gridMinWidth(laneCount, gutters), pr: 2 }}>
            <Box
              sx={{
                position: "sticky",
                top: 0,
                zIndex: 3,
                display: "grid",
                gridTemplateColumns: laneTemplate(laneCount, gutters),
                borderBottom: "1px solid",
                borderColor: "divider",
                bgcolor: "background.paper",
              }}
            >
              {header}
            </Box>
            {children}
          </Box>
        </Box>
        {edges.visible < laneCount ? (
          <Typography
            typography="s3"
            sx={{
              position: "absolute",
              right: 12,
              bottom: 8,
              zIndex: 5,
              px: 1,
              py: 0.25,
              borderRadius: 1,
              bgcolor: "background.paper",
              border: "1px solid",
              borderColor: "divider",
              color: "text.secondary",
              boxShadow: 1,
            }}
          >
            Showing {edges.visible} of {laneCount} lanes · scroll sideways
          </Typography>
        ) : null}
      </Box>
      {footer}
    </Box>
  );
});

LanesGrid.propTypes = {
  header: PropTypes.node,
  children: PropTypes.node,
  laneCount: PropTypes.number.isRequired,
  gutters: PropTypes.bool,
  toolbar: PropTypes.node,
  footer: PropTypes.node,
  sx: PropTypes.object,
};

export default LanesGrid;
