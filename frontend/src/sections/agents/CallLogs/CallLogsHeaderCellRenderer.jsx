import React from "react";
import { Box, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";

const CallLogsHeaderCellRenderer = ({ displayName }) => {
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 0.5,
        overflow: "hidden",
      }}
    >
      <Iconify
        icon="solar:check-circle-linear"
        color="status.success.fg"
        width={16}
        height={16}
        style={{ flexShrink: 0 }}
      />
      <Typography
        variant="subtitle2"
        sx={{
          fontWeight: 500,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {displayName}
      </Typography>
    </Box>
  );
};

CallLogsHeaderCellRenderer.propTypes = {
  displayName: PropTypes.string,
};

export default CallLogsHeaderCellRenderer;
