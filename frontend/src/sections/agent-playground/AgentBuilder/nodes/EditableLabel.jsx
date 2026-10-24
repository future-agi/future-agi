import React, { useState, useCallback } from "react";
import { Box, InputBase, Typography } from "@mui/material";
import PropTypes from "prop-types";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import { useUpdatePort } from "src/api/agent-playground/agent-playground";
import {
  useAgentPlaygroundStoreShallow,
  useAgentPlaygroundStore,
} from "../../store";
import { useSaveDraftContext } from "../saveDraftContext";
import { useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import logger from "src/utils/logger";

const labelBaseSx = {
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

export default function EditableLabel({
  nodeId,
  portId,
  label,
  preview,
  sx = {},
}) {
  const [isEditing, setIsEditing] = useState(false);
  const [editValue, setEditValue] = useState(label || "");
  const [hasError, setHasError] = useState(false);

  const renameOutputPort = useAgentPlaygroundStoreShallow(
    (s) => s.renameOutputPort,
  );

  const { mutateAsync } = useUpdatePort();
  const { ensureDraft } = useSaveDraftContext();
  const queryClient = useQueryClient();

  const handleSave = useCallback(async () => {
    const newName = editValue.trim();
    setIsEditing(false);

    if (!newName || newName === label) {
      setEditValue(label);
      return;
    }

    // Always apply optimistic rename first
    renameOutputPort(nodeId, newName);

    const draftResult = await ensureDraft();

    if (draftResult === false) {
      // Rollback
      renameOutputPort(nodeId, label);
      return;
    }

    if (draftResult === "created") {
      // Rename was included in the POST. Done!
      return;
    }

    // Already a draft — fire individual API call
    const { currentAgent } = useAgentPlaygroundStore.getState();
    mutateAsync({
      graphId: currentAgent?.id,
      versionId: currentAgent?.version_id,
      portId,
      data: { display_name: newName },
    })
      .then(() => {
        queryClient.invalidateQueries({
          queryKey: ["agent-playground", "possible-edge-mappings"],
        });
      })
      .catch((error) => {
        logger.error("[EditableLabel] updatePort failed", error);
        renameOutputPort(nodeId, label);
        enqueueSnackbar("Failed to rename port", { variant: "error" });
      });
  }, [
    editValue,
    label,
    nodeId,
    portId,
    renameOutputPort,
    mutateAsync,
    ensureDraft,
    queryClient,
  ]);

  const handleKeyDown = useCallback(
    (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        handleSave();
      } else if (e.key === "Escape") {
        e.preventDefault();
        setEditValue(label);
        setHasError(false);
        setIsEditing(false);
      }
    },
    [handleSave, label],
  );

  const handleClick = useCallback(() => {
    if (preview) return;
    setEditValue(label);
    setIsEditing(true);
  }, [label, preview]);

  const handleBlur = useCallback(() => {
    setEditValue(label);
    setHasError(false);
    setIsEditing(false);
  }, [label]);

  if (isEditing) {
    return (
      <Box
        sx={{
          ...labelBaseSx,
          ...sx,
          overflow: "visible",
          textOverflow: "clip",
        }}
      >
        <InputBase
          autoFocus
          onFocus={(e) => e.target.select()}
          value={editValue}
          onChange={(e) => {
            const raw = e.target.value;
            const hasForbidden = /[[\].]/.test(raw);
            setHasError(hasForbidden);
            setEditValue(raw.replace(/[[\].]/g, ""));
          }}
          onKeyDown={handleKeyDown}
          onBlur={handleBlur}
          sx={{
            position: "relative",
            transform: "none",
            minWidth: 80,
            maxWidth: 160,
            border: "1px solid",
            borderColor: hasError ? "error.main" : "primary.main",
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
        {hasError && (
          <Typography
            variant="caption"
            color="error"
            sx={{ mt: 0.25, whiteSpace: "nowrap", display: "block" }}
          >
            Characters [ ] . are not allowed
          </Typography>
        )}
      </Box>
    );
  }

  return (
    <CustomTooltip show title={label} size="small" arrow>
      <Typography
        onClick={handleClick}
        sx={{
          ...labelBaseSx,
          ...sx,
        }}
      >
        {label}
      </Typography>
    </CustomTooltip>
  );
}

EditableLabel.propTypes = {
  nodeId: PropTypes.string.isRequired,
  portId: PropTypes.string,
  label: PropTypes.string,
  preview: PropTypes.bool,
  sx: PropTypes.object,
};
