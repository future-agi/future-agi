import { IconButton } from "@mui/material";
import React from "react";
import SvgColor from "src/components/svg-color";

export const ExperimentActionsCellRenderer = () => {
  return (
    <IconButton
      className="row-hover-action"
      type="button"
      size="small"
      variant="contained"
      color="primary"
    >
      <SvgColor
        src="/assets/icons/ic_pen.svg"
        sx={{
          width: 16,
          height: 16,
          color: "text.primary",
        }}
      />
    </IconButton>
  );
};
