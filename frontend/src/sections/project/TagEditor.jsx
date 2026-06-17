import {
  Box,
  Checkbox,
  Chip,
  Divider,
  IconButton,
  Popover,
  TextField,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useState, useCallback, useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Iconify from "src/components/iconify";
import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
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

const fetchAllKnownTags = async () => {
  const { data } = await axios.get(apiPath("/tracer/project/list_projects/"), {
    params: { project_type: "observe", page_size: 100, page_number: 0 },
  });
  const projects = data?.result?.table || [];
  const tagSet = new Set();
  projects.forEach((p) => (p.tags || []).forEach((t) => tagSet.add(t)));
  return Array.from(tagSet).sort();
};

// ── Component ──

const TagEditor = ({ projectId, variant = "grid" }) => {
  const [anchorEl, setAnchorEl] = useState(null);
  const [search, setSearch] = useState("");
  const [newTagInput, setNewTagInput] = useState("");
  const queryClient = useQueryClient();

  // ── Fetch this project's tags ──
  const { data: tags = [] } = useQuery({
    queryKey: ["project-tags", projectId],
    queryFn: () => fetchProjectTags(projectId),
    enabled: !!projectId,
    staleTime: 30_000,
  });

  // ── Fetch all known tags (for the dropdown list) — only when popover is open ──
  const { data: allKnownTags = [] } = useQuery({
    queryKey: ["all-known-tags"],
    queryFn: fetchAllKnownTags,
    enabled: Boolean(anchorEl),
    staleTime: 60_000,
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

  const toggleTag = useCallback(
    (tag) => {
      const updated = tags.includes(tag)
        ? tags.filter((t) => t !== tag)
        : [...tags, tag];
      mutation.mutate(updated);
    },
    [tags, mutation],
  );

  const handleNewTag = useCallback(() => {
    const tag = newTagInput.trim();
    if (!tag) return;
    if (!tags.includes(tag)) {
      mutation.mutate([...tags, tag]);
    }
    setNewTagInput("");
  }, [newTagInput, tags, mutation]);

  // Combine known tags + current tags for the dropdown
  const availableTags = useMemo(() => {
    const combined = new Set([...allKnownTags, ...tags]);
    const q = search.toLowerCase();
    return Array.from(combined)
      .filter((t) => !q || t.toLowerCase().includes(q))
      .sort();
  }, [allKnownTags, tags, search]);

  return (
    <>
      {/* ── Inline display — entire area is clickable ── */}
      <Box
        onClick={(e) => {
          e.stopPropagation();
          setAnchorEl(e.currentTarget);
        }}
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.5,
          overflow: "hidden",
          width: "100%",
          cursor: "pointer",
          borderRadius: "4px",
          px: 0.5,
          py: 0.25,
          "&:hover": { bgcolor: "action.hover" },
        }}
      >
        {tags.length > 0 ? (
          <>
            {tags.slice(0, 2).map((tag) => (
              <Chip
                key={tag}
                label={tag}
                size="small"
                sx={{
                  height: 20,
                  fontSize: 11,
                  fontWeight: 500,
                  color: getTagColor(tag),
                  bgcolor: `${getTagColor(tag)}14`,
                  border: `1px solid ${getTagColor(tag)}30`,
                  "& .MuiChip-label": { px: 0.75 },
                  pointerEvents: "none",
                }}
              />
            ))}
            {tags.length > 2 && (
              <Typography sx={{ fontSize: 10, color: "text.disabled" }}>
                +{tags.length - 2}
              </Typography>
            )}
            <Iconify
              icon="mdi:chevron-down"
              width={14}
              sx={{ color: "text.disabled", ml: "auto", flexShrink: 0 }}
            />
          </>
        ) : (
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
        )}
      </Box>

      {/* ── Popover ── */}
      <Popover
        open={Boolean(anchorEl)}
        anchorEl={anchorEl}
        onClose={() => {
          setAnchorEl(null);
          setSearch("");
          setNewTagInput("");
        }}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{
          paper: {
            sx: { width: 220, borderRadius: "10px", overflow: "hidden" },
            onClick: (e) => e.stopPropagation(),
          },
        }}
      >
        <Box sx={{ p: 1.5, pb: 1 }}>
          <Typography sx={{ fontSize: 13, fontWeight: 600, mb: 1 }}>
            Tags
          </Typography>
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
                  cursor: "pointer",
                  borderRadius: "4px",
                  "&:hover": { bgcolor: "action.hover" },
                }}
              >
                <Checkbox
                  size="small"
                  checked={checked}
                  tabIndex={-1}
                  sx={{ p: 0.25, "& .MuiSvgIcon-root": { fontSize: 16 } }}
                />
                <Typography sx={{ fontSize: 12, fontWeight: 500, color }}>
                  {tag}
                </Typography>
              </Box>
            );
          })}
          {availableTags.length === 0 && (
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
        </Box>
      </Popover>
    </>
  );
};

TagEditor.propTypes = {
  projectId: PropTypes.string.isRequired,
  variant: PropTypes.oneOf(["grid", "header"]),
};

export default TagEditor;
