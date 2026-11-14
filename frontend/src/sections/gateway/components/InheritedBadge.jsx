/* eslint-disable react/prop-types */
import React from "react";
import { Chip } from "@mui/material";

const InheritedBadge = ({ variant = "inherited" }) => {
  if (variant === "custom") {
    return (
      <Chip label="Custom" size="small" variant="outlined" color="primary" />
    );
  }

  return (
    <Chip
      label="Inherited"
      size="small"
      variant="outlined"
      sx={{
        color: "text.secondary",
        borderColor: "divider",
      }}
    />
  );
};

export default InheritedBadge;
