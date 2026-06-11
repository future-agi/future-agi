import React, { useState, useCallback } from "react";
import PropTypes from "prop-types";
import { Box, Popover, Stack, Typography } from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import { enqueueSnackbar } from "notistack";
import { normalizeTags } from "./tagUtils";
import TagChip from "./TagChip";
import TagInput from "./TagInput";

const AddTagsPopover = ({
  anchorEl,
  open,
  onClose,
  traceId,
  spanId,
  bulkItems,
  currentTags = [],
  onSuccess,
}) => {
  const items = Array.isArray(bulkItems) ? bulkItems : [];
  const isBulk = items.length > 1;

  const [tags, setTags] = useState(() =>
    isBulk ? [] : normalizeTags(currentTags),
  );
  const queryClient = useQueryClient();

  React.useEffect(() => {
    if (open) setTags(isBulk ? [] : normalizeTags(currentTags));
  }, [open, currentTags, isBulk]);

  const patchTrace = (id, newTags) =>
    axios.patch(apiPath("/tracer/trace/{id}/tags/", { id }), { tags: newTags });
  const patchSpan = (id, newTags) =>
    axios.post(apiPath("/tracer/observation-span/update-tags/"), {
      span_id: id,
      tags: newTags,
    });

  const { mutate: saveTags, isPending } = useMutation({
    mutationFn: (newTags) => {
      if (isBulk) {
        // Merge with each item's existing tags to avoid overwriting.
        // Backend PATCH replaces tags[], so we compute the full set here.
        return Promise.all(
          items.map((item) => {
            const existing = normalizeTags(item.currentTags || []);
            const merged = [...existing];
            newTags.forEach((t) => {
              if (!merged.some((e) => e.name === t.name)) merged.push(t);
            });
            return item.type === "span"
              ? patchSpan(item.id, merged)
              : patchTrace(item.id, merged);
          }),
        );
      }
      if (spanId) {
        return patchSpan(spanId, newTags);
      }
      return patchTrace(traceId, newTags);
    },
    onSuccess: () => {
      enqueueSnackbar(
        isBulk ? `Tags applied to ${items.length} items` : "Tags updated",
        { variant: "success" },
      );
      queryClient.invalidateQueries({ queryKey: ["trace-detail"] });
      queryClient.invalidateQueries({ queryKey: ["traceList"] });
      queryClient.invalidateQueries({ queryKey: ["spanList"] });
      onSuccess?.();
    },
    onError: () => {
      if (!isBulk) setTags(normalizeTags(currentTags));
      enqueueSnackbar("Failed to update tags", { variant: "error" });
    },
  });

  const persist = useCallback(
    (nextTags) => {
      setTags(nextTags);
      saveTags(nextTags);
    },
    [saveTags],
  );

  const handleAdd = useCallback(
    (newTag) => {
      if (tags.some((t) => t.name === newTag.name)) return;
      persist([...tags, newTag]);
    },
    [tags, persist],
  );

  const handleRemove = useCallback(
    (idx) => persist(tags.filter((_, i) => i !== idx)),
    [tags, persist],
  );

  const handleColorChange = useCallback(
    (idx, color) =>
      persist(tags.map((t, i) => (i === idx ? { ...t, color } : t))),
    [tags, persist],
  );

  const handleRename = useCallback(
    (idx, newName) => {
      if (tags.some((t, i) => i !== idx && t.name === newName)) return;
      persist(tags.map((t, i) => (i === idx ? { ...t, name: newName } : t)));
    },
    [tags, persist],
  );

  return (
    <Popover
      open={open}
      anchorEl={anchorEl}
      onClose={onClose}
      anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
      transformOrigin={{ vertical: "top", horizontal: "right" }}
      slotProps={{ paper: { sx: { width: 300, p: 1.5, mt: 0.5 } } }}
    >
      <Typography sx={{ fontSize: 12, fontWeight: 600, mb: 1 }}>
        {isBulk ? `Add tags to ${items.length} items` : "Tags"}
      </Typography>

      {!isBulk && tags.length > 0 && (
        <Stack direction="row" sx={{ flexWrap: "wrap", gap: 0.5, mb: 1.5 }}>
          {tags.map((tag, idx) => (
            <TagChip
              key={`${tag.name}-${idx}`}
              name={tag.name}
              color={tag.color}
              onRemove={() => handleRemove(idx)}
              onColorChange={(c) => handleColorChange(idx, c)}
              onRename={(n) => handleRename(idx, n)}
            />
          ))}
        </Stack>
      )}

      <TagInput
        onAdd={handleAdd}
        existingNames={tags.map((t) => t.name)}
        disabled={isPending}
      />

      <Typography sx={{ fontSize: 10, color: "text.disabled", mt: 0.75 }}>
        {isBulk
          ? "Tags will be added to every selected item"
          : "Double-click name to rename · Click dot to change color"}
      </Typography>
    </Popover>
  );
};

AddTagsPopover.propTypes = {
  anchorEl: PropTypes.any,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  traceId: PropTypes.string,
  spanId: PropTypes.string,
  bulkItems: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string.isRequired,
      type: PropTypes.oneOf(["trace", "span"]).isRequired,
      currentTags: PropTypes.array,
    }),
  ),
  currentTags: PropTypes.array,
  onSuccess: PropTypes.func,
};

export default React.memo(AddTagsPopover);
