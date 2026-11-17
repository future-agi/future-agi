import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import CircularProgress from "@mui/material/CircularProgress";

import { useSandboxMutation } from "../api/queries";
import SandboxIcon from "../components/SandboxIcon";
import { joinPath } from "../lib/files";
import FileNameDialog from "./FileNameDialog";

/**
 * The Files list's actions for the current folder: Upload (one or more files
 * from this computer; also by dropping them on the list), New folder and New
 * file. Shown only to a viewer who may change the sandbox's files.
 */
export default function FilesToolbar({
  sandboxId,
  dir,
  onNewFile,
  onUpload,
  uploads,
}) {
  const input = useRef(null);
  const [naming, setNaming] = useState(false);
  const mkdir = useSandboxMutation(
    (api, name) => api.makeFolder(sandboxId, joinPath(dir, name)),
    {
      onSuccess: () => setNaming(false),
    },
  );
  return (
    <>
      <Box
        sx={{
          display: "flex",
          gap: 0.5,
          flexWrap: "wrap",
          justifyContent: "flex-end",
          // Stays on the right when it wraps under a long folder path.
          ml: "auto",
        }}
      >
        <input
          ref={input}
          type="file"
          multiple
          hidden
          data-testid="file-upload-input"
          onChange={(event) => {
            const files = Array.from(event.target.files || []);
            event.target.value = "";
            // FilesTab asks first when a file would replace one in the list.
            onUpload(files);
          }}
        />
        <Button
          size="small"
          onClick={() => input.current?.click()}
          disabled={Boolean(uploads.progress)}
          startIcon={
            uploads.progress ? (
              <CircularProgress size={12} />
            ) : (
              <SandboxIcon name="upload" size={14} />
            )
          }
          data-testid="file-upload"
        >
          Upload
        </Button>
        <Button
          size="small"
          onClick={() => {
            mkdir.reset();
            setNaming(true);
          }}
          startIcon={<SandboxIcon name="folderPlus" size={14} />}
          data-testid="new-folder"
        >
          New folder
        </Button>
        <Button
          size="small"
          onClick={onNewFile}
          startIcon={<SandboxIcon name="plus" size={14} />}
          data-testid="new-file"
        >
          New file
        </Button>
      </Box>
      <FileNameDialog
        open={naming}
        title="New folder"
        confirmLabel="Create"
        pending={mkdir.isPending}
        error={mkdir.error}
        onClose={() => setNaming(false)}
        onSubmit={(name) => mkdir.mutate(name)}
      />
    </>
  );
}

FilesToolbar.propTypes = {
  sandboxId: PropTypes.string.isRequired,
  dir: PropTypes.string.isRequired,
  onNewFile: PropTypes.func.isRequired,
  /** Called with the chosen files; FilesTab uploads them, or asks first. */
  onUpload: PropTypes.func.isRequired,
  uploads: PropTypes.shape({
    progress: PropTypes.object,
  }).isRequired,
};
