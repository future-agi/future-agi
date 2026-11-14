import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  ButtonBase,
  Divider,
  InputBase,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import SvgColor from "src/components/svg-color";
import { GRAPH_NODES } from "../common";
import { useGraphStore } from "../store/graphStore";

const NodeHeader = ({ type, title, badge, nodeId, editable = false }) => {
  const renameNode = useGraphStore((state) => state.renameNode);
  const [isEditing, setIsEditing] = useState(false);
  const [editValue, setEditValue] = useState("");
  // Enter and Escape close the input, which can also fire blur; finish once.
  const isFinishedRef = useRef(true);

  const node = GRAPH_NODES.find((n) => n.type === type);
  if (!node) return null;
  const { color, backgroundColor, icon, name } = node;
  const canEdit = editable && Boolean(nodeId);
  const displayName = title || name;

  const startEditing = () => {
    if (!canEdit) return;
    isFinishedRef.current = false;
    setEditValue(title || "");
    setIsEditing(true);
  };

  const finishEditing = (shouldSave) => {
    if (isFinishedRef.current) return;
    isFinishedRef.current = true;
    setIsEditing(false);
    if (!shouldSave) return;

    const newName = editValue.trim();
    if (!newName || newName === title) return;
    if (!renameNode(nodeId, newName)) {
      enqueueSnackbar(`A node named "${newName}" already exists`, {
        variant: "error",
      });
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      finishEditing(true);
    } else if (e.key === "Escape") {
      e.preventDefault();
      // The builder is a dialog that closes on Escape; cancel only the rename.
      e.stopPropagation();
      finishEditing(false);
    }
  };

  return (
    <>
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: "12px",
        }}
      >
        <Box
          sx={{
            backgroundColor,
            padding: 1,
            borderRadius: "4px",
            width: "24px",
            height: "24px",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <SvgColor
            src={icon}
            sx={{
              width: "16px",
              height: "16px",
              color,
              flexShrink: 0,
            }}
          />
        </Box>
        {isEditing ? (
          <InputBase
            autoFocus
            className="nodrag"
            value={editValue}
            onChange={(e) => setEditValue(e.target.value)}
            onFocus={(e) => e.target.select()}
            onKeyDown={handleKeyDown}
            onBlur={() => finishEditing(true)}
            inputProps={{ "aria-label": "Node name" }}
            sx={{
              flex: 1,
              minWidth: 0,
              border: "1px solid",
              borderColor: "primary.main",
              borderRadius: "4px",
              "& .MuiInputBase-input": {
                padding: "2px 4px",
                typography: "s2",
                fontWeight: "fontWeightMedium",
                height: "auto",
              },
            }}
          />
        ) : canEdit ? (
          <ButtonBase
            disableRipple
            onClick={startEditing}
            aria-label={`Rename ${displayName}`}
            sx={{
              minWidth: 0,
              justifyContent: "flex-start",
              textAlign: "left",
              borderRadius: "4px",
              cursor: "text",
              "&.Mui-focusVisible": {
                outline: "2px solid",
                outlineColor: "primary.main",
                outlineOffset: "2px",
              },
            }}
          >
            <Typography
              component="span"
              typography="s2"
              fontWeight="fontWeightMedium"
            >
              {displayName}
            </Typography>
          </ButtonBase>
        ) : (
          <Typography typography="s2" fontWeight="fontWeightMedium">
            {displayName}
          </Typography>
        )}
        {badge && <Box sx={{ marginLeft: "auto" }}>{badge}</Box>}
      </Box>
      <Divider />
    </>
  );
};

NodeHeader.propTypes = {
  type: PropTypes.string,
  title: PropTypes.string,
  badge: PropTypes.node,
  nodeId: PropTypes.string,
  editable: PropTypes.bool,
};

export default NodeHeader;
