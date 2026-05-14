import PropTypes from "prop-types";
import { useState, useMemo, useRef, useEffect, useCallback } from "react";
import { enqueueSnackbar } from "notistack";
import {
  Box,
  CircularProgress,
  Divider,
  IconButton,
  InputAdornment,
  MenuItem,
  MenuList,
  Popover,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  useAnnotationQueuesList,
  useAddQueueItems,
} from "src/api/annotation-queues/annotation-queues";
import CreateQueueDrawer from "../create-queue-drawer";

const PAGE_SIZE = 8;

AddToQueueDialog.propTypes = {
  anchorEl: PropTypes.any,
  onClose: PropTypes.func.isRequired,
  sourceType: PropTypes.string.isRequired,
  // In manual mode these are the selected IDs. In filter mode they carry
  // the *excluded* (deselected) IDs — the backend subtracts them from the
  // filter match set.
  sourceIds: PropTypes.array,
  onSuccess: PropTypes.func,
  itemName: PropTypes.string,
  // Phase 3 — filter-mode selection
  selectionMode: PropTypes.oneOf(["manual", "filter"]),
  filter: PropTypes.array,
  projectId: PropTypes.string,
  // Set to true when the selection came from the voice/simulator grid,
  // so the backend resolver applies list_voice_calls constraints
  // (has_conversation_root, voice system metrics). Without this the
  // resolver would return the project's full trace set.
  isVoiceCall: PropTypes.bool,
  // Voice/simulator projects only — mirrors the grid toolbar toggle. When
  // true, the resolver excludes VAPI simulator calls so the queue receives
  // the same set the grid shows.
  removeSimulationCalls: PropTypes.bool,
};

