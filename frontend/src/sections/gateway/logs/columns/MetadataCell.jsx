import React, { useLayoutEffect, useRef, useState } from "react";
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

/**
 * Generic cell for a declared custom-property column. Reads exactly
 * `metadata[name]` and renders plain text only. Truncated values, and
 * shorter ones the bounded cell clips, are focusable so the tooltip opens on
 * focus as well as hover, and the Tooltip's string title gives them their
 * accessible name.
 */
const MetadataCell = React.memo(function MetadataCell({ metadata, name }) {
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

  return (
    <TableCell data-column={toCustomColumnId(name)}>
      {showsFull ? (
        <Tooltip title={full} placement="top" slotProps={TOOLTIP_SLOT_PROPS}>
          {content}
        </Tooltip>
      ) : (
        content
      )}
    </TableCell>
  );
});

MetadataCell.propTypes = {
  metadata: PropTypes.any,
  name: PropTypes.string.isRequired,
};

export default MetadataCell;
