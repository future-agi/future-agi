import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Skeleton from "@mui/material/Skeleton";
import Typography from "@mui/material/Typography";

import { useFileBlob } from "../api/queries";
import SandboxErrorState from "../components/SandboxErrorState";
import { formatBytes } from "../lib/format";
import { PREVIEW_MAX_BYTES } from "../lib/files";

/**
 * An image the sandbox made (a chart, a screenshot), drawn from its bytes. The
 * bytes become a local object URL, so the image never loads from the sandbox
 * directly and nothing in it can run; very large images are offered for
 * download instead.
 */
export default function FilePreview({ sandboxId, path, bytes }) {
  const tooLarge = typeof bytes === "number" && bytes > PREVIEW_MAX_BYTES;
  const blob = useFileBlob(sandboxId, path, !tooLarge);
  const [url, setUrl] = useState(null);
  const [broken, setBroken] = useState(false);
  useEffect(() => {
    setBroken(false);
    if (!blob.data) {
      setUrl(null);
      return undefined;
    }
    const next = URL.createObjectURL(blob.data);
    setUrl(next);
    return () => URL.revokeObjectURL(next);
  }, [blob.data]);

  if (tooLarge) {
    return (
      <Typography typography="s1" color="text.secondary">
        This image is {formatBytes(bytes)}, too large to show here. Download it
        to open it.
      </Typography>
    );
  }
  if (blob.error)
    return (
      <SandboxErrorState
        compact
        error={blob.error}
        notFoundTitle="File not found"
      />
    );
  if (!url)
    return (
      <Skeleton
        variant="rounded"
        height={240}
        data-testid="file-preview-loading"
      />
    );
  if (broken) {
    return (
      <Typography typography="s1" color="text.secondary">
        This file is named like an image but is not one a browser can show.
        Download it to open it.
      </Typography>
    );
  }
  return (
    <Box
      sx={{
        bgcolor: "background.neutral",
        borderRadius: 1,
        p: 1,
        display: "flex",
        justifyContent: "center",
        // A checkerboard shows where a transparent image has no pixels.
        backgroundImage:
          "linear-gradient(45deg, rgba(128,128,128,.12) 25%, transparent 25%, transparent 75%, rgba(128,128,128,.12) 75%), linear-gradient(45deg, rgba(128,128,128,.12) 25%, transparent 25%, transparent 75%, rgba(128,128,128,.12) 75%)",
        backgroundSize: "16px 16px",
        backgroundPosition: "0 0, 8px 8px",
      }}
    >
      <Box
        component="img"
        src={url}
        alt={`Preview of ${path}`}
        data-testid="file-preview"
        onError={() => setBroken(true)}
        sx={{
          display: "block",
          maxWidth: "100%",
          maxHeight: 520,
          objectFit: "contain",
        }}
      />
    </Box>
  );
}

FilePreview.propTypes = {
  sandboxId: PropTypes.string.isRequired,
  path: PropTypes.string.isRequired,
  bytes: PropTypes.number,
};
