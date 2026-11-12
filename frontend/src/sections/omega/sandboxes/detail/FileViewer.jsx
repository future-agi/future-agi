import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import LoadingButton from "@mui/lab/LoadingButton";

import { useFileContent, useSandboxMutation } from "../api/queries";
import { LoadingRows } from "../components/SessionGate";
import SandboxErrorState from "../components/SandboxErrorState";
import SandboxIcon from "../components/SandboxIcon";
import { isPreviewableImage } from "../lib/files";
import { formatBytes, formatDateTime } from "../lib/format";
import FilePreview from "./FilePreview";

const monoFont =
  '"JetBrains Mono", "SFMono-Regular", Menlo, Consolas, monospace';

/**
 * One file: read it, or edit and save it (an audited write); see an image the
 * sandbox made; download any file. A new file starts in edit mode with no
 * content to read.
 */
export default function FileViewer({
  sandboxId,
  path,
  isNew,
  canEdit,
  canDownload = false,
  size,
  download,
  onSaved,
}) {
  const image = !isNew && isPreviewableImage(path);
  // An image is drawn from its bytes (download), so its JSON read is skipped.
  const file = useFileContent(sandboxId, isNew || image ? null : path);
  const [editing, setEditing] = useState(Boolean(isNew));
  const [draft, setDraft] = useState("");
  const [saved, setSaved] = useState(null);
  const save = useSandboxMutation(
    (api, content) => api.writeFile(sandboxId, path, content),
    {
      onSuccess: (result) => {
        setSaved(result);
        setEditing(false);
        onSaved?.(result);
      },
    },
  );

  useEffect(() => {
    setEditing(Boolean(isNew));
    setSaved(null);
    setDraft("");
    save.reset();
    // Reset when another file is opened (a saved new file keeps its message).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);

  const data = file.data;
  const text = data?.encoding === "utf8" ? data.content : null;

  let body;
  if (image)
    body = <FilePreview sandboxId={sandboxId} path={path} bytes={size} />;
  else if (!isNew && file.isLoading) body = <LoadingRows rows={3} />;
  else if (!isNew && file.error)
    body = (
      <SandboxErrorState
        compact
        error={file.error}
        notFoundTitle="File not found"
        onRetry={() => file.refetch()}
      />
    );
  else if (editing) {
    body = (
      <TextField
        multiline
        fullWidth
        minRows={10}
        maxRows={24}
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        InputProps={{
          sx: { "& textarea": { fontFamily: monoFont, fontSize: 12.5 } },
        }}
        inputProps={{
          "aria-label": `Contents of ${path}`,
          "data-testid": "file-editor",
        }}
      />
    );
  } else if (text === null) {
    body = (
      <Typography typography="s1" color="text.secondary">
        Binary file ({formatBytes(data?.bytes)}). It isn&apos;t shown here
        {canDownload ? "; download it to open it on your computer." : "."}
      </Typography>
    );
  } else {
    body = (
      <Box
        component="pre"
        data-testid="file-content"
        sx={{
          m: 0,
          p: 1.5,
          maxHeight: 480,
          overflow: "auto",
          fontFamily: monoFont,
          fontSize: 12.5,
          lineHeight: 1.6,
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          bgcolor: "background.neutral",
          borderRadius: 1,
        }}
      >
        {text || " "}
      </Box>
    );
  }

  return (
    <Box
      sx={{ display: "flex", flexDirection: "column", gap: 1.5, minWidth: 0 }}
      data-testid="file-viewer"
    >
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 1,
          flexWrap: "wrap",
        }}
      >
        <Box sx={{ minWidth: 0 }}>
          <Typography
            typography="s1"
            fontWeight="fontWeightMedium"
            sx={{ fontFamily: monoFont, overflowWrap: "anywhere" }}
          >
            {path}
          </Typography>
          {data && !editing && (
            <Typography typography="s2" color="text.secondary">
              {formatBytes(data.bytes)} · modified{" "}
              {formatDateTime(data.modifiedAt)}
              {data.truncated ? " · showing the first part only" : ""}
            </Typography>
          )}
          {image && typeof size === "number" && (
            <Typography typography="s2" color="text.secondary">
              {formatBytes(size)} · image
            </Typography>
          )}
        </Box>
        {canDownload && !isNew && !editing && (
          <LoadingButton
            size="small"
            variant="outlined"
            color="inherit"
            loading={download?.pending === path}
            onClick={() => download?.download(path)}
            startIcon={<SandboxIcon name="download" size={14} />}
            data-testid="file-download"
          >
            Download
          </LoadingButton>
        )}
        {canEdit && !editing && (text !== null || isNew) && !file.error && (
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={() => {
              setDraft(text ?? "");
              setEditing(true);
            }}
            disabled={data?.truncated}
            data-testid="file-edit"
          >
            Edit
          </Button>
        )}
        {editing && (
          <Box sx={{ display: "flex", gap: 1 }}>
            {!isNew && (
              <Button
                size="small"
                color="inherit"
                onClick={() => setEditing(false)}
                disabled={save.isPending}
              >
                Cancel
              </Button>
            )}
            <LoadingButton
              size="small"
              variant="contained"
              loading={save.isPending}
              onClick={() => save.mutate(draft)}
              data-testid="file-save"
            >
              Save
            </LoadingButton>
          </Box>
        )}
      </Box>
      {save.error && <Alert severity="error">{save.error.message}</Alert>}
      {saved && !editing && (
        <Typography
          typography="s2"
          color="success.main"
          data-testid="file-saved"
        >
          Saved {formatBytes(saved.bytes)}. The write is recorded in Activity.
        </Typography>
      )}
      {body}
    </Box>
  );
}

FileViewer.propTypes = {
  sandboxId: PropTypes.string.isRequired,
  path: PropTypes.string.isRequired,
  isNew: PropTypes.bool,
  canEdit: PropTypes.bool,
  canDownload: PropTypes.bool,
  size: PropTypes.number,
  download: PropTypes.shape({
    download: PropTypes.func,
    pending: PropTypes.string,
  }),
  onSaved: PropTypes.func,
};
