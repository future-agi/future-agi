import {
  Box,
  Chip,
  Divider,
  Popper,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useCallback, useMemo, useRef, useState } from "react";
import Iconify from "src/components/iconify";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { useAuthContext } from "src/auth/hooks";
import { getColorMap } from "../common";
import LabelPopperContent from "./LabelPopperContent";
import { LoadingButton } from "@mui/lab";

const LabelSelectContent = ({
  promptId,
  versionId,
  selectedLabels,
  labels,
  onSuccess,
  onClose,
  version,
  isPending,
  isFetchingNextPage,
  fetchNextPage,
}) => {
  const anchorRef = useRef(null);
  const [open, setOpen] = useState(false);
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [newLabelName, setNewLabelName] = useState("");
  const theme = useTheme();
  const queryClient = useQueryClient();
  const { role: userRole } = useAuthContext();

  // initialize state directly on mount
  const [selectedLabelsList, setSelectedLabelsList] = useState(() => {
    if (selectedLabels && selectedLabels.length > 0 && labels.length > 0) {
      return selectedLabels.map((selectedLabel) => {
        const fullLabel = labels.find((l) => l.id === selectedLabel.id);
        return fullLabel || selectedLabel;
      });
    }
    return [];
  });

  const originalLabelIds = new Set((selectedLabels ?? []).map((l) => l.id));
  const hasChanges =
    selectedLabelsList.length !== originalLabelIds.size ||
    selectedLabelsList.some((l) => !originalLabelIds.has(l.id));

  const { mutate: assignLabel, isPending: isAssigning } = useMutation({
    mutationFn: (labelIds) =>
      axios.post(endpoints.develop.runPrompt.assignMultipleLabels, {
        template_version_id: versionId,
        label_ids: labelIds,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["prompt-versions", promptId],
      });
      queryClient.invalidateQueries({
        queryKey: ["prompt-latest-version", promptId],
      });
      enqueueSnackbar("Labels updated successfully!", { variant: "success" });
      setOpen(false);
      setSelectedLabelsList([]);
      onClose?.();
      onSuccess?.();
    },
  });

  const { mutate: createLabel, isPending: isCreatingLabel } = useMutation({
    mutationFn: (data) =>
      axios.post(endpoints.develop.runPrompt.createPromptLabel, {
        ...data,
        type: "custom",
      }),
    onSuccess: (response) => {
      const newLabel = response.data;
      if (newLabel) {
        setSelectedLabelsList((prev) => [...prev, newLabel]);
      }
      queryClient.invalidateQueries({
        queryKey: ["prompt-labels"],
        type: "all",
      });
      enqueueSnackbar("Label created successfully!", { variant: "success" });
      setShowCreateForm(false);
      setNewLabelName("");
    },
  });

  const handleSelect = useCallback((label) => {
    setSelectedLabelsList((prev) => {
      const isAlreadySelected = prev.some((l) => l.id === label.id);
      if (isAlreadySelected) {
        return prev.filter((l) => l.id !== label.id);
      }
      return [...prev, label];
    });
  }, []);

  const hasDeployPermission = useMemo(
    () => RolePermission.PROMPTS[PERMISSIONS.DEPLOY][userRole],
    [userRole],
  );

  const handleSave = useCallback(() => {
    const labelIds = selectedLabelsList.map((label) => label.id);
    assignLabel(labelIds);
  }, [selectedLabelsList, assignLabel]);

  const handleCreateLabel = useCallback(() => {
    if (!newLabelName.trim()) {
      return enqueueSnackbar("Please enter a label name", {
        variant: "warning",
      });
    }
    createLabel({ name: newLabelName.trim() });
  }, [newLabelName, createLabel]);

  const isLabelSelected = useCallback(
    (labelId) => selectedLabelsList.some((l) => l.id === labelId),
    [selectedLabelsList],
  );

  return (
    <>
      <Divider orientation="horizontal" sx={{ marginX: "-12px", mb: 2 }} />

      <Box sx={{ position: "relative" }}>
        <Box
          ref={anchorRef}
          onClick={() => setOpen((prev) => !prev)}
          sx={{
            position: "relative",
            display: "flex",
            alignItems: "center",
            flexWrap: "wrap",
            gap: 0.5,
            minHeight: "40px",
            padding: "8px 40px 8px 14px",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "4px",
            transition: "border-color 0.2s",
            "&:hover": {
              borderColor: "divider",
            },
          }}
        >
          <Typography
            typography={"s3"}
            fontWeight={"fontWeightMedium"}
            color={"text.disabled"}
            sx={{
              position: "absolute",
              top: selectedLabelsList.length > 0 ? -8 : 11,
              left: 14,
              backgroundColor: "background.paper",
              padding: "0 4px",
              color: "text.secondary",
              fontSize: selectedLabelsList.length > 0 ? "12px" : "14px",
              transition: "all 0.2s",
              pointerEvents: "none",
            }}
          >
            Select Tags
          </Typography>

          {selectedLabelsList.map((label) => {
            const colorMap = getColorMap(label?.name, theme);
            return (
              <Chip
                key={label.id}
                label={label.name}
                size="small"
                disabled={!hasDeployPermission}
                onDelete={
                  hasDeployPermission ? () => handleSelect(label) : undefined
                }
                deleteIcon={
                  <Iconify
                    icon="mdi:close"
                    sx={{
                      width: 14,
                      height: 14,
                    }}
                  />
                }
                sx={{
                  backgroundColor: colorMap?.backgroundColor,
                  color: colorMap?.color,
                  borderRadius: "100px",
                  zIndex: 10,
                  "&:hover": {
                    backgroundColor: colorMap?.hoverBackgroundColor,
                  },
                  "& .MuiChip-deleteIcon": {
                    color: colorMap?.color,
                  },
                  typography: "s2_1",
                  fontWeight: "fontWeightMedium",
                }}
              />
            );
          })}

          <Box sx={{ flex: 1, minWidth: "50px" }} />

          <Iconify
            icon="eva:arrow-ios-upward-fill"
            sx={{
              position: "absolute",
              right: 12,
              top: "50%",
              transform: `translateY(-50%) rotateX(${open ? 0 : -180}deg)`,
              cursor: hasDeployPermission ? "pointer" : "default",
              color: hasDeployPermission ? "text.primary" : "text.disabled",
              width: 20,
              height: 20,
              transition: "transform 0.3s",
            }}
            onClick={(e) => {
              if (!hasDeployPermission) return;
              e.stopPropagation();
              setOpen((prev) => !prev);
            }}
          />
        </Box>

        <Popper
          open={open}
          anchorEl={anchorRef.current}
          placement="bottom-start"
          style={{
            width: anchorRef.current?.offsetWidth || 467,
            zIndex: 1300,
          }}
        >
          <LabelPopperContent
            labels={labels}
            isPending={isPending}
            isFetchingNextPage={isFetchingNextPage}
            isLabelSelected={isLabelSelected}
            handleSelect={handleSelect}
            showCreateForm={showCreateForm}
            setShowCreateForm={setShowCreateForm}
            newLabelName={newLabelName}
            setNewLabelName={setNewLabelName}
            handleCreateLabel={handleCreateLabel}
            isCreatingLabel={isCreatingLabel}
            onClose={() => setOpen(false)}
            version={version}
            fetchNextPage={fetchNextPage}
          />
        </Popper>
      </Box>

      <LoadingButton
        disabled={!hasChanges}
        loading={isAssigning}
        color="primary"
        variant="contained"
        onClick={handleSave}
        fullWidth
      >
        Save
      </LoadingButton>
    </>
  );
};

LabelSelectContent.propTypes = {
  promptId: PropTypes.string.isRequired,
  versionId: PropTypes.string,
  selectedLabels: PropTypes.array,
  labels: PropTypes.array.isRequired,
  onSuccess: PropTypes.func,
  onClose: PropTypes.func,
  version: PropTypes.oneOfType([PropTypes.string, PropTypes.object]).isRequired,
  isPending: PropTypes.bool,
  isFetchingNextPage: PropTypes.bool,
  fetchNextPage: PropTypes.func,
};

export default LabelSelectContent;
