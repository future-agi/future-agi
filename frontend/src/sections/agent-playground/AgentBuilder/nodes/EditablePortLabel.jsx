import React, { useState, useCallback } from "react";
import { InputBase, Typography } from "@mui/material";
import PropTypes from "prop-types";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import { useAgentPlaygroundStoreShallow } from "../../store";
import { useSaveDraftContext } from "../saveDraftContext";
import { enqueueSnackbar } from "notistack";

const portLabelBaseSx = {
  position: "absolute",
  transform: "translateY(-50%)",
  typography: "s1",
  color: "text.primary",
  whiteSpace: "nowrap",
  overflow: "hidden",
  textOverflow: "ellipsis",
  maxWidth: 100,
  backgroundColor: "background.paper",
  py: 0.25,
  px: 0.5,
  zIndex: 20,
  cursor: "pointer",
  pointerEvents: "auto",
};

export default function EditablePortLabel({ nodeId, port, preview, sx = {} }) {
  const [isEditing, setIsEditing] = useState(false);
  const [editValue, setEditValue] = useState(port.display_name || "");

  const renamePortDisplayName = useAgentPlaygroundStoreShallow(
    (s) => s.renamePortDisplayName,
  );

  const { saveDraft } = useSaveDraftContext();

  const handleSave = useCallback(() => {
    const newName = editValue.trim();
    const oldName = port.display_name;
    setIsEditing(false);

    if (!newName || newName === oldName) {
      setEditValue(oldName);
      return;
    }

    // Optimistic update
    renamePortDisplayName(nodeId, port.id, newName);

    saveDraft({
      onError: () => {
        renamePortDisplayName(nodeId, port.id, oldName);
        enqueueSnackbar("Failed to rename port", { variant: "error" });
      },
    });
  }, [
    editValue,
    port.display_name,
    port.id,
    nodeId,
    renamePortDisplayName,
    saveDraft,
  ]);

  const handleKeyDown = useCallback(
    (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        handleSave();
      } else if (e.key === "Escape") {
        e.preventDefault();
        setEditValue(port.display_name);
        setIsEditing(false);
      }
    },
    [handleSave, port.display_name],
  );

  const handleClick = useCallback(() => {
    if (preview) return;
    setEditValue(port.display_name);
    setIsEditing(true);
  }, [port.display_name, preview]);

  const handleBlur = useCallback(() => {
    setEditValue(port.display_name);
    setIsEditing(false);
  }, [port.display_name]);

  const displayName = port.display_name || "";

  if (isEditing) {
    return (
      <InputBase
        autoFocus
        onFocus={(e) => e.target.select()}
        value={editValue}
        onChange={(e) => setEditValue(e.target.value)}
        onKeyDown={handleKeyDown}
        onBlur={handleBlur}
        sx={{
          ...portLabelBaseSx,
          ...sx,
          overflow: "visible",
          textOverflow: "clip",
          minWidth: 80,
          maxWidth: 160,
          border: "1px solid",
          borderColor: "primary.main",
          borderRadius: "4px",
          "& .MuiInputBase-input": {
            padding: "2px 4px",
            typography: "s1",
            color: "text.primary",
            height: "auto",
            lineHeight: "inherit",
          },
        }}
      />
    );
  }

  return (
    <CustomTooltip show title={displayName} size="small" arrow>
      <Typography
        onClick={handleClick}
        sx={{
          ...portLabelBaseSx,
          ...sx,
        }}
      >
        {displayName}
      </Typography>
    </CustomTooltip>
  );
}

EditablePortLabel.propTypes = {
  nodeId: PropTypes.string.isRequired,
  port: PropTypes.shape({
    id: PropTypes.string,
    display_name: PropTypes.string,
    displayName: PropTypes.string,
  }).isRequired,
  preview: PropTypes.bool,
  sx: PropTypes.object,
};
