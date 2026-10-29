import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { MONO } from "../format";

// The tree, built from flat paths: folders first, then files, both by name.
export const buildTree = (files, subfolder) => {
  const root = { name: "", folders: new Map(), files: [] };
  const prefix = subfolder ? `${subfolder}/` : "";
  files.forEach((file) => {
    const relative = file.path.startsWith(prefix)
      ? file.path.slice(prefix.length)
      : file.path;
    const parts = relative.split("/");
    let node = root;
    parts.slice(0, -1).forEach((part) => {
      if (!node.folders.has(part)) {
        node.folders.set(part, { name: part, folders: new Map(), files: [] });
      }
      node = node.folders.get(part);
    });
    node.files.push({ ...file, name: parts[parts.length - 1] });
  });
  return root;
};

const STATUS_TONE = {
  added: "success.main",
  modified: "warning.main",
  removed: "error.main",
};

function Folder({ node, depth, selected, changes, onSelect, openByDefault }) {
  const [open, setOpen] = useState(openByDefault);
  const folders = [...node.folders.values()].sort((a, b) =>
    a.name.localeCompare(b.name),
  );
  const files = [...node.files].sort((a, b) => a.name.localeCompare(b.name));
  return (
    <>
      {node.name && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={0.5}
          role="button"
          tabIndex={0}
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              setOpen((v) => !v);
            }
          }}
          sx={{
            pl: depth * 1.5,
            py: 0.25,
            cursor: "pointer",
            color: "text.secondary",
            "&:hover": { color: "text.primary" },
          }}
        >
          <Iconify
            icon={
              open
                ? "solar:alt-arrow-down-linear"
                : "solar:alt-arrow-right-linear"
            }
            width={14}
          />
          <Typography sx={{ typography: "s2", fontFamily: MONO }}>
            {node.name}
          </Typography>
        </Stack>
      )}
      {open && (
        <>
          {folders.map((folder) => (
            <Folder
              key={folder.name}
              node={folder}
              depth={node.name ? depth + 1 : depth}
              selected={selected}
              changes={changes}
              onSelect={onSelect}
              openByDefault={false}
            />
          ))}
          {files.map((file) => {
            const status = changes[file.path];
            const isSelected = file.path === selected;
            return (
              <Stack
                key={file.path}
                direction="row"
                alignItems="center"
                spacing={0.75}
                role="button"
                tabIndex={0}
                aria-current={isSelected ? "true" : undefined}
                data-testid={`file-${file.path}`}
                onClick={() => onSelect(file.path)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onSelect(file.path);
                  }
                }}
                sx={{
                  pl: (node.name ? depth + 1 : depth) * 1.5 + 2,
                  pr: 1,
                  py: 0.25,
                  borderRadius: 0.75,
                  cursor: "pointer",
                  bgcolor: isSelected ? "action.selected" : "transparent",
                  "&:hover": {
                    bgcolor: isSelected ? "action.selected" : "action.hover",
                  },
                }}
              >
                <Typography
                  noWrap
                  sx={{
                    typography: "s2",
                    fontFamily: MONO,
                    color:
                      file.kind === "text" ? "text.primary" : "text.disabled",
                  }}
                >
                  {file.name}
                </Typography>
                {status && (
                  <Box
                    component="span"
                    aria-label={status}
                    sx={{
                      width: 6,
                      height: 6,
                      borderRadius: "50%",
                      bgcolor: STATUS_TONE[status],
                      flexShrink: 0,
                    }}
                  />
                )}
              </Stack>
            );
          })}
        </>
      )}
    </>
  );
}

Folder.propTypes = {
  node: PropTypes.object.isRequired,
  depth: PropTypes.number.isRequired,
  selected: PropTypes.string,
  changes: PropTypes.object.isRequired,
  onSelect: PropTypes.func.isRequired,
  openByDefault: PropTypes.bool.isRequired,
};

/** The files of a version as a folder tree; changed files carry a dot. */
export default function FileTree({
  files,
  subfolder,
  selected,
  changes,
  onSelect,
}) {
  const tree = useMemo(() => buildTree(files, subfolder), [files, subfolder]);
  return (
    <Box data-testid="file-tree" sx={{ overflowY: "auto", minHeight: 0 }}>
      <Folder
        node={tree}
        depth={0}
        selected={selected}
        changes={changes}
        onSelect={onSelect}
        openByDefault
      />
    </Box>
  );
}

FileTree.propTypes = {
  files: PropTypes.arrayOf(PropTypes.object).isRequired,
  subfolder: PropTypes.string,
  selected: PropTypes.string,
  changes: PropTypes.object.isRequired,
  onSelect: PropTypes.func.isRequired,
};
