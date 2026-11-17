import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { TableCell, Tooltip, Typography } from "@mui/material";
import { CUSTOM_COLUMN_TEXT_WIDTH, toCustomColumnId } from "./columnModel";
import { formatMetadataValue, readMetadataValue } from "./formatMetadataValue";

// 720px fits 2,000 characters of typical text within 40vh on viewports 900px
// or taller. Anything taller scrolls inside the tooltip, so the tooltip always
// fits on screen; a capped value's truncation marker leads it, so stays visible.
const TOOLTIP_SLOT_PROPS = {
  tooltip: {
    sx: {
      maxWidth: 720,
      maxHeight: "40vh",
      overflowY: "auto",
      overflowWrap: "anywhere",
    },
  },
};

// Keyboard focus on the cell or inside it, as the browser shows a focus
// ring for it (:focus-visible): a click that focuses the cell is not one.
function hasKeyboardFocus(cell) {
  try {
    return (
      cell.matches(":focus-visible") ||
      Boolean(cell.querySelector(":focus-visible"))
    );
  } catch {
    return cell.contains(document.activeElement);
  }
}

/**
 * True while `cell` has keyboard focus. The logs grid moves the keyboard
 * from cell to cell and focuses the cell itself, never the value inside it,
 * so a value there follows its cell's keyboard focus, as a focusable value
 * does with MUI's Tooltip: a click that focuses the cell opens nothing, and
 * Escape closes it until the cell is focused again.
 */
function useCellFocus(cell) {
  const [focused, setFocused] = useState(false);
  useEffect(() => {
    if (!cell) return undefined;
    const onFocusIn = () => setFocused(hasKeyboardFocus(cell));
    const onFocusOut = (event) => {
      if (!cell.contains(event.relatedTarget)) setFocused(false);
    };
    const onKeyDown = (event) => {
      if (event.key === "Escape") setFocused(false);
    };
    cell.addEventListener("focusin", onFocusIn);
    cell.addEventListener("focusout", onFocusOut);
    cell.addEventListener("keydown", onKeyDown);
    setFocused(cell.contains(document.activeElement) && hasKeyboardFocus(cell));
    return () => {
      cell.removeEventListener("focusin", onFocusIn);
      cell.removeEventListener("focusout", onFocusOut);
      cell.removeEventListener("keydown", onKeyDown);
    };
  }, [cell]);
  return focused;
}

/**
 * A custom-property value as bounded plain text; the full value shows in a
 * tooltip on hover and focus when it is clipped or truncated. Used bare in the
 * logs grid, where `cell` is its grid cell and the tooltip also shows while
 * that cell has keyboard focus, and inside MetadataCell's table cell.
 */
export const MetadataValue = React.memo(function MetadataValue({
  metadata,
  name,
  cell,
}) {
  const { text, full, truncated } = formatMetadataValue(
    readMetadataValue(metadata, name),
  );
  const textRef = useRef(null);
  const [isClipped, setIsClipped] = useState(false);

  // Clipping depends only on the text and the max width, so one measurement
  // per text is enough.
  useLayoutEffect(() => {
    const node = textRef.current;
    setIsClipped(node.scrollWidth > node.clientWidth);
  }, [text]);

  const showsFull = truncated || isClipped;
  const cellFocused = useCellFocus(cell);
  const [hoverOpen, setHoverOpen] = useState(false);
  const content = (
    <Typography
      ref={textRef}
      variant="body2"
      noWrap
      component="span"
      tabIndex={showsFull ? 0 : undefined}
      sx={{ display: "block", maxWidth: CUSTOM_COLUMN_TEXT_WIDTH }}
    >
      {text}
    </Typography>
  );

  if (!showsFull) return content;
  return cell ? (
    <Tooltip
      title={full}
      placement="top"
      slotProps={TOOLTIP_SLOT_PROPS}
      open={hoverOpen || cellFocused}
      onOpen={() => setHoverOpen(true)}
      onClose={() => setHoverOpen(false)}
    >
      {content}
    </Tooltip>
  ) : (
    <Tooltip title={full} placement="top" slotProps={TOOLTIP_SLOT_PROPS}>
      {content}
    </Tooltip>
  );
});

MetadataValue.propTypes = {
  metadata: PropTypes.any,
  name: PropTypes.string.isRequired,
  cell: PropTypes.object,
};

/**
 * Generic cell for a declared custom-property column. Reads exactly
 * `metadata[name]` and renders plain text only. Truncated values, and
 * shorter ones the bounded cell clips, are focusable so the tooltip opens on
 * focus as well as hover, and the Tooltip's string title gives them their
 * accessible name.
 */
const MetadataCell = React.memo(function MetadataCell({ metadata, name }) {
  return (
    <TableCell data-column={toCustomColumnId(name)}>
      <MetadataValue metadata={metadata} name={name} />
    </TableCell>
  );
});

MetadataCell.propTypes = {
  metadata: PropTypes.any,
  name: PropTypes.string.isRequired,
};

export default MetadataCell;
