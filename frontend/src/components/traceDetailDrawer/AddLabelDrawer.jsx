import React, { useEffect, useRef, useState } from "react";
import {
  Alert,
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
  useInfiniteAnnotationLabelsList,
  annotationLabelKeys,
} from "src/api/annotation-labels/annotation-labels";
import {
  extractErrorMessage,
  useGetOrCreateDefaultQueue,
  useAddLabelToQueue,
  useRemoveLabelFromQueue,
} from "src/api/annotation-queues/annotation-queues";
import { useQueryClient } from "@tanstack/react-query";
import PropTypes from "prop-types";
import CreateLabelDrawer from "src/sections/annotations/labels/create-label-drawer";
import LabelTypeChip from "src/components/label-type-chip/LabelTypeChip";
import { useDebounce } from "src/hooks/use-debounce";

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
  const [knownLabelsById, setKnownLabelsById] = useState(new Map());
  const [saving, setSaving] = useState(false);
  const [createLabelOpen, setCreateLabelOpen] = useState(false);
  const [queueError, setQueueError] = useState("");
  const queryClient = useQueryClient();
  const debouncedSearch = useDebounce(search.trim(), 300);

  const {
    data: labelsData,
    isLoading: isLabelsLoading,
    isError: isLabelsError,
    isFetchingNextPage,
    hasNextPage,
    fetchNextPage,
    refetch: retryLabels,
  } = useInfiniteAnnotationLabelsList({
    search: debouncedSearch,
    limit: 50,
  });
  const labels = labelsData?.results || [];

  const { mutate: getOrCreateDefault, isPending: isDefaultQueuePending } =
    useGetOrCreateDefaultQueue({
      notifyOnError: false,
    });
  const addLabelMutation = useAddLabelToQueue();
  const removeLabelMutation = useRemoveLabelFromQueue();

  const scopeId = projectId || datasetId || agentDefinitionId;
  const hasFetchedRef = useRef(false);

  // Get or create default queue on mount — guarded to run only once
  useEffect(() => {
    if (scopeId && !defaultQueue && !hasFetchedRef.current) {
      hasFetchedRef.current = true;
      getOrCreateDefault(
        { projectId, datasetId, agentDefinitionId },
        {
          onSuccess: (response) => {
            setQueueError("");
            const result = response.data?.result || response.data;
            const queue = result?.queue;
            if (queue) setDefaultQueue(queue);
            const existingIds = new Set(
              (result?.labels || []).map((l) => l.id),
            );
            setQueueLabelIds(existingIds);
            setKnownLabelsById(
              new Map(
                (result?.labels || [])
                  .filter((label) => label?.id)
                  .map((label) => [label.id, label]),
              ),
            );
          },
          onError: (error) => {
            setDefaultQueue(null);
            setQueueLabelIds(new Set());
            setKnownLabelsById(new Map());
            setQueueError(
              extractErrorMessage(error, "Failed to get default queue"),
            );
          },
        },
      );
    }
  }, [
    scopeId,
    defaultQueue,
    projectId,
    datasetId,
    agentDefinitionId,
    getOrCreateDefault,
  ]);

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
        const selectedLabel = labels.find((label) => label.id === labelId);
        if (selectedLabel) {
          setKnownLabelsById((prev) =>
            new Map(prev).set(selectedLabel.id, selectedLabel),
          );
        }
      }
      onLabelsChanged?.();
    } finally {
      setSaving(false);
    }
  };

  const visibleLabelsById = new Map(knownLabelsById);
  labels.forEach((label) => visibleLabelsById.set(label.id, label));
  const selectedLabels = Array.from(queueLabelIds)
    .map((labelId) => visibleLabelsById.get(labelId))
    .filter(Boolean);
  const isInitialLoading =
    isDefaultQueuePending || (isLabelsLoading && labels.length === 0);

  const handleLabelsScroll = (event) => {
    const element = event.currentTarget;
    const isNearBottom =
      element.scrollHeight - element.scrollTop - element.clientHeight < 32;
    if (isNearBottom && hasNextPage && !isFetchingNextPage) {
      fetchNextPage();
    }
  };

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
          minHeight: 0,
          overflow: "hidden",
        }}
      >
        <Typography variant="body2" color="text.secondary">
          Select labels to add to the default annotation queue. All team members
          can annotate using these labels.
        </Typography>

        {queueError && <Alert severity="error">{queueError}</Alert>}

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
          data-testid="annotation-labels-scroll-region"
          onScroll={handleLabelsScroll}
          sx={{
            flex: "1 1 0",
            minHeight: 120,
            overflowY: "auto",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
          }}
        >
          {isInitialLoading ? (
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ p: 2, textAlign: "center" }}
            >
              Loading...
            </Typography>
          ) : isLabelsError && labels.length === 0 ? (
            <Alert
              severity="error"
              action={
                <Button
                  color="inherit"
                  size="small"
                  onClick={() => retryLabels()}
                >
                  Retry
                </Button>
              }
              sx={{ m: 1 }}
            >
              We couldn&apos;t load labels. Please retry.
            </Alert>
          ) : (
            labels.map((label) => (
              <Box
                key={label.id}
                onClick={() =>
                  !saving && defaultQueue?.id && handleToggle(label.id)
                }
                sx={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  px: 1,
                  py: 0.5,
                  cursor: saving
                    ? "wait"
                    : defaultQueue?.id
                      ? "pointer"
                      : "not-allowed",
                  opacity: defaultQueue?.id ? 1 : 0.6,
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
                    disabled={!defaultQueue?.id || saving}
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
          {!isInitialLoading && !isLabelsError && labels.length === 0 && (
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ p: 2, textAlign: "center" }}
            >
              No labels found
            </Typography>
          )}
          {!isInitialLoading && hasNextPage && (
            <Button
              fullWidth
              size="small"
              disabled={isFetchingNextPage}
              onClick={() => fetchNextPage()}
              sx={{ borderRadius: 0, py: 1 }}
            >
              {isFetchingNextPage ? "Loading more..." : "Load more labels"}
            </Button>
          )}
        </Box>

        {/* Create new label */}
        <Button
          size="small"
          startIcon={<Iconify icon="mingcute:add-line" width={16} />}
          onClick={() => setCreateLabelOpen(true)}
          disabled={!defaultQueue?.id}
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
