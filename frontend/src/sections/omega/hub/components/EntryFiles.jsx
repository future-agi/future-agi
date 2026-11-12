import React, { useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

import { HOST_API } from "src/config-global";

import { hubApi } from "../api/endpoints";
import { useHubVersionFile } from "../api/queries";
import { formatBytes } from "../lib/format";

import FileTree from "./FileTree";
import FileViewer from "./FileViewer";
import { HubError } from "./HubStates";

const IMAGE = /\.(png|jpe?g|gif|webp)$/i;

// The Files tab: the approved version's files with their sizes, and the open
// one read as plain text through the registry (SPEC §7.2), under a bar that
// names it and its size. Images come from the same registry endpoint. A file
// that does not load says which, with Try again.
export default function EntryFiles({ publisher, name, version, files }) {
  const list = files || [];
  const first =
    list.find((file) => /readme\.md$/i.test(file.path))?.path || list[0]?.path;
  const [path, setPath] = useState(first);
  const isImage = IMAGE.test(path || "");
  const file = useHubVersionFile(
    publisher,
    name,
    version,
    isImage ? null : path,
  );
  const imageUrl = path
    ? `${HOST_API || ""}${hubApi.versionFile(publisher, name, version, path)}`
    : null;
  const open = list.find((row) => row.path === path);
  const total = list.reduce((sum, row) => sum + (Number(row.bytes) || 0), 0);
  return (
    <Box
      data-testid="hub-entry-files"
      sx={{
        display: "grid",
        gap: 2,
        gridTemplateColumns: {
          xs: "minmax(0, 1fr)",
          md: "240px minmax(0, 1fr)",
        },
        alignItems: "start",
      }}
    >
      <Box
        sx={{ minWidth: 0, display: "flex", flexDirection: "column", gap: 1 }}
      >
        <Typography typography="s2" color="text.secondary">
          {`${list.length} file${list.length === 1 ? "" : "s"} · ${formatBytes(total)}`}
        </Typography>
        <FileTree files={list} selected={path} onSelect={setPath} />
      </Box>
      <Box
        sx={{ minWidth: 0, display: "flex", flexDirection: "column", gap: 1 }}
      >
        {path && (
          <Box
            sx={{
              display: "flex",
              alignItems: "baseline",
              justifyContent: "space-between",
              gap: 1.5,
              minWidth: 0,
            }}
          >
            <Typography
              component="h3"
              typography="code_md"
              color="text.primary"
              sx={{ fontWeight: 600, overflowWrap: "anywhere", minWidth: 0 }}
            >
              {path}
            </Typography>
            {open && (
              <Typography
                typography="s2"
                color="text.secondary"
                sx={{ whiteSpace: "nowrap" }}
              >
                {formatBytes(open.bytes)}
              </Typography>
            )}
          </Box>
        )}
        {file.isError && !isImage ? (
          <HubError
            compact
            title={`${path} did not load`}
            error={file.error}
            onRetry={() => file.refetch()}
          />
        ) : (
          <FileViewer
            path={path}
            text={file.data}
            isLoading={file.isLoading}
            imageUrl={imageUrl}
          />
        )}
      </Box>
    </Box>
  );
}

EntryFiles.propTypes = {
  publisher: PropTypes.string.isRequired,
  name: PropTypes.string.isRequired,
  version: PropTypes.string.isRequired,
  files: PropTypes.array,
};