export default function AddToQueueDialog({
  anchorEl,
  onClose,
  sourceType,
  sourceIds = [],
  onSuccess,
  itemName,
  selectionMode = "manual",
  filter = null,
  projectId = null,
  isVoiceCall = false,
  removeSimulationCalls = false,
}) {
  const open = Boolean(anchorEl);
  const [searchQuery, setSearchQuery] = useState("");
  const [focusIndex, setFocusIndex] = useState(0);
  const [page, setPage] = useState(0);
  const [createDrawerOpen, setCreateDrawerOpen] = useState(false);
  const searchRef = useRef(null);

  const { data: queuesData, isLoading: queuesLoading } =
    useAnnotationQueuesList(
      { limit: 100, ...(searchQuery && { search: searchQuery }) },
      { enabled: open },
    );
  const { mutate: addItems, isPending } = useAddQueueItems();

  const queues = useMemo(() => {
    const all = queuesData?.results || [];
    return all
      .filter((q) => q.status !== "completed")
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [queuesData]);

  const totalPages = Math.ceil(queues.length / PAGE_SIZE);
  const pagedQueues = useMemo(
    () => queues.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE),
    [queues, page],
  );

  // Reset state when popover opens
  useEffect(() => {
    if (open) {
      setSearchQuery("");
      setFocusIndex(0);
      setPage(0);
      setTimeout(() => searchRef.current?.focus(), 50);
    }
  }, [open]);

  // Reset page & focus when search changes
  useEffect(() => {
    setPage(0);
    setFocusIndex(0);
  }, [searchQuery]);

  const handleSelect = useCallback(
    (queue) => {
      if (isPending) return;
      const isFilterMode = selectionMode === "filter";
      if (!isFilterMode && sourceIds.length === 0) return;

      const mutationArgs = isFilterMode
        ? {
            queueId: queue.id,
            selection: {
              mode: "filter",
              source_type: sourceType,
              project_id: projectId,
              filter: filter || [],
              exclude_ids: sourceIds,
              is_voice_call: !!isVoiceCall,
              remove_simulation_calls: !!removeSimulationCalls,
            },
          }
        : {
            queueId: queue.id,
            items: sourceIds.map((id) => ({
              source_type: sourceType,
              source_id: id,
            })),
          };

      addItems(mutationArgs, {
        onSuccess: (data) => {
          const result = data?.data?.result || data?.data;
          const added = result?.added || 0;
          const duplicates = result?.duplicates || 0;
          const errors = Array.isArray(result?.errors) ? result.errors : [];
          const label = itemName || "Item";
          const plural = (n, s = "s") => (n === 1 ? "" : s);
          if (added === 0 && duplicates === 0 && errors.length > 0) {
            // Backend returned 200 but resolution failed — surface the
            // real reason instead of pretending the add succeeded.
            enqueueSnackbar(`Couldn't add to ${queue.name}: ${errors[0]}`, {
              variant: "error",
            });
          } else if (added === 0 && duplicates > 0) {
            // Nothing added — everything was already there.
            enqueueSnackbar(
              duplicates === 1
                ? `${label} already in ${queue.name}`
                : `All ${duplicates} ${label.toLowerCase()}s already in ${queue.name}`,
              { variant: "info" },
            );
          } else if (added > 0 && duplicates > 0) {
            // Partial — report both counts so the user isn't misled.
            enqueueSnackbar(
              `${added} ${label.toLowerCase()}${plural(added)} added to ${queue.name} · ${duplicates} already in queue`,
              { variant: "info" },
            );
          } else if (added === 0) {
            // Nothing added and no duplicates or errors reported — don't
            // claim success.
            enqueueSnackbar(
              `Couldn't add ${label.toLowerCase()} to ${queue.name}`,
              { variant: "error" },
            );
          } else {
            enqueueSnackbar(
              added === 1
                ? `${label} added to ${queue.name}`
                : `${added} ${label.toLowerCase()}s added to ${queue.name}`,
              { variant: "success" },
            );
          }
          onSuccess?.();
          onClose();
        },
      });
    },
    [
      isPending,
      sourceIds,
      sourceType,
      selectionMode,
      filter,
      projectId,
      isVoiceCall,
      removeSimulationCalls,
      addItems,
      itemName,
      onSuccess,
      onClose,
    ],
  );

  const handleKeyDown = useCallback(
    (e) => {
      switch (e.key) {
        case "ArrowDown":
          e.preventDefault();
          setFocusIndex((prev) =>
            prev < pagedQueues.length - 1 ? prev + 1 : prev,
          );
          break;
        case "ArrowUp":
          e.preventDefault();
          setFocusIndex((prev) => (prev > 0 ? prev - 1 : 0));
          break;
        case "Enter":
          e.preventDefault();
          if (pagedQueues[focusIndex]) handleSelect(pagedQueues[focusIndex]);
          break;
        default:
          break;
      }
    },
    [pagedQueues, focusIndex, handleSelect],
  );

  return (
    <>
      <Popover
        open={open}
        anchorEl={anchorEl}
        onClose={onClose}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{
          paper: {
            sx: {
              width: 280,
              mt: 0.5,
              borderRadius: "6px",
              boxShadow: "1px 1px 12px 10px rgba(0,0,0,0.04)",
              border: 1,
              borderColor: "divider",
            },
          },
        }}
      >
        <Box sx={{ p: 1 }} onKeyDown={handleKeyDown}>
          {/* Header */}
          <Typography
            variant="caption"
            sx={{
              px: 0.5,
              py: 0.25,
              fontSize: 11,
              color: "text.secondary",
              textTransform: "uppercase",
              letterSpacing: 0,
              display: "block",
            }}
          >
            Annotation Queues
          </Typography>

          {/* Search */}
          <TextField
            inputRef={searchRef}
            size="small"
            fullWidth
            placeholder="Search queues..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            sx={{ mt: 0.5, mb: 0.5 }}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <Iconify
                    icon="eva:search-fill"
                    width={16}
                    sx={{ color: "text.disabled" }}
                  />
                </InputAdornment>
              ),
              sx: { fontSize: 13, height: 32 },
            }}
          />

          {/* Queue list */}
          {queuesLoading ? (
            <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
              <CircularProgress size={20} />
            </Box>
          ) : pagedQueues.length === 0 ? (
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ py: 2, textAlign: "center", fontSize: 13 }}
            >
              {searchQuery ? "No queues match" : "No queues found"}
            </Typography>
          ) : (
            <MenuList dense sx={{ py: 0.5 }}>
              {pagedQueues.map((queue, idx) => (
                <MenuItem
                  key={queue.id}
                  selected={idx === focusIndex}
                  disabled={isPending}
                  onClick={() => handleSelect(queue)}
                  onMouseEnter={() => setFocusIndex(idx)}
                  sx={{
                    fontSize: 13,
                    py: 0.5,
                    px: 0.5,
                    borderRadius: "4px",
                    minHeight: "auto",
                  }}
                >
                  {queue.name}
                </MenuItem>
              ))}
            </MenuList>
          )}

          {/* Pagination */}
          {totalPages > 1 && (
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                px: 0.5,
                py: 0.25,
              }}
            >
              <IconButton
                size="small"
                disabled={page === 0}
                onClick={() => {
                  setPage((p) => p - 1);
                  setFocusIndex(0);
                }}
                sx={{ p: 0.25 }}
              >
                <Iconify icon="eva:chevron-left-fill" width={18} />
              </IconButton>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ fontSize: 11 }}
              >
                {page + 1} / {totalPages}
              </Typography>
              <IconButton
                size="small"
                disabled={page >= totalPages - 1}
                onClick={() => {
                  setPage((p) => p + 1);
                  setFocusIndex(0);
                }}
                sx={{ p: 0.25 }}
              >
                <Iconify icon="eva:chevron-right-fill" width={18} />
              </IconButton>
            </Box>
          )}

          {/* Footer actions */}
          <Divider sx={{ my: 0.5 }} />
          <MenuItem
            dense
            onClick={() => {
              onClose();
              setCreateDrawerOpen(true);
            }}
            sx={{
              fontSize: 13,
              fontWeight: 600,
              color: "primary.main",
              gap: 0.5,
              py: 0.5,
              px: 0.5,
              borderRadius: "4px",
              minHeight: "auto",
            }}
          >
            <Iconify icon="mdi:plus" width={20} />
            Create new queue
          </MenuItem>
          <Divider sx={{ my: 0.5 }} />
          <MenuItem
            dense
            component="a"
            href="/dashboard/annotations/queues"
            target="_blank"
            rel="noopener"
            onClick={onClose}
            sx={{
              fontSize: 13,
              fontWeight: 600,
              color: "primary.main",
              gap: 0.5,
              py: 0.5,
              px: 0.5,
              borderRadius: "4px",
              minHeight: "auto",
            }}
          >
            <Iconify icon="iconoir:open-new-window" width={20} />
            Manage queues
          </MenuItem>
        </Box>
      </Popover>

      {/* Inline create queue drawer */}
      <CreateQueueDrawer
        open={createDrawerOpen}
        onClose={() => setCreateDrawerOpen(false)}
        onCreated={(queue) => {
          if (!queue?.id) return;
          const isFilterMode = selectionMode === "filter";
          if (!isFilterMode && sourceIds.length === 0) return;

          const mutationArgs = isFilterMode
            ? {
                queueId: queue.id,
                selection: {
                  mode: "filter",
                  source_type: sourceType,
                  project_id: projectId,
                  filter: filter || [],
                  exclude_ids: sourceIds,
                },
              }
            : {
                queueId: queue.id,
                items: sourceIds.map((id) => ({
                  source_type: sourceType,
                  source_id: id,
                })),
              };

          addItems(mutationArgs, {
            onSuccess: () => {
              const label = itemName || "Item";
              const queueName = queue.name || "queue";
              enqueueSnackbar(`${label} added to ${queueName}`, {
                variant: "success",
              });
              onSuccess?.();
            },
          });
        }}
      />
    </>
  );
}
