import PropTypes from "prop-types";
import React, { useRef, useState } from "react";
import { useSnackbar } from "notistack";
import { Box, Button, LinearProgress, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import ConfirmDialog from "src/components/custom-dialog/confirm-dialog";
import { useChangeVersion } from "src/api/agents/agentDetail";
import { fData } from "src/utils/format-number";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import {
  prepareSourceFolder,
  uploadErrorText,
  uploadSourceFolder,
} from "src/pages/dashboard/harness/sourceUpload";
import { FolderInput } from "src/sections/simulate/environments/panels/PanelCodeUpload";

/**
 * A new version of an uploaded agent from a folder: the folder is uploaded as
 * one archive, then the version is made from it and read. The same archive
 * again makes no version.
 */
export default function UploadVersionDialog({ agent, open, onClose }) {
  const { enqueueSnackbar } = useSnackbar();
  const change = useChangeVersion(agent.id);
  const inputRef = useRef(null);
  const [folder, setFolder] = useState(null);
  const [progress, setProgress] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [archiveId, setArchiveId] = useState(null);
  const [error, setError] = useState("");
  const next = `v${(agent.versionCount || 0) + 1}`;

  const pick = async (files) => {
    setError("");
    setArchiveId(null);
    let prepared;
    try {
      prepared = prepareSourceFolder(files);
    } catch (e) {
      setError(e.message);
      return;
    }
    setFolder({
      name: prepared.name,
      files: prepared.files.length,
      bytes: prepared.totalBytes,
    });
    setUploading(true);
    setProgress(0);
    try {
      const uploaded = await uploadSourceFolder(prepared, setProgress);
      setArchiveId(uploaded.source_id);
    } catch (e) {
      setError(uploadErrorText(e, errorMessage));
    } finally {
      setUploading(false);
    }
  };

  const submit = () =>
    change.mutate(
      { kind: "upload", archive_artifact_id: archiveId },
      {
        onSuccess: (data) => {
          onClose();
          const label = data.version.version_name_display;
          enqueueSnackbar(
            data.up_to_date
              ? `${label} already has this code`
              : `${label} created from the upload; it is being read`,
            { variant: data.up_to_date ? "info" : "success" },
          );
        },
        onError: (e) => enqueueSnackbar(errorMessage(e), { variant: "error" }),
      },
    );

  const busy = uploading || change.isPending;
  return (
    <ConfirmDialog
      open={open}
      onClose={() => (busy ? null : onClose())}
      closeDisabled={busy}
      title={`Upload ${next}`}
      content={
        <Stack spacing={1.5}>
          <Typography sx={{ typography: "s2_1", color: "text.secondary" }}>
            Choose the folder with the new code. It is read, and then you can
            set the version as production.
          </Typography>
          <Box
            component="button"
            type="button"
            data-testid="upload-version-dropzone"
            onClick={() => inputRef.current?.click()}
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
            }}
          >
            <Stack direction="row" alignItems="center" spacing={1.25}>
              <Iconify
                icon="solar:folder-with-files-linear"
                width={20}
                sx={{ color: "text.secondary" }}
              />
              <Box flex={1} minWidth={0}>
                <Typography sx={{ typography: "s2_1", fontWeight: 500 }}>
                  {folder ? folder.name : "Choose a folder"}
                </Typography>
                <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                  {folder
                    ? `${folder.files} file${folder.files === 1 ? "" : "s"} · ${fData(folder.bytes)}`
                    : "Secrets files and dependencies are left out."}
                </Typography>
              </Box>
            </Stack>
          </Box>
          <Box ref={inputRef} component="span" sx={{ display: "none" }}>
            <FolderInput onPick={pick} />
          </Box>
          {uploading && (
            <LinearProgress variant="determinate" value={progress} />
          )}
          {error && (
            <Typography
              sx={{ typography: "s2", color: "error.main" }}
              data-testid="upload-version-error"
            >
              {error}
            </Typography>
          )}
        </Stack>
      }
      action={
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={busy || !archiveId}
          onClick={submit}
        >
          {`Save as ${next}`}
        </Button>
      }
    />
  );
}

UploadVersionDialog.propTypes = {
  agent: PropTypes.object.isRequired,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
};
