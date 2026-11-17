import React, { useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Link from "@mui/material/Link";
import Typography from "@mui/material/Typography";

import { HOST_API } from "src/config-global";

import { hubApi } from "../api/endpoints";
import { useHubVersionFile } from "../api/queries";
import { formatBytes } from "../lib/format";

import { CopyButton } from "./CopyCommand";
import FileTree from "./FileTree";
import FileViewer from "./FileViewer";
import { HubError, HubState } from "./HubStates";

const IMAGE = /\.(png|jpe?g|gif|webp)$/i;

// The registry gives at most a file's first 64 KB as text.
const SHOWN_BYTES = 64 * 1024;
// That limit as the partial-file note says it: a whole number of KB, with a
// no-break space so a line never ends between "64" and "KB".
const SHOWN_LIMIT = `${SHOWN_BYTES / 1024}\u00a0KB`;

// How many lines the viewer numbers for `text`.
function lineCount(text) {
  return String(text ?? "")
    .replace(/\n$/, "")
    .split("\n").length;
}

// What the empty Files tab says: that the version has no files, then the
// next thing to read, its README and, when the entry has other versions,
// the Versions table. An entry with only this version has no other to
// point to, so the sentence ends at the version (and its README).
function noFilesText(version, hasReadme, onlyVersion) {
  const lead = `Version ${version} has no files to show.`;
  if (onlyVersion) return hasReadme ? `${lead} Read its README.` : lead;
  return hasReadme
    ? `${lead} Read its README, or open another version from the Versions table.`
    : `${lead} Open another version from the Versions table.`;
}

// A version with no files: say so in the Hub's calm empty state, and offer
// the next thing to read, its README and the versions.
function NoFiles({ version, hasReadme, onlyVersion, onOpenTab }) {
  return (
    <HubState
      compact
      icon="file"
      titleComponent="h3"
      testId="hub-entry-files-empty"
      title="No files"
      description={noFilesText(version, hasReadme, onlyVersion)}
      action={
        onOpenTab ? (
          <Box
            sx={{
              display: "flex",
              flexWrap: "wrap",
              justifyContent: "center",
              gap: 1,
            }}
          >
            {hasReadme && (
              <Button
                size="small"
                variant="outlined"
                onClick={() => onOpenTab("card")}
              >
                Open the README
              </Button>
            )}
            <Button
              size="small"
              variant="outlined"
              onClick={() => onOpenTab("versions")}
            >
              See all versions
            </Button>
          </Box>
        ) : null
      }
    />
  );
}

NoFiles.propTypes = {
  version: PropTypes.string.isRequired,
  hasReadme: PropTypes.bool,
  onlyVersion: PropTypes.bool,
  onOpenTab: PropTypes.func,
};

// The Files tab: the approved version's files with their sizes, and the open
// one read as plain text through the registry (SPEC §7.2), under a bar that
// names it, its line count and its size, with a button that copies it whole.
// A file longer than the registry gives says only its start is shown, and
// links the archive that holds all of it; its bar gives only its size, as
// the lines shown are not all of its lines. Images come from the same
// registry endpoint. A file that does not load says which, with Try again.
// A version with no files says so and offers its README and the versions;
// its sentence points to other versions only when the entry has more than
// this one (`versionCount`).
export default function EntryFiles({
  publisher,
  name,
  version,
  files,
  hasReadme = false,
  versionCount,
  downloadHref,
  onOpenTab,
}) {
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
  if (list.length === 0) {
    return (
      <NoFiles
        version={version}
        hasReadme={hasReadme}
        onlyVersion={versionCount === 1}
        onOpenTab={onOpenTab}
      />
    );
  }
  const imageUrl = path
    ? `${HOST_API || ""}${hubApi.versionFile(publisher, name, version, path)}`
    : null;
  const open = list.find((row) => row.path === path);
  const total = list.reduce((sum, row) => sum + (Number(row.bytes) || 0), 0);
  const text = !isImage && typeof file.data === "string" ? file.data : null;
  const partial = text !== null && Number(open?.bytes) > SHOWN_BYTES;
  // The start of a longer file is not all of its lines: no count for it.
  const lines = text === null || partial ? null : lineCount(text);
  const facts = [
    lines !== null && `${lines} line${lines === 1 ? "" : "s"}`,
    open && formatBytes(open.bytes),
  ].filter(Boolean);
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
              alignItems: "center",
              justifyContent: "space-between",
              gap: 1.5,
              minWidth: 0,
              minHeight: 28,
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
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 0.5,
                flexShrink: 0,
              }}
            >
              {facts.length > 0 && (
                <Typography
                  typography="s2"
                  color="text.secondary"
                  data-testid="hub-file-facts"
                  sx={{ whiteSpace: "nowrap" }}
                >
                  {facts.join(" · ")}
                </Typography>
              )}
              {/* Only a file shown whole is offered as the whole file. */}
              {text !== null && !partial && (
                <CopyButton
                  text={text}
                  label={`Copy the contents of ${path}`}
                  tooltip="Copy the file"
                  size={13}
                  sx={{ p: 0.5, color: "text.secondary" }}
                />
              )}
            </Box>
          </Box>
        )}
        {partial && (
          <Typography
            typography="s2"
            color="text.secondary"
            data-testid="hub-file-partial"
          >
            {`This file is ${formatBytes(open.bytes)}; the first ${SHOWN_LIMIT} is shown. `}
            {downloadHref ? (
              <>
                <Link
                  href={downloadHref}
                  target="_blank"
                  rel="noopener noreferrer"
                  underline="hover"
                >
                  Download the archive
                </Link>
                {" for the whole file."}
              </>
            ) : (
              "The version's archive holds the whole file."
            )}
          </Typography>
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
  hasReadme: PropTypes.bool,
  versionCount: PropTypes.number,
  downloadHref: PropTypes.string,
  onOpenTab: PropTypes.func,
};
