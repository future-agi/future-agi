// R4 (lane M): Data grid cells whose value is media, a call or a persona
// render through MediaValue (`size="cell"`): image strips with a hover
// preview and the lightbox, a mini waveform that plays on click, PDF and
// video chips, call chips, persona chips. The cell keeps its edit pencil
// (hover) where the old renderer had one. `mediaCellKind` says whether a
// value takes this path; anything else renders exactly as before.
import React from "react";
import PropTypes from "prop-types";
import { Box, IconButton } from "@mui/material";
import Iconify from "src/components/iconify";
import MediaValue from "src/sections/common/Media/MediaValue";
import { mediaCellKind } from "src/sections/common/Media/mediaModel";
import { DataTypes } from "./cellRendererHelper";

const COLUMN_TYPE = {
  [DataTypes.IMAGE]: "image",
  [DataTypes.IMAGES]: "images",
  [DataTypes.AUDIO]: "audio",
  [DataTypes.VIDEO]: "video",
  [DataTypes.FILE]: "document",
};

// Pure rule in Media/mediaModel.js (tested there); re-exported for callers.
export { mediaCellKind };

export default function MediaCellRenderer({
  value,
  dataType,
  dense = false,
  editable = false,
  onEditCell,
}) {
  return (
    <Box
      data-media-cell=""
      // A click on a cell's own control (play, thumb, chip) must not also
      // open the row: the data grid skips its row click after this flag
      // (the grid's existing `__imageClick` convention). Capture runs
      // before AG Grid's own cell listener.
      onClickCapture={(e) => {
        if (e.target?.closest?.("button, a, [role=button]"))
          window.__imageClick = true;
      }}
      sx={{
        position: "relative",
        display: "flex",
        alignItems: "center",
        height: "100%",
        px: 1,
        minWidth: 0,
        overflow: "hidden",
        "& .fa-media-edit": { opacity: 0 },
        "&:hover .fa-media-edit, & .fa-media-edit:focus-visible": {
          opacity: 1,
        },
      }}
    >
      <MediaValue
        value={value}
        size="cell"
        columnType={COLUMN_TYPE[dataType]}
        dense={dense}
      />
      {editable && onEditCell && COLUMN_TYPE[dataType] ? (
        <IconButton
          size="small"
          className="fa-media-edit"
          aria-label="Edit"
          onClick={(e) => {
            e.stopPropagation();
            onEditCell();
          }}
          sx={{
            position: "absolute",
            right: 4,
            top: "50%",
            transform: "translateY(-50%)",
            bgcolor: "background.paper",
          }}
        >
          <Iconify icon="mdi:pencil-outline" width={14} />
        </IconButton>
      ) : null}
    </Box>
  );
}

MediaCellRenderer.propTypes = {
  value: PropTypes.any,
  dataType: PropTypes.string,
  dense: PropTypes.bool,
  editable: PropTypes.bool,
  onEditCell: PropTypes.func,
};
