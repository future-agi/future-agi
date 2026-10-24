import React from "react";
import PropTypes from "prop-types";
import { useDropzone } from "react-dropzone";

import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Button from "@mui/material/Button";
import { alpha } from "@mui/material/styles";
import IconButton from "@mui/material/IconButton";
import Typography from "@mui/material/Typography";

import { UploadIllustration } from "src/assets/illustrations";

import Iconify from "../iconify";
import MultiFilePreview from "./preview-multi-file";
import RejectionFiles from "./errors-rejection-files";
import SingleFilePreview from "./preview-single-file";

// ----------------------------------------------------------------------

export default function Upload({
  showIcon,
  disabled,
  multiple = false,
  error,
  helperText,
  //
  file,
  onDelete,
  //
  files,
  thumbnail,
  onUpload,
  onRemove,
  onRemoveAll,
  sx,
  showIllustration = true,
  heading,
  description,
  uploadIcon,
  actionButton,
  showDropRejection = true,
  hidePreview = false,
  ...other
}) {
  const {
    getRootProps,
    getInputProps,
    isDragActive,
    isDragReject,
    fileRejections,
  } = useDropzone({
    multiple,
    disabled,
    ...other,
  });

  const hasFile = !!file && !multiple;

  const hasFiles = !!files && multiple && !!files.length;

  const hasError = isDragReject || !!error;

  const renderPlaceholder = (
    <Stack
      spacing={2}
      alignItems="center"
      justifyContent="center"
      flexWrap="wrap"
    >
      {showIcon && (
        <Iconify
          color="secondary.main"
          icon="solar:download-minimalistic-bold"
          height={24}
          width={24}
        />
      )}
      {showIllustration && (
        <UploadIllustration sx={{ width: 1, maxWidth: 200 }} />
      )}
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: "16px",
          alignItems: "center",
        }}
      >
        {uploadIcon && uploadIcon}
        <Stack spacing={1} sx={{ textAlign: "center" }}>
          <Typography
            fontWeight={"fontWeightMedium"}
            color="text.primary"
            variant="m3"
          >
            {heading || "Drop or Select file"}
          </Typography>
          <Typography
            variant="s2"
            fontWeight={"fontWeightRegular"}
            color="text.primary"
          >
            {!description ? (
              <React.Fragment>
                Drop files here or click
                <Box
                  component="span"
                  sx={{
                    mx: 0.5,
                    color: "primary.main",
                    textDecoration: "underline",
                    fontWeight: "600",
                  }}
                >
                  browse
                </Box>
                through your machine
              </React.Fragment>
            ) : typeof description === "string" ? (
              description
            ) : (
              <Stack spacing={0}>
                {description?.map((item, index) => (
                  <Typography
                    variant="s2"
                    fontWeight={"fontWeightRegular"}
                    color="text.primary"
                    key={index}
                  >
                    {item}
                  </Typography>
                ))}
              </Stack>
            )}
          </Typography>
        </Stack>
        {actionButton && actionButton}
      </Box>
    </Stack>
  );

  const renderSinglePreview = (
    <SingleFilePreview
      imgUrl={typeof file === "string" ? file : file?.preview}
    />
  );

  const removeSinglePreview = hasFile && onDelete && (
    <IconButton
      size="small"
      onClick={onDelete}
      sx={{
        top: 16,
        right: 16,
        zIndex: 9,
        position: "absolute",
        color: (theme) => alpha(theme.palette.common.white, 0.8),
        bgcolor: (theme) => alpha(theme.palette.text.primary, 0.72),
        "&:hover": {
          bgcolor: (theme) => alpha(theme.palette.text.primary, 0.48),
        },
      }}
    >
      <Iconify icon="solar:close-linear" width={20} />
    </IconButton>
  );

  const renderMultiPreview = hasFiles && !hidePreview && (
    <>
      <Box sx={{ my: 3 }}>
        <MultiFilePreview
          files={files}
          thumbnail={thumbnail}
          onRemove={onRemove}
        />
      </Box>

      <Stack direction="row" justifyContent="flex-end" spacing={1.5}>
        {onRemoveAll && (
          <Button
            color="inherit"
            variant="outlined"
            size="small"
            onClick={onRemoveAll}
          >
            Remove All
          </Button>
        )}

        {onUpload && (
          <Button
            size="small"
            variant="contained"
            onClick={onUpload}
            startIcon={<Iconify icon="solar:cloud-upload-linear" />}
          >
            Upload
          </Button>
        )}
      </Stack>
    </>
  );

  return (
    <>
      <Box
        {...getRootProps()}
        sx={{
          paddingX: 5,
          paddingY: 4,
          outline: "none",
          borderRadius: 1,
          cursor: "pointer",
          overflow: "hidden",
          position: "relative",
          bgcolor: (theme) => theme.palette.action.hover,
          border: (theme) =>
            `1px dashed ${alpha(theme.palette.primary.main, 0.3)}`,
          transition: (theme) =>
            theme.transitions.create([
              "opacity",
              "padding",
              "border-color",
              "background-color",
            ]),
          "&:hover": {
            bgcolor: (theme) => alpha(theme.palette.primary.main, 0.08),
            borderColor: (theme) => alpha(theme.palette.primary.main, 0.5),
            // boxShadow: (theme) => `0 8px 25px ${alpha(theme.palette.primary.main, 0.15)}`,
          },
          ...(isDragActive && {
            bgcolor: (theme) => alpha(theme.palette.primary.main, 0.12),
            borderColor: (theme) => theme.palette.primary.main,
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
          ...(hasFile && {
            padding: "24% 0",
          }),
          ...sx,
        }}
      >
        <input {...getInputProps()} />

        {hasFile ? renderSinglePreview : renderPlaceholder}
      </Box>

      {removeSinglePreview}

      {helperText && helperText}

      {showDropRejection && <RejectionFiles fileRejections={fileRejections} />}

      {renderMultiPreview}
    </>
  );
}

Upload.propTypes = {
  showIcon: PropTypes.bool,
  disabled: PropTypes.object,
  error: PropTypes.bool,
  file: PropTypes.oneOfType([PropTypes.string, PropTypes.object]),
  files: PropTypes.array,
  helperText: PropTypes.object,
  multiple: PropTypes.bool,
  onDelete: PropTypes.func,
  onRemove: PropTypes.func,
  onRemoveAll: PropTypes.func,
  onUpload: PropTypes.func,
  sx: PropTypes.object,
  thumbnail: PropTypes.bool,
  uploadIcon: PropTypes.any,
  heading: PropTypes.string,
  description: PropTypes.oneOfType([PropTypes.string, PropTypes.array]),
  actionButton: PropTypes.any,
  showIllustration: PropTypes.bool,
  showDropRejection: PropTypes.bool,
  hidePreview: PropTypes.bool,
};
