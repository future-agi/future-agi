import React, { useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";

import { useReviewFile } from "../api/queries";
import { findingsByFile, marksFor } from "../lib/report";

import FileTree from "./FileTree";
import FileViewer from "./FileViewer";

// The submission's files for a reviewer (SPEC §5.2): the tree with per-file
// findings, and a plain-text viewer that marks each finding's line. The page
// may choose the file (a flag's place opens its file here); otherwise the
// first file with a finding opens.
export default function ReviewFiles({ submission, selected, onSelect }) {
  const byFile = findingsByFile(submission.report);
  const files = submission.files || [];
  const first =
    Object.keys(byFile)[0] ||
    files.find((f) => /\.(m?[jt]s|tsx)$/.test(f.path))?.path ||
    files[0]?.path;
  const [own, setOwn] = useState(first);
  const path = selected || own;
  const choose = (next) => {
    setOwn(next);
    onSelect?.(next);
  };
  const image = /\.(png|jpe?g|gif|webp)$/i.test(path || "");
  const file = useReviewFile(submission.id, image ? null : path);
  return (
    <Box
      data-testid="hub-review-files"
      sx={{
        display: "grid",
        gap: 2,
        gridTemplateColumns: { xs: "1fr", lg: "240px minmax(0, 1fr)" },
        alignItems: "start",
      }}
    >
      <FileTree
        files={files}
        selected={path}
        onSelect={choose}
        findingsByFile={byFile}
      />
      <FileViewer
        path={path}
        text={file.data}
        isLoading={file.isLoading}
        error={file.error}
        marks={marksFor(path, { report: submission.report })}
      />
    </Box>
  );
}

ReviewFiles.propTypes = {
  submission: PropTypes.object.isRequired,
  selected: PropTypes.string,
  onSelect: PropTypes.func,
};
