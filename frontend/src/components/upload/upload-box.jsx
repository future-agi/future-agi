import React from "react";
import PropTypes from "prop-types";
import { useDropzone } from "react-dropzone";

import Box from "@mui/material/Box";
import { alpha } from "@mui/material/styles";

import Iconify from "../iconify";

// ----------------------------------------------------------------------

export default function UploadBox({
  placeholder,
  error,
  disabled,
  sx,
  ...other
}) {
  const { getRootProps, getInputProps, isDragActive, isDragReject } =
    useDropzone({
      disabled,
      ...other,
    });

  const hasError = isDragReject || error;

  return (
    <Box
      {...getRootProps()}
      sx={{
        m: 0.5,
        width: 64,
        height: 64,
        flexShrink: 0,
        display: "flex",
        borderRadius: 1,
        cursor: "pointer",
        alignItems: "center",
        color: "text.disabled",
        justifyContent: "center",
        bgcolor: (theme) => alpha(theme.palette.text.disabled, 0.08),
        border: (theme) =>
          `dashed 1px ${alpha(theme.palette.text.disabled, 0.16)}`,
        ...(isDragActive && {
          opacity: 0.72,
        }),
        ...(disabled && {
          opacity: 0.48,
          pointerEvents: "none",
        }),
        ...(hasError && {
          color: "error.main",
          borderColor: "error.main",
          bgcolor: (theme) => alpha(theme.palette.error.main, 0.08),
        }),
        "&:hover": {
          opacity: 0.72,
        },
        ...sx,
      }}
    >
      <input {...getInputProps()} />

      {placeholder || <Iconify icon="solar:cloud-upload-linear" width={28} />}
    </Box>
  );
}

UploadBox.propTypes = {
  disabled: PropTypes.object,
  error: PropTypes.bool,
  placeholder: PropTypes.object,
  sx: PropTypes.object,
};
