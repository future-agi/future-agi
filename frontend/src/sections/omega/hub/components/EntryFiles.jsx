import React, { useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";

import { HOST_API } from "src/config-global";

import { hubApi } from "../api/endpoints";
import { useHubVersionFile } from "../api/queries";

import FileTree from "./FileTree";
import FileViewer from "./FileViewer";

// The Files tab: the approved version's files, read as plain text through the
// registry (SPEC §7.2). Images come from the same registry endpoint.
export default function EntryFiles({ publisher, name, version, files }) {
  const first = (files || []).find((f) => /readme\.md$/i.test(f.path))?.path || files?.[0]?.path;
  const [path, setPath] = useState(first);
  const file = useHubVersionFile(publisher, name, version, /\.(png|jpe?g|gif|webp)$/i.test(path || "") ? null : path);
  const imageUrl = path ? `${HOST_API || ""}${hubApi.versionFile(publisher, name, version, path)}` : null;
  return (
    <Box
      data-testid="hub-entry-files"
      sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", md: "260px minmax(0, 1fr)" }, alignItems: "start" }}
    >
      <FileTree files={files} selected={path} onSelect={setPath} />
      <FileViewer
        path={path}
        text={file.data}
        isLoading={file.isLoading}
        error={file.error}
        imageUrl={imageUrl}
      />
    </Box>
  );
}

EntryFiles.propTypes = {
  publisher: PropTypes.string.isRequired,
  name: PropTypes.string.isRequired,
  version: PropTypes.string.isRequired,
  files: PropTypes.array,
};
