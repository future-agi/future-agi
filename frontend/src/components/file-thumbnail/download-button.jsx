import React from "react";
import PropTypes from "prop-types";

import { useTheme } from "@mui/material/styles";
import IconButton from "@mui/material/IconButton";

import { bgBlur } from "src/theme/css";

import Iconify from "../iconify";

// ----------------------------------------------------------------------

export default function DownloadButton({ onDownload }) {
  const theme = useTheme();

  return (
    <IconButton
      size="small"
      onClick={onDownload}
      sx={{
        p: 0,
        top: 0,
        right: 0,
        width: 1,
        zIndex: 9,
        opacity: 0,
        position: "absolute",
        justifyContent: "center",
        bgcolor: "text.primary",
        color: "common.white",
        transition: theme.transitions.create(["opacity"]),

        "&:hover": {
          opacity: 1,
          ...bgBlur({
            opacity: 0.64,
            color: theme.palette.text.primary,
          }),
        },
      }}
    >
      <Iconify icon="solar:round-arrow-down-linear" width={24} />
    </IconButton>
  );
}

DownloadButton.propTypes = {
  onDownload: PropTypes.func,
};
