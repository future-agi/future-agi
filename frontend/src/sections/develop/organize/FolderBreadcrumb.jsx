// The folder breadcrumb in the list toolbar (W9-SCOPE §5 A6):
// `All datasets / support / billing`; a segment click selects that folder
// (the same filter as the rail). Shown while a folder is picked.
import React from "react";
import PropTypes from "prop-types";
import { Box, Link, Typography } from "@mui/material";
import { breadcrumbOf } from "./folderTree";
import { FolderDropZone } from "./FolderDnd";

export default function FolderBreadcrumb({ path, onPick }) {
  const crumbs = breadcrumbOf(path);
  return (
    <Box
      component="nav"
      aria-label="Folder"
      sx={{ display: "flex", alignItems: "center", gap: 0.5, minWidth: 0 }}
    >
      {crumbs.map((c, i) => {
        const last = i === crumbs.length - 1;
        return (
          <React.Fragment key={c.path || "all"}>
            {i > 0 && (
              <Typography typography="s2" color="text.disabled">
                /
              </Typography>
            )}
            {last ? (
              <Typography
                typography="s2"
                fontWeight={600}
                noWrap
                aria-current="location"
              >
                {c.label}
              </Typography>
            ) : c.path ? (
              <FolderDropZone path={c.path}>
                <Link
                  component="button"
                  type="button"
                  typography="s2"
                  color="text.secondary"
                  underline="hover"
                  noWrap
                  onClick={() => onPick({ kind: "folder", path: c.path })}
                >
                  {c.label}
                </Link>
              </FolderDropZone>
            ) : (
              <Link
                component="button"
                type="button"
                typography="s2"
                color="text.secondary"
                underline="hover"
                noWrap
                onClick={() =>
                  onPick(
                    c.path ? { kind: "folder", path: c.path } : { kind: "all" },
                  )
                }
              >
                {c.label}
              </Link>
            )}
          </React.Fragment>
        );
      })}
    </Box>
  );
}

FolderBreadcrumb.propTypes = {
  path: PropTypes.string.isRequired,
  onPick: PropTypes.func.isRequired,
};
