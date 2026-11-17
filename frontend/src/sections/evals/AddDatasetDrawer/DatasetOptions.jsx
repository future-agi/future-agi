import { Box, Chip, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";

// A disabled option is an import path not built in this release: it stays
// visible and dimmed with the shared line, and clicking it does nothing.
const DatasetOptions = ({ title, subTitle, onClick, disabled }) => {
  return (
    <Box
      sx={{
        paddingY: 2,
        paddingX: "24px",
        gap: 1,
        display: "flex",
        flexDirection: "column",
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
        cursor: disabled ? "default" : "pointer",
        opacity: disabled ? 0.5 : 1,
      }}
      aria-disabled={disabled || undefined}
      onClick={disabled ? undefined : onClick}
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
        <Typography variant="body2" fontWeight={500} color="text.primary">
          {title}
        </Typography>
        {disabled && (
          <Chip variant="outlined" label={NOT_IN_RELEASE} size="small" />
        )}
      </Box>
      <Typography variant="caption" color="text.secondary">
        {subTitle}
      </Typography>
    </Box>
  );
};

DatasetOptions.propTypes = {
  title: PropTypes.string,
  subTitle: PropTypes.string,
  onClick: PropTypes.func,
  disabled: PropTypes.bool,
};

export default DatasetOptions;
