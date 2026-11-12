import PropTypes from "prop-types";
import React, { useRef, useState } from "react";
import { Box, LinearProgress, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { fData } from "src/utils/format-number";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import {
  prepareSourceFolder,
  uploadErrorText,
  uploadSourceFolder,
} from "src/pages/dashboard/harness/sourceUpload";
import { FolderInput } from "src/sections/simulate/environments/panels/PanelCodeUpload";

/**
 * Pick a folder and upload it as one archive. Reports the stored upload, or
 * that one is on its way so the caller can hold its own actions.
 */
export default function FolderUploadField({
  folder,
  onUploaded,
  onUploading,
  disabled,
  testId = "upload-folder",
}) {
  const inputRef = useRef(null);
  const [progress, setProgress] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [picked, setPicked] = useState(null);
  const shown = picked || folder;

  const pick = async (files) => {
    setError("");
    let prepared;
    try {
      prepared = prepareSourceFolder(files);
    } catch (e) {
      setError(e.message);
      return;
    }
    const summary = {
      name: prepared.name,
      files: prepared.files.length,
      bytes: prepared.totalBytes,
    };
    setPicked(summary);
    setUploading(true);
    onUploading?.(true);
    setProgress(0);
    try {
      const uploaded = await uploadSourceFolder(prepared, setProgress);
      onUploaded({ ...summary, sourceId: uploaded.source_id });
    } catch (e) {
      setError(uploadErrorText(e, errorMessage));
    } finally {
      setUploading(false);
      onUploading?.(false);
    }
  };

  const busy = disabled || uploading;
  return (
    <Stack spacing={1}>
      <Box
        component="button"
        type="button"
        data-testid={testId}
        onClick={() => inputRef.current?.querySelector("input")?.click()}
        disabled={busy}
        sx={{
          width: "100%",
          textAlign: "left",
          px: 2,
          py: 1.5,
          borderRadius: 1,
          border: "1px dashed",
          borderColor: "divider",
          bgcolor: "transparent",
          cursor: busy ? "default" : "pointer",
          font: "inherit",
          color: "inherit",
          "&:hover": busy ? undefined : { bgcolor: "action.hover" },
        }}
      >
        <Stack direction="row" alignItems="center" spacing={1.25}>
          <Iconify
            icon="solar:folder-with-files-linear"
            width={20}
            sx={{ color: "text.secondary" }}
          />
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "s1", fontWeight: 500 }} noWrap>
              {shown ? shown.name : "Choose a folder"}
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>
              {shown
                ? `${shown.files} file${shown.files === 1 ? "" : "s"} · ${fData(shown.bytes)}`
                : "Secrets files and dependencies are left out."}
            </Typography>
          </Box>
          {shown && !busy && (
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>
              Change
            </Typography>
          )}
        </Stack>
      </Box>
      <Box ref={inputRef} component="span" sx={{ display: "none" }}>
        <FolderInput onPick={pick} />
      </Box>
      {uploading && <LinearProgress variant="determinate" value={progress} />}
      {error && (
        <Typography
          sx={{ typography: "s3", color: "status.danger.fg" }}
          data-testid={`${testId}-error`}
        >
          {error}
        </Typography>
      )}
    </Stack>
  );
}

FolderUploadField.propTypes = {
  folder: PropTypes.shape({
    name: PropTypes.string,
    files: PropTypes.number,
    bytes: PropTypes.number,
  }),
  onUploaded: PropTypes.func.isRequired,
  onUploading: PropTypes.func,
  disabled: PropTypes.bool,
  testId: PropTypes.string,
};
