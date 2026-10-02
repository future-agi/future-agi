import {
  Box,
  Button,
  Chip,
  Divider,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useCallback, useMemo, useState } from "react";
import Iconify from "src/components/iconify";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { useAuthContext } from "src/auth/hooks";
import SvgColor from "src/components/svg-color";
import { LoadingButton } from "@mui/lab";
import { getColorMap } from "../common";
import LabelList from "./LabelList";
import CreateTagInput from "./CreateTagInput";

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
      // Keep the create input open for back-to-back adds.
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
    if (selectedLabelsList.length === 0) {
      return enqueueSnackbar("Please select at least one label", {
        variant: "warning",
      });
    }
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

  const handleCancelCreate = useCallback(() => {
    setShowCreateForm(false);
    setNewLabelName("");
  }, []);

  const isLabelSelected = useCallback(
    (labelId) => selectedLabelsList.some((l) => l.id === labelId),
    [selectedLabelsList],
  );

  return (
    <>
      <Divider orientation="horizontal" sx={{ marginX: "-12px", mb: 1.5 }} />

      <Box
        sx={{
          position: "relative",
          display: "flex",
          alignItems: "center",
          flexWrap: "wrap",
          gap: 0.5,
          minHeight: "40px",
          padding: "5px 12px",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "6px",
        }}
      >
        <Typography
          typography={"s3"}
          fontWeight={"fontWeightMedium"}
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
          Selected Tags
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
      </Box>

      <LabelList
        labels={labels}
        isPending={isPending}
        isFetchingNextPage={isFetchingNextPage}
        isLabelSelected={isLabelSelected}
        handleSelect={handleSelect}
        version={version}
        fetchNextPage={fetchNextPage}
      />

      {showCreateForm && (
        <CreateTagInput
          newLabelName={newLabelName}
          setNewLabelName={setNewLabelName}
          handleCreateLabel={handleCreateLabel}
          isCreatingLabel={isCreatingLabel}
          onCancel={handleCancelCreate}
        />
      )}

      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 1,
          mt: 1.25,
        }}
      >
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          disabled={showCreateForm || !hasDeployPermission}
          onClick={() => setShowCreateForm(true)}
          startIcon={
            <SvgColor
              src="/assets/icons/ic_add.svg"
              sx={{ width: 16, height: 16 }}
            />
          }
        >
          <Typography typography="s3" fontWeight={500}>
            Add custom tag
          </Typography>
        </Button>

        <LoadingButton
          size="small"
          disabled={selectedLabelsList?.length === 0}
          loading={isAssigning}
          color="primary"
          variant="contained"
          onClick={handleSave}
        >
          Save
        </LoadingButton>
      </Box>
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
