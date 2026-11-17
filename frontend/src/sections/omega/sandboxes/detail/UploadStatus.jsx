import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import IconButton from "@mui/material/IconButton";
import Typography from "@mui/material/Typography";

import SandboxIcon from "../components/SandboxIcon";
import { formatBytes } from "../lib/format";

/** What is uploading now, and how the last batch went. */
export default function UploadStatus({ uploads }) {
  const { progress, results, clear } = uploads;
  if (progress) {
    return (
      <Typography
        typography="s2"
        color="text.secondary"
        role="status"
        data-testid="upload-progress"
        sx={{ px: 1.5, py: 1, overflowWrap: "anywhere" }}
      >
        Uploading {progress.current} ({progress.done + 1} of {progress.total})…
      </Typography>
    );
  }
  if (results.length === 0) return null;
  const failed = results.filter((result) => !result.ok);
  return (
    <Box
      role="status"
      data-testid="upload-results"
      sx={{ px: 1.5, py: 1, display: "flex", gap: 1, alignItems: "flex-start" }}
    >
      <Box sx={{ flex: 1, minWidth: 0 }}>
        {failed.length < results.length && (
          <Typography typography="s2" color="success.main">
            Uploaded {results.length - failed.length} of {results.length}
            {results.length === 1 && results[0].ok
              ? ` (${formatBytes(results[0].bytes)})`
              : ""}
            . Successful uploads are recorded in Activity.
          </Typography>
        )}
        {failed.map((result) => (
          <Typography
            key={result.name}
            typography="s2"
            color="error.main"
            sx={{ overflowWrap: "anywhere" }}
          >
            {result.name}: {result.message}
          </Typography>
        ))}
      </Box>
      <IconButton
        size="small"
        aria-label="Dismiss upload results"
        onClick={clear}
      >
        <SandboxIcon name="close" size={14} />
      </IconButton>
    </Box>
  );
}

UploadStatus.propTypes = {
  uploads: PropTypes.shape({
    progress: PropTypes.object,
    results: PropTypes.array,
    clear: PropTypes.func,
  }).isRequired,
};
