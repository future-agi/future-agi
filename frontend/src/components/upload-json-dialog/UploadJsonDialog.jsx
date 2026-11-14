import React, { useCallback, useState, useEffect } from "react";
import PropTypes from "prop-types";
import { useDropzone } from "react-dropzone";
import {
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogContent,
  DialogTitle,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import SvgColor from "src/components/svg-color";

// Status constants for dialog states
const STATUS = {
  IDLE: "idle",
  LOADING: "loading",
  ERROR: "error",
};

export default function UploadJsonDialog({
  open,
  onClose,
  onFileSelect,
  onSuccessComplete,
  isLoading = false,
  isSuccess = false,
  error = null,
  title = "Upload JSON",
  accept = { "application/json": [".json"] },
  dropzoneText = "Drag and drop the file",
  dropActiveText = "Drop the file here",
  buttonText = "Add from files",
  loadingText,
  multiple = false,
  disabled = false,
}) {
  const [selectedFile, setSelectedFile] = useState(null);
  const [localError, setLocalError] = useState(null);

  // Handle success - immediately close dialog and notify parent
  useEffect(() => {
    if (isSuccess && selectedFile) {
      onSuccessComplete?.(selectedFile);
      onClose();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isSuccess]);

  // Reset local state when dialog opens
  useEffect(() => {
    if (open) {
      setLocalError(null);
      setSelectedFile(null);
    }
  }, [open]);

  const handleClose = () => {
    if (isLoading) return; // Prevent close while loading
    setSelectedFile(null);
    setLocalError(null);
    onClose();
  };

  const onDrop = useCallback(
    (acceptedFiles) => {
      setLocalError(null); // Clear any previous local errors
      const file = multiple ? acceptedFiles : acceptedFiles[0];
      if (file) {
        setSelectedFile(multiple ? acceptedFiles : file);
        if (onFileSelect) {
          onFileSelect(file, acceptedFiles);
        }
      }
    },
    [onFileSelect, multiple],
  );

  const onDropRejected = useCallback((rejectedFiles) => {
    if (rejectedFiles.length > 0) {
      const rejection = rejectedFiles[0];
      const errorCode = rejection.errors?.[0]?.code;

      let errorMessage;
      switch (errorCode) {
        case "file-invalid-type":
          errorMessage = "Invalid file type. Please upload a JSON file.";
          break;
        case "file-too-large":
          errorMessage = "File is too large.";
          break;
        case "file-too-small":
          errorMessage = "File is too small.";
          break;
        case "too-many-files":
          errorMessage = "Too many files. Please upload only one file.";
          break;
        default:
          errorMessage = "Invalid file. Please try again.";
      }
      setLocalError(errorMessage);
    }
  }, []);

  const { getRootProps, getInputProps, isDragActive, isDragReject } =
    useDropzone({
      onDrop,
      onDropRejected,
      accept,
      multiple,
      disabled: isLoading || disabled,
    });

  const getLoadingText = () => {
    if (loadingText) return loadingText;
    if (selectedFile?.name) return `Uploading ${selectedFile.name}...`;
    return "Uploading...";
  };

  // Determine current status
  const getStatus = () => {
    if (isLoading) return STATUS.LOADING;
    if (error || localError) return STATUS.ERROR;
    return STATUS.IDLE;
  };

  const status = getStatus();
  const displayError = error || localError;

  // Get border color based on state
  const getBorderColor = () => {
    if (isDragReject) return "error.main";
    if (isDragActive) return "primary.main";
    if (displayError) return "error.light";
    return "status.info.border";
  };

  // Get background color based on state
  const getBgColor = () => {
    if (isDragReject) return "error.lighter";
    return "status.info.bg";
  };

  const renderContent = () => {
    if (status === STATUS.LOADING) {
      return (
        <Stack alignItems="center" spacing={2}>
          <CircularProgress size={32} />
          <Typography
            typography="s1"
            fontWeight="fontWeightRegular"
            color="text.primary"
          >
            {getLoadingText()}
          </Typography>
        </Stack>
      );
    }

    return (
      <Stack alignItems="center" spacing={1.5}>
        <Stack direction="row" gap={1}>
          <SvgColor
            src="/assets/icons/action_buttons/ic_download.svg"
            sx={{
              width: 20,
              height: 20,
              bgcolor: isDragReject ? "error.main" : "text.primary",
            }}
          />
          <Typography
            typography="s2_1"
            fontWeight="fontWeightMedium"
            color={isDragReject ? "error.main" : "text.primary"}
          >
            {isDragReject
              ? "Invalid file type"
              : isDragActive
                ? dropActiveText
                : dropzoneText}
          </Typography>
        </Stack>
        <Button
          variant="outlined"
          size="small"
          disabled={disabled}
          color="primary"
        >
          {buttonText}
        </Button>
      </Stack>
    );
  };

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth="sm"
      fullWidth
      PaperProps={{
        sx: {
          borderRadius: 1,
          maxWidth: 560,
        },
      }}
    >
      <DialogTitle sx={{ p: 2 }}>
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
        >
          <Typography typography="m3" fontWeight={600} color="text.primary">
            {title}
          </Typography>
          <IconButton
            onClick={handleClose}
            size="small"
            disabled={isLoading}
            sx={{
              color: "text.primary",
            }}
          >
            <SvgColor
              src="/assets/icons/ic_close.svg"
              sx={{ height: 24, width: 24 }}
            />
          </IconButton>
        </Stack>
      </DialogTitle>

      <DialogContent sx={{ p: 2, pt: 1 }}>
        <Box
          {...getRootProps()}
          sx={{
            border: "2px dashed",
            borderColor: getBorderColor(),
            borderRadius: 1,
            bgcolor: getBgColor(),
            p: 4,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            cursor: isLoading || disabled ? "default" : "pointer",
            minHeight: 160,
            transition: "all 0.2s ease-in-out",
            "&:hover": {
              borderColor:
                isLoading || disabled ? getBorderColor() : "primary.main",
              bgcolor: isLoading || disabled ? getBgColor() : "status.info.bg",
            },
          }}
        >
          <input {...getInputProps()} />
          {renderContent()}
        </Box>

        {displayError && (
          <Typography
            typography="s2"
            color="error.main"
            sx={{ mt: 1.5, textAlign: "center" }}
          >
            {displayError}
          </Typography>
        )}
      </DialogContent>
    </Dialog>
  );
}

UploadJsonDialog.propTypes = {
  /** Whether the dialog is open */
  open: PropTypes.bool.isRequired,
  /** Callback when dialog is closed */
  onClose: PropTypes.func.isRequired,
  /** Callback when file is selected/dropped - receives (file, allFiles) */
  onFileSelect: PropTypes.func,
  /** Callback fired on success with the selected file - receives (file) */
  onSuccessComplete: PropTypes.func,
  /** Loading state - disables interactions and shows loading UI */
  isLoading: PropTypes.bool,
  /** Success state - triggers onSuccessComplete and closes dialog */
  isSuccess: PropTypes.bool,
  /** Error message to display */
  error: PropTypes.string,
  /** Dialog title */
  title: PropTypes.string,
  /** Accepted file types for dropzone */
  accept: PropTypes.object,
  /** Text shown in dropzone */
  dropzoneText: PropTypes.string,
  /** Text shown when file is being dragged over */
  dropActiveText: PropTypes.string,
  /** Text for the file select button */
  buttonText: PropTypes.string,
  /** Custom loading text (defaults to "Uploading {filename}...") */
  loadingText: PropTypes.string,
  /** Allow multiple file selection */
  multiple: PropTypes.bool,
  /** Disable the dropzone */
  disabled: PropTypes.bool,
};
