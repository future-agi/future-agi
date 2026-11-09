// The folder rail of the dataset list (W9-SCOPE §5 A6): All datasets ·
// Starred · each folder with its own and subtree counts · No folder. A
// click sets one filter (folderRailModel); a folder (or No folder) takes a
// dragged dataset (FolderDnd). Each folder has ⋯ with Rename
// folder…, Move folder to…, Remove folder. Shown by the host only while
// at least one folder exists.
import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  IconButton,
  ListItemButton,
  Menu,
  MenuItem,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { railRows } from "./folderRailModel";
import { FolderDropZone } from "./FolderDnd";
import { datasetCaps } from "../realBackendCaps";

const Count = ({ n }) => (
  <Typography
    component="span"
    typography="s3"
    color="text.secondary"
    sx={{ fontVariantNumeric: "tabular-nums", ml: "auto", pl: 1 }}
  >
    {n}
  </Typography>
);
Count.propTypes = { n: PropTypes.number };

function RailRow({ label, count, selected, depth = 0, icon, onClick, menu }) {
  return (
    <Box sx={{ display: "flex", alignItems: "center", position: "relative" }}>
      <ListItemButton
        selected={selected}
        onClick={onClick}
        aria-current={selected ? "true" : undefined}
        sx={{
          borderRadius: 0.5,
          py: 0.25,
          pl: 1 + depth * 1.5,
          pr: menu ? 4 : 1,
          minHeight: 30,
          gap: 0.75,
        }}
      >
        <Iconify icon={icon} width={16} sx={{ color: "text.secondary" }} />
        <Typography component="span" typography="s2" noWrap>
          {label}
        </Typography>
        <Count n={count} />
      </ListItemButton>
      {menu}
    </Box>
  );
}
RailRow.propTypes = {
  label: PropTypes.string,
  count: PropTypes.number,
  selected: PropTypes.bool,
  depth: PropTypes.number,
  icon: PropTypes.string,
  onClick: PropTypes.func,
  menu: PropTypes.node,
};

/**
 * @param {{folders: {path, count, total}[], allCount: number,
 *   starredCount: number, noFolderCount: number,
 *   pick: {kind, path?}, onPick: (pick) => void,
 *   onAction?: (action: "rename"|"move"|"remove", path: string) => void,
 *   canEdit?: boolean}} props
 */
export default function FolderRail({
  folders,
  allCount,
  starredCount,
  noFolderCount,
  pick,
  onPick,
  onAction,
  canEdit = false,
}) {
  const [menu, setMenu] = useState(null); // {anchor, path}
  const rows = railRows(folders);
  const is = (kind, path) =>
    pick?.kind === kind && (kind !== "folder" || pick.path === path);
  return (
    <Box
      component="nav"
      aria-label="Folders"
      sx={{ display: "flex", flexDirection: "column", gap: 0.25, minWidth: 0 }}
    >
      <RailRow
        label="All datasets"
        count={allCount}
        icon="mdi:database-outline"
        selected={is("all")}
        onClick={() => onPick({ kind: "all" })}
      />
      {datasetCaps().listStars && (
        <RailRow
          label="Starred"
          count={starredCount}
          icon="mdi:star-outline"
          selected={is("starred")}
          onClick={() => onPick({ kind: "starred" })}
        />
      )}
      {rows.map((r) => (
        <FolderDropZone key={r.path} path={r.path}>
          <RailRow
            label={r.name}
            count={r.total}
            depth={r.depth - 1}
            icon="mdi:folder-outline"
            selected={is("folder", r.path)}
            onClick={() => onPick({ kind: "folder", path: r.path })}
            menu={
              canEdit && onAction ? (
                <IconButton
                  size="small"
                  aria-label={`Folder actions for ${r.path}`}
                  onClick={(e) =>
                    setMenu({ anchor: e.currentTarget, path: r.path })
                  }
                  sx={{ position: "absolute", right: 2, p: 0.25 }}
                >
                  <Iconify icon="mdi:dots-horizontal" width={16} />
                </IconButton>
              ) : null
            }
          />
        </FolderDropZone>
      ))}
      <FolderDropZone path="">
        <RailRow
          label="No folder"
          count={noFolderCount}
          icon="mdi:folder-off-outline"
          selected={is("none")}
          onClick={() => onPick({ kind: "none" })}
        />
      </FolderDropZone>
      <Menu
        open={Boolean(menu)}
        anchorEl={menu?.anchor}
        onClose={() => setMenu(null)}
      >
        {[
          ["rename", "Rename folder…"],
          ["move", "Move folder to…"],
          ["remove", "Remove folder"],
        ].map(([action, label]) => (
          <MenuItem
            key={action}
            sx={{ typography: "s2" }}
            onClick={() => {
              const path = menu.path;
              setMenu(null);
              onAction(action, path);
            }}
          >
            {label}
          </MenuItem>
        ))}
      </Menu>
    </Box>
  );
}

FolderRail.propTypes = {
  folders: PropTypes.arrayOf(
    PropTypes.shape({
      path: PropTypes.string,
      count: PropTypes.number,
      total: PropTypes.number,
    }),
  ).isRequired,
  allCount: PropTypes.number.isRequired,
  starredCount: PropTypes.number.isRequired,
  noFolderCount: PropTypes.number.isRequired,
  pick: PropTypes.shape({ kind: PropTypes.string, path: PropTypes.string }),
  onPick: PropTypes.func.isRequired,
  onAction: PropTypes.func,
  canEdit: PropTypes.bool,
};
