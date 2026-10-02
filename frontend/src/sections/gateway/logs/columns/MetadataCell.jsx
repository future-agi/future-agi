import React from "react";
import PropTypes from "prop-types";
import { TableCell, Tooltip, Typography } from "@mui/material";
import { formatMetadataValue, readMetadataValue } from "./formatMetadataValue";

/**
 * Generic cell for a declared custom-property column. Reads exactly
 * `metadata[name]` and renders plain text only (R17-R24). Truncated values are
 * focusable so the tooltip opens on focus as well as hover (R41).
 */
const MetadataCell = React.memo(function MetadataCell({ metadata, name }) {
  const { text, full, truncated } = formatMetadataValue(
    readMetadataValue(metadata, name),
  );

  const content = (
    <Typography
      variant="body2"
      noWrap
      component="span"
      tabIndex={truncated ? 0 : undefined}
      aria-label={truncated ? full : undefined}
      sx={{ display: "block" }}
    >
      {text}
    </Typography>
  );

  return (
    <TableCell data-column={`metadata:${name}`}>
      {truncated ? (
        <Tooltip title={full} placement="top" arrow>
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
