import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import CustomTooltip from "src/components/tooltip";

/** A compact header for columns whose values are mostly empty. */
const EmptyColumnHeader = ({ displayName, col, onExpand }) => {
  const emptyCount = Number(col?.stats?.empty_count) || 0;
  const totalCount = Number(col?.stats?.total) || 0;
  const title = `${displayName} · ${emptyCount} of ${totalCount} empty · click to expand`;

  return (
    <CustomTooltip show title={title} placement="top" size="small" arrow>
      <Box
        role="button"
        tabIndex={0}
        aria-label={title}
        onClick={(event) => {
          event.stopPropagation();
          onExpand?.();
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            event.stopPropagation();
            onExpand?.();
          }
        }}
        sx={{
          width: "100%",
          minWidth: 0,
          height: "100%",
          display: "flex",
          flexDirection: "column",
          alignItems: "flex-start",
          justifyContent: "center",
          gap: 0.25,
          px: 1,
          cursor: "pointer",
          overflow: "hidden",
          "&:hover": { bgcolor: "action.hover" },
        }}
      >
        <Typography
          component="span"
          typography="s2"
          sx={{
            fontWeight: 500,
            color: "text.primary",
            maxWidth: "100%",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {displayName}
        </Typography>
        <Typography
          component="span"
          typography="s3"
          color="text.secondary"
          sx={{ whiteSpace: "nowrap" }}
        >
          {`${emptyCount} of ${totalCount} empty`}
        </Typography>
      </Box>
    </CustomTooltip>
  );
};

EmptyColumnHeader.propTypes = {
  displayName: PropTypes.string,
  col: PropTypes.object,
  onExpand: PropTypes.func,
};

export default EmptyColumnHeader;
