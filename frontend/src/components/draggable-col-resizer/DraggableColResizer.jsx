import { Box } from "@mui/material";
import PropTypes from "prop-types";
import { useCallback, useEffect, useRef } from "react";

// Thin drag handle rendered between two columns in a flex row. Parent
// owns the column width state; we notify on drag via `onResize(next)`.
// Min/max clamp lives in parent so each callsite can cap to its layout.
const DraggableColResizer = ({
  onResize,
  onResizeStart,
  onResizeEnd,
  getCurrentWidth,
  minWidth = 80,
  maxWidth = 800,
  height = 24,
  sx,
}) => {
  const dragStateRef = useRef(null);

  const handleMouseMove = useCallback(
    (e) => {
      const state = dragStateRef.current;
      if (!state) return;
      const delta = e.clientX - state.startX;
      const next = Math.max(
        minWidth,
        Math.min(maxWidth, state.startWidth + delta),
      );
      onResize(next);
    },
    [maxWidth, minWidth, onResize],
  );

  const handleMouseUp = useCallback(() => {
    dragStateRef.current = null;
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    window.removeEventListener("mousemove", handleMouseMove);
    window.removeEventListener("mouseup", handleMouseUp);
    onResizeEnd?.();
  }, [handleMouseMove, onResizeEnd]);

  const handleMouseDown = useCallback(
    (e) => {
      e.preventDefault();
      e.stopPropagation();
      dragStateRef.current = {
        startX: e.clientX,
        startWidth: getCurrentWidth(),
      };
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
      window.addEventListener("mousemove", handleMouseMove);
      window.addEventListener("mouseup", handleMouseUp);
      onResizeStart?.();
    },
    [getCurrentWidth, handleMouseMove, handleMouseUp, onResizeStart],
  );

  useEffect(
    () => () => {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    },
    [handleMouseMove, handleMouseUp],
  );

  return (
    <Box
      onMouseDown={handleMouseDown}
      sx={[
        {
          width: "4px",
          height,
          flexShrink: 0,
          cursor: "col-resize",
          mx: 0.25,
          borderRadius: 0.5,
          transition: "background-color 120ms",
          "&:hover": { backgroundColor: "primary.main" },
          "&:active": { backgroundColor: "primary.dark" },
        },
        ...(Array.isArray(sx) ? sx : [sx]),
      ]}
    />
  );
};

DraggableColResizer.propTypes = {
  onResize: PropTypes.func.isRequired,
  onResizeStart: PropTypes.func,
  onResizeEnd: PropTypes.func,
  getCurrentWidth: PropTypes.func.isRequired,
  minWidth: PropTypes.number,
  maxWidth: PropTypes.number,
  height: PropTypes.number,
  sx: PropTypes.oneOfType([PropTypes.object, PropTypes.array, PropTypes.func]),
};

export default DraggableColResizer;
