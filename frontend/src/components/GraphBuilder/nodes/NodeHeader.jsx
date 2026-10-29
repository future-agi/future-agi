import React, { useCallback, useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Divider, InputBase, Typography } from "@mui/material";
import SvgColor from "src/components/svg-color";
import { GRAPH_NODES } from "../common";
import { useGraphStore } from "../store/graphStore";

const NodeHeader = ({ id, type, title, badge }) => {
  const [isEditing, setIsEditing] = useState(false);
  const [editValue, setEditValue] = useState(title || "");
  const shouldSkipBlurSave = useRef(false);
  const renameNode = useGraphStore((state) => state.renameNode);
  const node = GRAPH_NODES.find((n) => n.type === type);
  const label = title || node?.name || "";

  useEffect(() => {
    if (!isEditing) {
      setEditValue(label);
    }
  }, [isEditing, label]);

  const handleSave = useCallback(() => {
    if (shouldSkipBlurSave.current) {
      shouldSkipBlurSave.current = false;
      return;
    }

    const nextValue = editValue.trim();

    setIsEditing(false);

    if (!id || !nextValue || nextValue === label) {
      setEditValue(label);
      return;
    }

    renameNode(id, nextValue);
  }, [editValue, id, label, renameNode]);

  const handleCancel = useCallback(() => {
    shouldSkipBlurSave.current = true;
    setEditValue(label);
    setIsEditing(false);
  }, [label]);

  const handleKeyDown = useCallback(
    (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        handleSave();
      }

      if (event.key === "Escape") {
        event.preventDefault();
        handleCancel();
      }
    },
    [handleCancel, handleSave],
  );

  const handleEditStart = useCallback(
    (event) => {
      event.stopPropagation();
      setEditValue(label);
      setIsEditing(true);
    },
    [label],
  );

  if (!node) return null;
  const { color, backgroundColor, icon } = node;

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
        {isEditing ? (
          <InputBase
            autoFocus
            className="nodrag"
            value={editValue}
            onBlur={handleSave}
            onChange={(event) => setEditValue(event.target.value)}
            onClick={(event) => event.stopPropagation()}
            onFocus={(event) => event.target.select()}
            onKeyDown={handleKeyDown}
            onMouseDown={(event) => event.stopPropagation()}
            sx={{
              flex: 1,
              minWidth: 0,
              border: "1px solid",
              borderColor: "primary.main",
              borderRadius: 0.5,
              px: 0.75,
              py: 0.25,
              "& .MuiInputBase-input": {
                typography: "s2",
                fontWeight: "fontWeightMedium",
                p: 0,
              },
            }}
          />
        ) : (
          <Typography
            className="nodrag"
            typography="s2"
            fontWeight="fontWeightMedium"
            onClick={handleEditStart}
            onMouseDown={(event) => event.stopPropagation()}
            sx={{
              cursor: "text",
              flex: 1,
              minWidth: 0,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {label}
          </Typography>
        )}
        {badge && <Box sx={{ marginLeft: "auto" }}>{badge}</Box>}
      </Box>
      <Divider />
    </>
  );
};

NodeHeader.propTypes = {
  id: PropTypes.string,
  type: PropTypes.string,
  title: PropTypes.string,
  badge: PropTypes.node,
};

export default NodeHeader;
