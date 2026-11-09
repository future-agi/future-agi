import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";

const DatasetOptions = ({ title, subTitle, onClick, disabled }) => {
  if (disabled) return null;
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
        cursor: "pointer",
      }}
      onClick={onClick}
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
        <Typography variant="body2" fontWeight={500} color="text.primary">
          {title}
        </Typography>
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
