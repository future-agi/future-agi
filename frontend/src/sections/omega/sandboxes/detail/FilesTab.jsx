import React, { useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Link from "@mui/material/Link";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import LoadingButton from "@mui/lab/LoadingButton";

import { useFileList } from "../api/queries";
import DeletedPanel from "./DeletedPanel";
import SandboxErrorState from "../components/SandboxErrorState";
import SandboxIcon from "../components/SandboxIcon";
import { LoadingRows } from "../components/SessionGate";
import StatePanel from "../components/StatePanel";
import { joinPath as join } from "../lib/files";
import { formatBytes } from "../lib/format";
import FileRowMenu from "./FileRowMenu";
import FilesToolbar from "./FilesToolbar";
import FileViewer from "./FileViewer";
import UploadStatus from "./UploadStatus";
import useFileDownload from "./useFileDownload";
import useFileUploads from "./useFileUploads";

function Breadcrumbs({ path, onOpen }) {
  const parts = path ? path.split("/") : [];
  return (
    <Box
      sx={{
        display: "flex",
        flexWrap: "wrap",
        alignItems: "center",
        gap: 0.5,
        typography: "s2",
        fontFamily: "monospace",
      }}
    >
      <Link
        component="button"
        type="button"
        onClick={() => onOpen("")}
        underline="hover"
        color={parts.length ? "text.secondary" : "text.primary"}
      >
        /workspace
      </Link>
      {parts.map((part, index) => (
        <React.Fragment key={index}>
          <Box component="span" sx={{ color: "text.disabled" }}>
            /
          </Box>
          <Link
            component="button"
            type="button"
            underline="hover"
            color={
              index === parts.length - 1 ? "text.primary" : "text.secondary"
            }
            onClick={() => onOpen(parts.slice(0, index + 1).join("/"))}
          >
            {part}
          </Link>
        </React.Fragment>
      ))}
    </Box>
  );
}

Breadcrumbs.propTypes = {
  path: PropTypes.string.isRequired,
  onOpen: PropTypes.func.isRequired,
};

function NewFile({ dir, onCreate, onCancel }) {
  const [name, setName] = useState("");
  const valid =
    /^[^/\0][^\0]*$/.test(name.trim()) &&
    !name.split("/").some((p) => p === "." || p === "..");
  return (
    <Box
      component="form"
      onSubmit={(e) => {
        e.preventDefault();
        if (valid) onCreate(join(dir, name.trim()));
      }}
      sx={{ display: "flex", gap: 1, alignItems: "center" }}
    >
      <TextField
        autoFocus
        size="small"
        placeholder="notes.txt"
        value={name}
        onChange={(e) => setName(e.target.value)}
        inputProps={{
          "aria-label": "New file name",
          "data-testid": "new-file-name",
        }}
        sx={{ flex: 1 }}
      />
      <Button size="small" color="inherit" onClick={onCancel}>
        Cancel
      </Button>
      <LoadingButton
        size="small"
        variant="contained"
        type="submit"
        disabled={!valid}
      >
        Create
      </LoadingButton>
    </Box>
  );
}

NewFile.propTypes = {
  dir: PropTypes.string.isRequired,
  onCreate: PropTypes.func.isRequired,
  onCancel: PropTypes.func.isRequired,
};

export default function FilesTab({ sandbox, onResume, resuming }) {
  const live = sandbox.state === "active";
  const [dir, setDir] = useState("");
  const [open, setOpen] = useState(null);
  const [adding, setAdding] = useState(false);
  const [dragging, setDragging] = useState(false);
  const listing = useFileList(sandbox.id, dir, live);
  const canEdit = !sandbox.readOnly;
  // An agent run's desk is read through the v1 read route only; it has no download.
  const canDownload = !sandbox.readOnly;
  const uploads = useFileUploads(sandbox.id);
  const download = useFileDownload(sandbox.id);

  if (!live) {
    if (sandbox.state === "destroyed")
      return <DeletedPanel sandbox={sandbox} what="files" />;
    const hibernated = sandbox.state === "hibernated";
    return (
      <StatePanel
        compact
        icon="folder"
        title={
          hibernated
            ? "Files are kept while the sandbox is hibernated"
            : "This sandbox isn't running"
        }
        description={
          hibernated
            ? "Resume it to browse and edit them."
            : "Its files can be browsed while it runs."
        }
        action={
          sandbox.state === "hibernated" && onResume && !sandbox.readOnly ? (
            <LoadingButton
              size="small"
              variant="contained"
              loading={resuming}
              onClick={onResume}
            >
              Resume
            </LoadingButton>
          ) : null
        }
      />
    );
  }

  const entries = listing.data?.entries ?? [];
  const openDir = (path) => {
    setDir(path);
    setOpen(null);
    setAdding(false);
  };
  // Keep the open file in step with a rename or delete made from the list.
  const onChanged = (change) => {
    if (!open) return;
    if (change.kind === "moved" && open.path === change.from)
      setOpen({ ...open, path: change.to });
    if (
      change.kind === "deleted" &&
      (open.path === change.path || open.path.startsWith(`${change.path}/`))
    )
      setOpen(null);
  };
  const dropProps = canEdit
    ? {
        onDragOver: (event) => {
          if (!event.dataTransfer?.types?.includes("Files")) return;
          event.preventDefault();
          setDragging(true);
        },
        onDragLeave: () => setDragging(false),
        onDrop: (event) => {
          if (!event.dataTransfer?.files?.length) return;
          event.preventDefault();
          setDragging(false);
          void uploads.upload(dir, event.dataTransfer.files);
        },
      }
    : {};

  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: { xs: "1fr", md: "minmax(240px, 2fr) 3fr" },
        gap: 2,
        alignItems: "start",
      }}
    >
      <Box
        sx={{
          border: "1px solid",
          borderColor: dragging ? "primary.main" : "divider",
          outline: dragging ? "1px dashed" : "none",
          outlineColor: "primary.main",
          borderRadius: 1,
          minWidth: 0,
        }}
        data-testid="files-list"
        {...dropProps}
      >
        <Box
          sx={{
            px: 1.5,
            py: 1,
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: 1,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Breadcrumbs path={dir} onOpen={openDir} />
          {canEdit && !adding && (
            <FilesToolbar
              sandboxId={sandbox.id}
              dir={dir}
              uploads={uploads}
              onNewFile={() => setAdding(true)}
            />
          )}
        </Box>
        <UploadStatus uploads={uploads} />
        {adding && (
          <Box
            sx={{ p: 1.5, borderBottom: "1px solid", borderColor: "divider" }}
          >
            <NewFile
              dir={dir}
              onCancel={() => setAdding(false)}
              onCreate={(path) => {
                setAdding(false);
                setOpen({ path, isNew: true });
              }}
            />
          </Box>
        )}
        {listing.isLoading && (
          <Box sx={{ p: 1.5 }}>
            <LoadingRows rows={3} />
          </Box>
        )}
        {listing.error && (
          <SandboxErrorState
            compact
            error={listing.error}
            onRetry={() => listing.refetch()}
          />
        )}
        {!listing.isLoading && !listing.error && entries.length === 0 && (
          <Typography typography="s1" color="text.secondary" sx={{ p: 2 }}>
            {dir
              ? "This folder is empty."
              : canEdit
                ? "No files yet. Upload or create one here, or make one from the Run tab."
                : "No files yet."}
          </Typography>
        )}
        <Box component="ul" sx={{ listStyle: "none", m: 0, p: 0.5 }}>
          {entries.map((entry) => {
            const path = join(dir, entry.name);
            const selected = open?.path === path;
            return (
              <Box
                component="li"
                key={entry.name}
                sx={{ display: "flex", alignItems: "center", gap: 0.25 }}
              >
                <Box
                  component="button"
                  type="button"
                  data-testid="file-entry"
                  data-name={entry.name}
                  onClick={() =>
                    entry.directory
                      ? openDir(path)
                      : setOpen({ path, isNew: false, size: entry.size })
                  }
                  sx={{
                    all: "unset",
                    boxSizing: "border-box",
                    flex: 1,
                    minWidth: 0,
                    cursor: "pointer",
                    display: "flex",
                    alignItems: "center",
                    gap: 1,
                    px: 1,
                    py: 0.75,
                    borderRadius: 0.75,
                    bgcolor: selected ? "action.selected" : "transparent",
                    "&:hover, &:focus-visible": { bgcolor: "action.hover" },
                    "&:focus-visible": {
                      outline: (theme) =>
                        `2px solid ${theme.palette.border.focus}`,
                      outlineOffset: -2,
                    },
                  }}
                >
                  <SandboxIcon
                    name={entry.directory ? "folder" : "file"}
                    size={16}
                    sx={{ color: "text.secondary" }}
                  />
                  <Typography
                    typography="s1"
                    sx={{
                      flex: 1,
                      minWidth: 0,
                      fontFamily: "monospace",
                      fontSize: 13,
                    }}
                    noWrap
                  >
                    {entry.name}
                  </Typography>
                  <Typography
                    typography="s2"
                    color="text.secondary"
                    sx={{ flexShrink: 0 }}
                  >
                    {entry.directory ? "" : formatBytes(entry.size)}
                  </Typography>
                </Box>
                {(canEdit || (canDownload && !entry.directory)) && (
                  <FileRowMenu
                    sandboxId={sandbox.id}
                    entry={entry}
                    path={path}
                    canEdit={canEdit}
                    onDownload={download.download}
                    onChanged={onChanged}
                  />
                )}
              </Box>
            );
          })}
        </Box>
      </Box>
      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          p: 2,
          minWidth: 0,
        }}
      >
        {open ? (
          <FileViewer
            sandboxId={sandbox.id}
            path={open.path}
            isNew={open.isNew}
            canEdit={canEdit}
            canDownload={canDownload}
            size={open.size}
            download={download}
            onSaved={() => setOpen({ path: open.path, isNew: false })}
          />
        ) : (
          <Typography typography="s1" color="text.secondary">
            Choose a file to read
            {canEdit
              ? ", edit or download it. Upload files with Upload or by dropping them on the list. Changes made here are recorded in Activity."
              : canDownload
                ? " or download it."
                : " it."}
          </Typography>
        )}
      </Box>
    </Box>
  );
}

FilesTab.propTypes = {
  sandbox: PropTypes.object.isRequired,
  onResume: PropTypes.func,
  resuming: PropTypes.bool,
};
