import {
  Box,
  Button,
  Checkbox,
  Chip,
  Divider,
  IconButton,
  Popover,
  TextField,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Iconify from "src/components/iconify";
import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import { fetchAllObserveProjects } from "src/api/project/observe-project-list";
import { enqueueSnackbar } from "notistack";

// ── Tag colors ──

const TAG_COLORS = [
  "#EF4444",
  "#3B82F6",
  "#F59E0B",
  "#22C55E",
  "#8B5CF6",
  "#EC4899",
  "#94A3B8",
  "#06B6D4",
];

function getTagColor(tag) {
  let hash = 0;
  for (let i = 0; i < tag.length; i++)
    hash = tag.charCodeAt(i) + ((hash << 5) - hash);
  return TAG_COLORS[Math.abs(hash) % TAG_COLORS.length];
}

// Below this trigger width a preview chip cannot stay readable next to the
// "+N" remainder and the chevron, so the list summary falls back to "N tags".
// Roughly: ~40px of chip label + 22px count + 8px gaps + 14px chevron.
const NARROW_TRIGGER_WIDTH = 88;

// ── API ──

const fetchProjectTags = async (projectId) => {
  const { data } = await axios.get(
    apiPath("/tracer/project/{id}/", { id: projectId }),
  );
  const result = data?.result || data;
  return result?.tags || [];
};

const updateProjectTags = async (projectId, tags) => {
  const { data } = await axios.patch(
    apiPath("/tracer/project/{id}/tags/", { id: projectId }),
    { tags },
  );
  return data?.result?.tags || tags;
};

const fetchAllKnownTags = async ({ signal } = {}) => {
  const projects = await fetchAllObserveProjects({ signal });
  const tagSet = new Set();
  projects.forEach((p) => (p.tags || []).forEach((t) => tagSet.add(t)));
  return Array.from(tagSet).sort();
};

// ── Component ──

const chipSx = (tag) => ({
  height: 20,
  fontSize: 11,
  fontWeight: 500,
  color: getTagColor(tag),
  bgcolor: `${getTagColor(tag)}14`,
  border: `1px solid ${getTagColor(tag)}30`,
  "& .MuiChip-label": { px: 0.75 },
  pointerEvents: "none",
});

const TagEditor = ({ projectId, projectName, variant = "grid" }) => {
  // The project header keeps its existing editor-first entry; the project
  // list (default "grid" variant) gets the bounded summary + inspect-first
  // flow from TH-4058.
  const isHeader = variant === "header";
  const [anchorEl, setAnchorEl] = useState(null);
  const [search, setSearch] = useState("");
  const [newTagInput, setNewTagInput] = useState("");
  const [mode, setMode] = useState(isHeader ? "edit" : "inspect");
  const [narrow, setNarrow] = useState(false);
  const triggerRef = useRef(null);
  const editButtonRef = useRef(null);
  const closeButtonRef = useRef(null);
  const focusEditOnReturn = useRef(false);
  const titleId = useId();
  const queryClient = useQueryClient();

  // ── Fetch this project's tags ──
  const {
    data: tagData,
    isLoading: isTagsLoading,
    isError: isTagsError,
    refetch: refetchTags,
  } = useQuery({
    queryKey: ["project-tags", projectId],
    queryFn: () => fetchProjectTags(projectId),
    enabled: !!projectId,
    staleTime: 30_000,
  });
  const tags = useMemo(() => tagData ?? [], [tagData]);
  // React Query keeps the last successful `data` when a refetch fails, so a
  // refresh failure is distinct from a first-read failure (AC08): keep the
  // last-known tags readable and pause editing until a refresh succeeds.
  const hasTagData = tagData !== undefined;
  const isTagsUnavailable = isTagsError && !hasTagData;
  const isTagsStale = isTagsError && hasTagData;
  const canMutate = !isTagsLoading && !isTagsError;

  // ── Fetch all known tags (for the dropdown list) — only while editing ──
  const {
    data: allKnownTags = [],
    isError: isKnownTagsError,
    isFetching: isKnownTagsFetching,
    refetch: refetchKnownTags,
  } = useQuery({
    queryKey: ["all-known-tags"],
    queryFn: ({ signal }) => fetchAllKnownTags({ signal }),
    enabled: Boolean(anchorEl) && mode === "edit",
    staleTime: 60_000,
    retry: false,
  });

  // ── Mutation ──
  const mutation = useMutation({
    mutationFn: (newTags) => updateProjectTags(projectId, newTags),
    onMutate: async (newTags) => {
      // Cancel outgoing refetches
      await queryClient.cancelQueries({
        queryKey: ["project-tags", projectId],
      });
      // Snapshot previous
      const prev = queryClient.getQueryData(["project-tags", projectId]);
      // Optimistically set new tags
      queryClient.setQueryData(["project-tags", projectId], newTags);
      return { prev };
    },
    onError: (_err, _newTags, context) => {
      // Revert on error
      queryClient.setQueryData(["project-tags", projectId], context?.prev);
      enqueueSnackbar("Failed to update tags", { variant: "error" });
    },
    onSettled: () => {
      // Refetch to ensure server state
      queryClient.invalidateQueries({ queryKey: ["project-tags", projectId] });
      queryClient.invalidateQueries({ queryKey: ["observe-projects"] });
      queryClient.invalidateQueries({ queryKey: ["all-known-tags"] });
    },
  });
  const isSaving = mutation.isPending;
  // Writes are blocked while a save is pending or the project's tags are not
  // authoritative (loading/stale). Controls stay focusable and advertise the
  // state with aria-disabled instead of `disabled`, so a keyboard user does
  // not lose focus after every save.
  const writesBlocked = isSaving || !canMutate;

  const toggleTag = useCallback(
    (tag) => {
      if (writesBlocked) return;
      const updated = tags.includes(tag)
        ? tags.filter((t) => t !== tag)
        : [...tags, tag];
      mutation.mutate(updated);
    },
    [tags, mutation, writesBlocked],
  );

  const handleNewTag = useCallback(() => {
    if (writesBlocked) return;
    const tag = newTagInput.trim();
    if (!tag) return;
    if (!tags.includes(tag)) {
      mutation.mutate([...tags, tag]);
    }
    setNewTagInput("");
  }, [newTagInput, tags, mutation, writesBlocked]);

  // Combine known tags + current tags for the dropdown
  const availableTags = useMemo(() => {
    const combined = new Set([...allKnownTags, ...tags]);
    const q = search.toLowerCase();
    return Array.from(combined)
      .filter((t) => !q || t.toLowerCase().includes(q))
      .sort();
  }, [allKnownTags, tags, search]);

  // Width-based fallback for the list summary (D01): measured, not guessed
  // from character counts.
  useEffect(() => {
    const el = triggerRef.current;
    if (isHeader || !el || typeof ResizeObserver === "undefined") {
      return undefined;
    }
    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect?.width ?? el.clientWidth;
      setNarrow(width > 0 && width < NARROW_TRIGGER_WIDTH);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [isHeader]);

  useEffect(() => {
    if (mode !== "inspect" || !focusEditOnReturn.current) return;
    focusEditOnReturn.current = false;
    const edit = editButtonRef.current;
    const target = edit && !edit.disabled ? edit : closeButtonRef.current;
    target?.focus();
  }, [mode]);

  const resetTransient = () => {
    setSearch("");
    setNewTagInput("");
  };

  const openPopover = (event) => {
    event.stopPropagation();
    setAnchorEl(event.currentTarget);
    setMode(isHeader ? "edit" : "inspect");
    resetTransient();
  };

  const closePopover = () => {
    setAnchorEl(null);
    setMode(isHeader ? "edit" : "inspect");
    resetTransient();
  };

  const tagCount = tags.length;
  const countLabel = `${tagCount} ${tagCount === 1 ? "tag" : "tags"}`;
  const subject = projectName ? `project ${projectName}` : "project";
  let triggerLabel = isHeader
    ? `Edit tags, ${countLabel}`
    : `View tags for ${subject}, ${countLabel}`;
  if (isTagsLoading) triggerLabel = `Tags for ${subject} are loading`;
  else if (isTagsUnavailable)
    triggerLabel = `Tags for ${subject} are unavailable`;
  else if (isTagsStale) triggerLabel += " (couldn't refresh)";

  const renderSummary = () => {
    if (isTagsLoading) {
      return (
        <Typography sx={{ fontSize: 10, color: "text.disabled" }}>
          Loading
        </Typography>
      );
    }
    if (isTagsUnavailable) {
      return (
        <Typography sx={{ fontSize: 10, color: "warning.main" }} noWrap>
          Tags unavailable
        </Typography>
      );
    }
    if (tagCount === 0) {
      if (isHeader) {
        // Existing header presentation, unchanged.
        return (
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 0.5,
              color: "text.disabled",
            }}
          >
            <Iconify icon="mdi:tag-plus-outline" width={18} />
          </Box>
        );
      }
      return (
        <Typography sx={{ fontSize: 10, color: "text.disabled" }} noWrap>
          No tags
        </Typography>
      );
    }
    if (isHeader) {
      // Existing header presentation, unchanged.
      return (
        <>
          {tags.slice(0, 2).map((tag) => (
            <Chip key={tag} label={tag} size="small" sx={chipSx(tag)} />
          ))}
          {tagCount > 2 && (
            <Typography sx={{ fontSize: 10, color: "text.disabled" }}>
              +{tagCount - 2}
            </Typography>
          )}
        </>
      );
    }
    if (narrow) {
      return (
        <Typography sx={{ fontSize: 10, color: "text.disabled" }} noWrap>
          {countLabel}
        </Typography>
      );
    }
    // One bounded preview chip plus the exact number of other tags.
    return (
      <>
        <Chip
          label={tags[0]}
          size="small"
          sx={{
            ...chipSx(tags[0]),
            minWidth: 0,
            "& .MuiChip-label": {
              px: 0.75,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            },
          }}
        />
        {tagCount > 1 && (
          <Typography
            sx={{ fontSize: 10, color: "text.disabled", flexShrink: 0 }}
          >
            +{tagCount - 1}
          </Typography>
        )}
      </>
    );
  };

  const showBackToTags = mode === "edit" && !isHeader;
  let editStatusText = "Changes save automatically";
  if (isSaving) editStatusText = "Saving…";
  else if (isTagsLoading) editStatusText = "Loading tags… editing paused";
  else if (isTagsError)
    editStatusText = "Couldn't refresh tags. Editing paused.";

  return (
    <>
      {/* ── Inline display — entire area is a keyboard-operable button ── */}
      <Box
        ref={triggerRef}
        role="button"
        tabIndex={0}
        aria-label={triggerLabel}
        aria-haspopup="dialog"
        aria-expanded={Boolean(anchorEl)}
        onClick={openPopover}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            openPopover(e);
          }
        }}
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.5,
          overflow: "hidden",
          width: "100%",
          minWidth: 0,
          cursor: "pointer",
          borderRadius: "4px",
          px: 0.5,
          py: 0.25,
          "&:hover": { bgcolor: "action.hover" },
          "&:focus-visible": {
            outline: "2px solid",
            outlineColor: "primary.main",
            outlineOffset: -2,
          },
        }}
      >
        {renderSummary()}
        {isTagsStale && (
          <Iconify
            icon="mdi:alert-circle-outline"
            width={12}
            title="Couldn't refresh tags"
            sx={{ color: "warning.main", ml: "auto", flexShrink: 0 }}
          />
        )}
        <Iconify
          icon="mdi:chevron-down"
          width={14}
          sx={{
            color: "text.disabled",
            ml: isTagsStale ? 0 : "auto",
            flexShrink: 0,
          }}
        />
      </Box>

      {/* ── Popover ── */}
      <Popover
        open={Boolean(anchorEl)}
        anchorEl={anchorEl}
        onClose={closePopover}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{
          paper: {
            role: "dialog",
            "aria-labelledby": titleId,
            sx: { width: 240, borderRadius: "10px", overflow: "hidden" },
            onClick: (e) => e.stopPropagation(),
          },
        }}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 0.5,
            px: 1.5,
            pt: 1,
            pb: 0.5,
          }}
        >
          <Typography
            id={titleId}
            sx={{ fontSize: 13, fontWeight: 600 }}
            noWrap
          >
            Tags{hasTagData && !isTagsLoading ? ` (${tagCount})` : ""}
          </Typography>
          <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
            {mode === "inspect" && hasTagData && !isTagsLoading && (
              <Button
                ref={editButtonRef}
                size="small"
                disabled={!canMutate}
                onClick={() => setMode("edit")}
                sx={{ minWidth: 0, px: 1, fontSize: 11 }}
              >
                {tagCount === 0 ? "Add tags" : "Edit tags"}
              </Button>
            )}
            {showBackToTags && (
              <Button
                size="small"
                onClick={() => {
                  focusEditOnReturn.current = true;
                  setMode("inspect");
                  resetTransient();
                }}
                sx={{ minWidth: 0, px: 1, fontSize: 11 }}
              >
                Back to tags
              </Button>
            )}
            <IconButton
              ref={closeButtonRef}
              size="small"
              aria-label="Close"
              onClick={closePopover}
              sx={{ p: 0.25 }}
            >
              <Iconify icon="mdi:close" width={16} />
            </IconButton>
          </Box>
        </Box>

        {mode === "inspect" && (
          <Box
            data-testid="tag-inspect-list"
            sx={{ maxHeight: 220, overflow: "auto", px: 1, pb: 1 }}
          >
            {isTagsLoading && (
              <Typography
                sx={{ px: 1, py: 1, fontSize: 11, color: "text.disabled" }}
              >
                Loading tags…
              </Typography>
            )}
            {isTagsError && (
              <Box
                role="alert"
                sx={{
                  px: 1,
                  py: 0.75,
                  fontSize: 11,
                  color: "warning.main",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 1,
                }}
              >
                {isTagsStale
                  ? "Couldn't refresh tags. Showing the last known tags."
                  : "Tags unavailable."}
                <Button size="small" onClick={() => refetchTags()}>
                  Retry
                </Button>
              </Box>
            )}
            {!isTagsLoading && !isTagsError && tagCount === 0 && (
              <Typography
                sx={{
                  px: 1,
                  py: 1,
                  fontSize: 11,
                  color: "text.disabled",
                  textAlign: "center",
                }}
              >
                No tags on this project
              </Typography>
            )}
            {!isTagsLoading &&
              hasTagData &&
              tags.map((tag) => (
                <Typography
                  key={tag}
                  sx={{
                    fontSize: 12,
                    fontWeight: 500,
                    color: getTagColor(tag),
                    px: 1,
                    py: 0.5,
                    borderRadius: "4px",
                    overflowWrap: "anywhere",
                    userSelect: "text",
                    cursor: "text",
                  }}
                >
                  {tag}
                </Typography>
              ))}
          </Box>
        )}

        {mode === "edit" && (
          <>
            <Box sx={{ px: 1.5, pb: 1 }}>
              <TextField
                size="small"
                fullWidth
                placeholder="Search tags"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                autoFocus
                InputProps={{
                  startAdornment: (
                    <Iconify
                      icon="mdi:magnify"
                      width={16}
                      sx={{ color: "text.disabled", mr: 0.5 }}
                    />
                  ),
                  sx: { fontSize: 12, height: 32, borderRadius: "6px" },
                }}
              />
            </Box>

            <Box sx={{ maxHeight: 200, overflow: "auto", px: 0.5 }}>
              {availableTags.map((tag) => {
                const checked = tags.includes(tag);
                const color = getTagColor(tag);
                return (
                  <Box
                    key={tag}
                    onClick={() => toggleTag(tag)}
                    sx={{
                      display: "flex",
                      alignItems: "center",
                      gap: 0.5,
                      px: 1,
                      py: 0.25,
                      cursor: writesBlocked ? "default" : "pointer",
                      borderRadius: "4px",
                      "&:hover": { bgcolor: "action.hover" },
                    }}
                  >
                    <Checkbox
                      size="small"
                      checked={checked}
                      inputProps={{
                        "aria-label": tag,
                        "aria-disabled": writesBlocked,
                      }}
                      sx={{ p: 0.25, "& .MuiSvgIcon-root": { fontSize: 16 } }}
                    />
                    <Typography sx={{ fontSize: 12, fontWeight: 500, color }}>
                      {tag}
                    </Typography>
                  </Box>
                );
              })}
              {isKnownTagsError && (
                <Box
                  role="alert"
                  sx={{
                    px: 1,
                    py: 0.75,
                    fontSize: 11,
                    color: "warning.main",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    gap: 1,
                  }}
                >
                  Tag suggestions unavailable.
                  <Button
                    size="small"
                    disabled={isKnownTagsFetching}
                    onClick={() => refetchKnownTags()}
                  >
                    Retry
                  </Button>
                </Box>
              )}
              {availableTags.length === 0 && !isKnownTagsError && (
                <Typography
                  sx={{
                    px: 1,
                    py: 1,
                    fontSize: 11,
                    color: "text.disabled",
                    textAlign: "center",
                  }}
                >
                  No tags found
                </Typography>
              )}
            </Box>

            <Divider sx={{ mt: 0.5 }} />

            <Box sx={{ p: 1 }}>
              <TextField
                size="small"
                fullWidth
                placeholder="Type new tag and press Enter"
                value={newTagInput}
                onChange={(e) => setNewTagInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    e.stopPropagation();
                    handleNewTag();
                  }
                }}
                inputProps={{
                  "aria-disabled": writesBlocked,
                  readOnly: writesBlocked,
                }}
                InputProps={{
                  startAdornment: (
                    <Iconify
                      icon="mdi:plus"
                      width={14}
                      sx={{ color: "primary.main", mr: 0.5 }}
                    />
                  ),
                  sx: { fontSize: 12, height: 32, borderRadius: "6px" },
                }}
              />
              <Box
                sx={{
                  mt: 0.5,
                  px: 0.5,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 1,
                }}
              >
                <Typography
                  sx={{
                    fontSize: 10,
                    color: canMutate ? "text.disabled" : "warning.main",
                  }}
                >
                  {editStatusText}
                </Typography>
                {isTagsError && (
                  <Button size="small" onClick={() => refetchTags()}>
                    Retry
                  </Button>
                )}
              </Box>
            </Box>
          </>
        )}
      </Popover>
    </>
  );
};

TagEditor.propTypes = {
  projectId: PropTypes.string.isRequired,
  projectName: PropTypes.string,
  variant: PropTypes.oneOf(["grid", "header"]),
};

export default TagEditor;
