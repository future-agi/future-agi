import PropTypes from "prop-types";
import React from "react";
import { Box, Skeleton, Typography } from "@mui/material";
import { DiffEditor } from "@monaco-editor/react";
import { useCodeFile } from "src/api/agents/agentDetail";
import { EDITOR_OPTIONS, editorTheme } from "./FileViewer";

// A side that is missing (the file was added or removed) diffs against empty.
const text = (file) => (file && file.kind === "text" ? file.content : "");

/**
 * One file in two versions, side by side: both sides are loaded here and
 * diffed in the browser; the host is never asked.
 */
export default function FileDiff({ agentId, versionId, againstId, path }) {
  const current = useCodeFile(agentId, versionId, path);
  const previous = useCodeFile(agentId, againstId, path);
  if (current.isLoading || previous.isLoading) {
    return (
      <Box sx={{ p: 2 }} data-testid="file-diff-loading">
        <Skeleton variant="rounded" height={240} />
      </Box>
    );
  }
  const file = current.data || previous.data;
  if (!file) {
    return (
      <Typography sx={{ p: 2, typography: "s2", color: "text.secondary" }}>
        Pick a file to compare it.
      </Typography>
    );
  }
  if (
    (current.data && current.data.kind !== "text") ||
    (previous.data && previous.data.kind !== "text")
  ) {
    return (
      <Typography
        sx={{ p: 2, typography: "s2", color: "text.secondary" }}
        data-testid="file-diff-kind"
      >
        Only text files are compared.
      </Typography>
    );
  }
  return (
    <Box sx={{ height: "100%", minHeight: 320 }} data-testid="file-diff">
      <DiffEditor
        height="100%"
        language={file.language}
        original={text(previous.data)}
        modified={text(current.data)}
        theme={editorTheme()}
        options={{ ...EDITOR_OPTIONS, renderSideBySide: true }}
      />
    </Box>
  );
}

FileDiff.propTypes = {
  agentId: PropTypes.string.isRequired,
  versionId: PropTypes.string.isRequired,
  againstId: PropTypes.string.isRequired,
  path: PropTypes.string.isRequired,
};
