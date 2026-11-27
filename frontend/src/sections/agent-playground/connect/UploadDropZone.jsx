import PropTypes from "prop-types";
import { useRef, useState } from "react";
import { Box, Button, LinearProgress, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { useDropzone } from "react-dropzone";
import { useAuthContext } from "src/auth/hooks";
import { useStartScan } from "src/api/agents/agentConnect";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import {
  prepareSourceFolder,
  uploadErrorText,
  uploadSourceFolder,
} from "src/pages/dashboard/harness/sourceUpload";
import FolderInput from "src/sections/simulate/environments/components/FolderInput";
import { fData } from "src/utils/format-number";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

const relativePath = (file) =>
  file.webkitRelativePath || String(file.path || "").replace(/^\.?\//, "");

/**
 * Drop or browse a folder right on the New agent page. The folder is uploaded
 * and read for agents here, then the caller opens the drawer on the agents it
 * found, so picking the folder is no longer a drawer screen of its own.
 */
export default function UploadDropZone({ onScanned, disabled = false }) {
  const { role } = useAuthContext();
  const canConnect =
    !!RolePermission.SIMULATION_AGENT[PERMISSIONS.CREATE][role];
  const pickerRef = useRef(null);
  const startScan = useStartScan();
  const [folder, setFolder] = useState(null);
  const [stage, setStage] = useState("idle");
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState("");
  // An open drawer only takes the folder picker away; the zone stays as is.
  const inert = !canConnect;

  const upload = async (list) => {
    setError("");
    let prepared;
    try {
      prepared = prepareSourceFolder(list);
    } catch (e) {
      setError(e.message);
      return;
    }
    const summary = {
      name: prepared.name,
      files: prepared.files.length,
      bytes: prepared.totalBytes,
    };
    setFolder(summary);
    setProgress(0);
    setStage("uploading");
    let uploaded;
    try {
      uploaded = await uploadSourceFolder(prepared, setProgress);
    } catch (e) {
      setError(uploadErrorText(e, errorMessage));
      setStage("idle");
      return;
    }
    const stored = { ...summary, sourceId: uploaded.source_id };
    setStage("scanning");
    startScan.mutate(
      { kind: "archive", archive_artifact_id: uploaded.source_id },
      {
        onSuccess: (data) => {
          setStage("idle");
          setFolder(null);
          onScanned({ folder: stored, scanId: data.id });
        },
        onError: (e) => {
          setStage("idle");
          setError(errorMessage(e));
        },
      },
    );
  };

  const drop = (accepted) => {
    if (disabled || !accepted.length) return;
    if (!accepted.some((file) => relativePath(file).includes("/"))) {
      setError("Drop the folder itself, not files from inside it.");
      return;
    }
    upload(accepted);
  };

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    multiple: true,
    noClick: true,
    noKeyboard: true,
    disabled: inert,
    onDrop: (accepted) => drop(accepted),
  });

  const browse = () => {
    if (inert || disabled || stage !== "idle") return;
    pickerRef.current?.querySelector("input")?.click();
  };

  return (
    <Stack spacing={1} sx={{ m: 1 }}>
      {stage === "idle" ? (
        <Box
          {...getRootProps({ onClick: browse })}
          data-testid="connect-upload-folder"
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1.5,
            px: 2,
            py: 1.25,
            borderRadius: 1,
            border: "1px dashed",
            borderColor: isDragActive ? "primary.main" : "divider",
            bgcolor: isDragActive ? "action.selected" : "transparent",
            cursor: inert ? "default" : "pointer",
            outline: "none",
            transition: "background-color 120ms, border-color 120ms",
            "&:hover": inert ? undefined : { bgcolor: "action.hover" },
            ...(inert && { opacity: 0.48 }),
          }}
        >
          <input {...getInputProps()} />
          <Iconify
            icon="solar:upload-minimalistic-linear"
            width={20}
            sx={{ color: "text.secondary", flexShrink: 0 }}
          />
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "s1", fontWeight: 500 }}>
              Choose a folder or drag &amp; drop it here
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>
              Up to 200 MB · secrets and dependencies are left out
            </Typography>
          </Box>
          <Button
            size="small"
            variant="contained"
            color="primary"
            disabled={inert}
            sx={{ flexShrink: 0 }}
          >
            Browse folder
          </Button>
        </Box>
      ) : (
        <Stack
          spacing={1.25}
          data-testid="connect-upload-progress"
          sx={{
            px: 2,
            py: 1.5,
            borderRadius: 1,
            border: "1px dashed",
            borderColor: "divider",
          }}
        >
          <Stack direction="row" alignItems="center" spacing={1.25}>
            <Iconify
              icon="solar:folder-with-files-linear"
              width={20}
              sx={{ color: "text.secondary" }}
            />
            <Box flex={1} minWidth={0}>
              <Typography noWrap sx={{ typography: "s1", fontWeight: 500 }}>
                {folder?.name}
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                {`${folder?.files} file${folder?.files === 1 ? "" : "s"} · ${fData(folder?.bytes)}`}
              </Typography>
            </Box>
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>
              {stage === "uploading"
                ? `Uploading ${progress}%`
                : "Finding agents"}
            </Typography>
          </Stack>
          <LinearProgress
            variant={stage === "uploading" ? "determinate" : "indeterminate"}
            value={progress}
          />
        </Stack>
      )}
      {!disabled && (
        <Box ref={pickerRef} component="span" sx={{ display: "none" }}>
          <FolderInput onPick={upload} />
        </Box>
      )}
      {error && (
        <Typography
          sx={{ typography: "s3", color: "status.danger.fg" }}
          data-testid="connect-upload-folder-error"
        >
          {error}
        </Typography>
      )}
    </Stack>
  );
}

UploadDropZone.propTypes = {
  onScanned: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};
