import React, { useState, useCallback } from "react";
import PropTypes from "prop-types";
import { Popover, Stack, Typography } from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import { enqueueSnackbar } from "notistack";
import { normalizeTags } from "./tagUtils";
import TagChip from "./TagChip";
import TagInput from "./TagInput";

// One shared empty list: a fresh `[]` on every render would re-run the
// reset effect below on every render while the popover is open.
const EMPTY_TAGS = [];

const AddTagsPopover = ({
  anchorEl,
  open,
  onClose,
  traceId,
  spanId,
  bulkItems,
  currentTags = EMPTY_TAGS,
  onSuccess,
}) => {
  const items = Array.isArray(bulkItems) ? bulkItems : [];
  const isBulk = items.length > 1;

  // A one-item selection is edited like a single trace or span: start from
  // that item's tags and save to its id. Callers that pass bulkItems pass
  // no traceId/spanId, so those props must not be used for it.
  const single = items.length === 1 ? items[0] : null;
  let targetTraceId = traceId;
  let targetSpanId = spanId;
  if (single) {
    targetTraceId = single.type === "span" ? undefined : single.id;
    targetSpanId = single.type === "span" ? single.id : undefined;
  }
  const seedTags = single ? single.currentTags || EMPTY_TAGS : currentTags;

  const [tags, setTags] = useState(() =>
    isBulk ? [] : normalizeTags(seedTags),
  );
  const queryClient = useQueryClient();

  React.useEffect(() => {
    if (open) setTags(isBulk ? [] : normalizeTags(seedTags));
  }, [open, seedTags, isBulk]);

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
      if (targetSpanId) {
        return patchSpan(targetSpanId, newTags);
      }
      return patchTrace(targetTraceId, newTags);
    },
    onSuccess: () => {
      enqueueSnackbar(
        isBulk ? `Tags applied to ${items.length} items` : "Tags updated",
        { variant: "success" },
      );
      // Refreshes the trace-detail drawer. The LLM tracing grid is AG-Grid
      // server-side (not React Query), so it relies on onSuccess instead.
      queryClient.invalidateQueries({ queryKey: ["trace-detail"] });
      onSuccess?.();
    },
    onError: () => {
      if (!isBulk) setTags(normalizeTags(seedTags));
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
      slotProps={{ paper: { sx: { width: 320, p: 1.5, mt: 0.5 } } }}
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

      <Typography sx={{ fontSize: 12, color: "text.disabled", mt: 0.75 }}>
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
