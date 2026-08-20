import React, { useEffect, useRef, useState } from "react";
import {
  Box,
  Button,
  Checkbox,
  Chip,
  Drawer,
  IconButton,
  InputAdornment,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  useAnnotationLabelsList,
  annotationLabelKeys,
} from "src/api/annotation-labels/annotation-labels";
import {
  useGetOrCreateDefaultQueue,
  useAddLabelToQueue,
  useRemoveLabelFromQueue,
} from "src/api/annotation-queues/annotation-queues";
import { useQueryClient } from "@tanstack/react-query";
import PropTypes from "prop-types";
import CreateLabelDrawer from "src/sections/annotations/labels/create-label-drawer";
import LabelTypeChip from "src/components/label-type-chip/LabelTypeChip";



const AddLabelDrawerContent = ({
  projectId,
  datasetId,
  agentDefinitionId,
  onClose,
  onLabelsChanged,
}) => {
  const [search, setSearch] = useState("");
  const [defaultQueue, setDefaultQueue] = useState(null);
  const [queueLabelIds, setQueueLabelIds] = useState(new Set());
  const [saving, setSaving] = useState(false);
  const [createLabelOpen, setCreateLabelOpen] = useState(false);
  const queryClient = useQueryClient();

  const { data: labelsData } = useAnnotationLabelsList({
    search: "",
    limit: 100,
  });
  const allLabels = labelsData?.results || [];

  const getOrCreateDefault = useGetOrCreateDefaultQueue();
  const addLabelMutation = useAddLabelToQueue();
  const removeLabelMutation = useRemoveLabelFromQueue();

  const scopeId = projectId || datasetId || agentDefinitionId;
  const hasFetchedRef = useRef(false);

  // Get or create default queue on mount — guarded to run only once
  useEffect(() => {
    if (scopeId && !defaultQueue && !hasFetchedRef.current) {
      hasFetchedRef.current = true;
      getOrCreateDefault.mutate(
        { projectId, datasetId, agentDefinitionId },
        {
          onSuccess: (response) => {
            const result = response.data?.result || response.data;
            const queue = result?.queue;
            if (queue) setDefaultQueue(queue);
            const existingIds = new Set(
              (result?.labels || []).map((l) => l.id),
            );
            setQueueLabelIds(existingIds);
          },
        },
      );
    }
  }, [scopeId, defaultQueue, projectId, datasetId, agentDefinitionId]);

  const filteredLabels = search
    ? allLabels.filter((l) =>
        l.name?.toLowerCase().includes(search.toLowerCase()),
      )
    : allLabels;

  const handleToggle = async (labelId) => {
    if (!defaultQueue?.id) return;

    setSaving(true);
    try {
      if (queueLabelIds.has(labelId)) {
        await removeLabelMutation.mutateAsync({
          queueId: defaultQueue.id,
          labelId,
        });
        setQueueLabelIds((prev) => {
          const next = new Set(prev);
          next.delete(labelId);
          return next;
        });
      } else {
        await addLabelMutation.mutateAsync({
          queueId: defaultQueue.id,
          labelId,
        });
        setQueueLabelIds((prev) => new Set([...prev, labelId]));
      }
      onLabelsChanged?.();
    } finally {
      setSaving(false);
    }
  };

  const selectedLabels = allLabels.filter((l) => queueLabelIds.has(l.id));

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
      }}
    >
      {/* Header */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          p: 2,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography variant="subtitle1" fontWeight="fontWeightSemiBold">
          Add Labels
        </Typography>
        <IconButton onClick={onClose} size="small">
          <Iconify icon="akar-icons:cross" width={18} />
        </IconButton>
      </Box>

      {/* Content */}
      <Box
        sx={{
          p: 2,
          display: "flex",
          flexDirection: "column",
          gap: 2,
          flex: 1,
          overflow: "auto",
        }}
      >
        <Typography variant="body2" color="text.secondary">
          Select labels to add to the default annotation queue. All team members
          can annotate using these labels.
        </Typography>

        {/* Selected labels */}
        {selectedLabels.length > 0 && (
          <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5 }}>
            {selectedLabels.map((label) => (
              <Chip
                key={label.id}
                label={label.name}
                size="small"
                onDelete={() => handleToggle(label.id)}
                sx={{ borderRadius: 1 }}
              />
            ))}
          </Box>
        )}

        {/* Search */}
        <TextField
          size="small"
          fullWidth
          placeholder="Search labels..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Iconify
                  icon="eva:search-fill"
                  sx={{ color: "text.disabled", width: 16, height: 16 }}
                />
              </InputAdornment>
            ),
          }}
        />

        {/* Label list */}
        <Box
          sx={{
            flex: 1,
            overflow: "auto",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
          }}
        >
          {getOrCreateDefault.isPending ? (
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ p: 2, textAlign: "center" }}
            >
              Loading...
            </Typography>
          ) : (
            filteredLabels.map((label) => (
              <Box
                key={label.id}
                onClick={() => !saving && handleToggle(label.id)}
                sx={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  px: 1,
                  py: 0.5,
                  cursor: saving ? "wait" : "pointer",
                  borderBottom: "1px solid",
                  borderColor: "divider",
                  "&:last-child": { borderBottom: 0 },
                  "&:hover": { bgcolor: "action.hover" },
                }}
              >
                <Box
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    gap: 1,
                    minWidth: 0,
                  }}
                >
                  <Checkbox
                    checked={queueLabelIds.has(label.id)}
                    size="small"
                    sx={{ p: 0.5 }}
                  />
                  <Typography variant="body2" noWrap>
                    {label.name}
                  </Typography>
                </Box>
                <LabelTypeChip type={label.type} />
              </Box>
            ))
          )}
          {!getOrCreateDefault.isPending && filteredLabels.length === 0 && (
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ p: 2, textAlign: "center" }}
            >
              No labels found
            </Typography>
          )}
        </Box>

        {/* Create new label */}
        <Button
          size="small"
          startIcon={<Iconify icon="mingcute:add-line" width={16} />}
          onClick={() => setCreateLabelOpen(true)}
          sx={{ alignSelf: "flex-start", fontSize: 12 }}
        >
          Create new label
        </Button>
        <CreateLabelDrawer
          open={createLabelOpen}
          onClose={() => {
            setCreateLabelOpen(false);
            // Refresh the org-wide labels list so the new label appears
            queryClient.invalidateQueries({
              queryKey: annotationLabelKeys.all,
            });
          }}
        />
      </Box>

      {/* Footer */}
      <Box
        sx={{
          p: 2,
          borderTop: "1px solid",
          borderColor: "divider",
          display: "flex",
          justifyContent: "flex-end",
          gap: 1,
        }}
      >
        <Button variant="outlined" size="small" onClick={onClose}>
          Next
        </Button>
      </Box>
    </Box>
  );
};

AddLabelDrawerContent.propTypes = {
  projectId: PropTypes.string,
  datasetId: PropTypes.string,
  agentDefinitionId: PropTypes.string,
  onClose: PropTypes.func.isRequired,
  onLabelsChanged: PropTypes.func,
};

const AddLabelDrawer = ({
  open,
  onClose,
  projectId,
  datasetId,
  agentDefinitionId,
  onLabelsChanged,
}) => {
  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: { width: 400, zIndex: 20 },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
    >
      {open && (
        <AddLabelDrawerContent
          projectId={projectId}
          datasetId={datasetId}
          agentDefinitionId={agentDefinitionId}
          onClose={onClose}
          onLabelsChanged={onLabelsChanged}
        />
      )}
    </Drawer>
  );
};

AddLabelDrawer.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  projectId: PropTypes.string,
  datasetId: PropTypes.string,
  agentDefinitionId: PropTypes.string,
  onLabelsChanged: PropTypes.func,
};

export default AddLabelDrawer;
