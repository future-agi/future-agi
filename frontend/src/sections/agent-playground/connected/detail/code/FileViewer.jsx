import PropTypes from "prop-types";
import React from "react";
import { Box, Skeleton, Typography } from "@mui/material";
import { Editor } from "@monaco-editor/react";
import { EDITOR_OPTIONS, editorTheme } from "./editorOptions";

const KIND_TEXT = {
  binary: "This file is binary; nothing to show.",
  too_large: "This file is over 1 MiB; it isn't shown here.",
};

/** One file of a version: text in a read-only editor, else why not. */
export default function FileViewer({ file, loading }) {
  if (loading) {
    return (
      <Box sx={{ p: 2 }} data-testid="file-viewer-loading">
        <Skeleton variant="rounded" height={240} />
      </Box>
    );
  }
  if (!file) {
    return (
      <Typography
        sx={{ p: 2, typography: "s2", color: "text.secondary" }}
        data-testid="file-viewer-empty"
      >
        Pick a file to read it.
      </Typography>
    );
  }
  if (file.kind !== "text") {
    return (
      <Typography
        sx={{ p: 2, typography: "s2", color: "text.secondary" }}
        data-testid="file-viewer-kind"
      >
        {KIND_TEXT[file.kind] || "This file can't be shown."}
      </Typography>
    );
  }
  return (
    <Box sx={{ height: "100%", minHeight: 320 }} data-testid="file-viewer">
      <Editor
        height="100%"
        path={file.path}
        language={file.language}
        value={file.content}
        theme={editorTheme()}
        options={EDITOR_OPTIONS}
      />
    </Box>
  );
}

FileViewer.propTypes = {
  file: PropTypes.object,
  loading: PropTypes.bool,
};
