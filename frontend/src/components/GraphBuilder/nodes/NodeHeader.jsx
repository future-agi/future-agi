import React, { useState, useCallback } from "react";
import PropTypes from "prop-types";
import { Box, Divider, InputBase, Typography } from "@mui/material";
import SvgColor from "src/components/svg-color";
import { GRAPH_NODES } from "../common";
import { useGraphStore } from "../store/graphStore";

const EditableNodeTitle = ({ nodeId, currentTitle }) => {
  const [isEditing, setIsEditing] = useState(false);
  const [editValue, setEditValue] = useState(currentTitle || "");
  const updateNodeData = useGraphStore((state) => state.updateNodeData);

  const handleSave = useCallback(() => {
    const newName = editValue.trim();
    setIsEditing(false);

    if (newName && newName !== currentTitle) {
      updateNodeData(nodeId, { name: newName });
    } else {
      setEditValue(currentTitle);
    }
  }, [editValue, currentTitle, nodeId, updateNodeData]);

  const handleKeyDown = useCallback(
    (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        handleSave();
      } else if (e.key === "Escape") {
        e.preventDefault();
        setEditValue(currentTitle);
        setIsEditing(false);
      }
    },
    [handleSave, currentTitle],
  );

  const handleClick = useCallback(() => {
    setEditValue(currentTitle);
    setIsEditing(true);
  }, [currentTitle]);

  if (isEditing) {
    return (
      <InputBase
        autoFocus
        value={editValue}
        onChange={(e) => setEditValue(e.target.value)}
        onKeyDown={handleKeyDown}
        onBlur={handleSave}
        sx={{
          typography: "s2",
          fontWeight: "fontWeightMedium",
          border: "1px solid",
          borderColor: "primary.main",
          borderRadius: 0.5,
          px: 0.5,
        }}
      />
    );
  }

  return (
    <Typography
      typography="s2"
      fontWeight="fontWeightMedium"
      onClick={handleClick}
      sx={{ cursor: "pointer" }}
    >
      {currentTitle}
    </Typography>
  );
};

EditableNodeTitle.propTypes = {
  nodeId: PropTypes.string.isRequired,
  currentTitle: PropTypes.string,
};

const NodeHeader = ({ type, title, nodeId }) => {
  const node = GRAPH_NODES.find((n) => n.type === type);
  if (!node) return null;
  const { color, backgroundColor, icon, name } = node;
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
            borderRadius: "2px",
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
        <EditableNodeTitle nodeId={nodeId} currentTitle={title || name} />
      </Box>
      <Divider />
    </>
  );
};

NodeHeader.propTypes = {
  type: PropTypes.string,
  title: PropTypes.string,
  nodeId: PropTypes.string.isRequired,
};

export default NodeHeader;
