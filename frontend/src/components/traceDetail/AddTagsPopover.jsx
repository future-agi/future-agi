import React, { useState, useCallback } from "react";
import PropTypes from "prop-types";
import { Popover, Stack, Typography } from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import useSaveEntityTags, {
  isSourceTag,
  tagUpdateErrorMessage,
} from "./useSaveEntityTags";
import { enqueueSnackbar } from "notistack";
import { normalizeTags } from "./tagUtils";
import TagChip from "./TagChip";
import TagInput from "./TagInput";

const EMPTY_TAGS = [];

const AddTagsPopover = ({
  anchorEl,
  open,
  onClose,
  spanId,
  entity,
  bulkItems,
  currentTags = EMPTY_TAGS,
  onSuccess,
}) => {
  const items = Array.isArray(bulkItems) ? bulkItems : [];
  const isBulk = items.length > 1;
  const selectedEntity = items.length === 1 ? items[0].entity : entity;
  const selectedTags =
    items.length === 1 ? items[0].currentTags || EMPTY_TAGS : currentTags;
  const saveEntityTags = useSaveEntityTags();

  const [tags, setTags] = useState(() =>
    isBulk ? [] : normalizeTags(selectedTags),
  );
  const confirmedTags = React.useRef(tags);
  const queryClient = useQueryClient();

  React.useEffect(() => {
    if (open) {
      confirmedTags.current = isBulk ? [] : normalizeTags(selectedTags);
      setTags(confirmedTags.current);
    }
  }, [open, selectedTags, isBulk]);

  const { mutate: saveTags, isPending } = useMutation({
    mutationFn: (newTags) => {
      if (isBulk) {
        // Merge with each item's existing tags to avoid overwriting.
        // Backend PATCH replaces tags[], so we compute the full set here.
        return Promise.allSettled(
          items.map((item) => {
            const merged = normalizeTags(item.currentTags || []);
            newTags.forEach((t) => {
              if (!merged.some((e) => e.name === t.name)) merged.push(t);
            });
            return saveEntityTags({
              entity: item.entity,
              type: item.type,
              tags: merged,
            });
          }),
        ).then((results) => {
          const failed = results.find((result) => result.status === "rejected");
          if (failed) throw failed.reason;
          return results;
        });
      }
      return saveEntityTags({
        entity: selectedEntity,
        type: items[0]?.type || (spanId ? "span" : "trace"),
        tags: newTags,
      });
    },
    onSuccess: (result) => {
      if (!isBulk) {
        confirmedTags.current = normalizeTags(result.tags);
        setTags(confirmedTags.current);
      }
      enqueueSnackbar(
        isBulk ? `Tags applied to ${items.length} items` : "Tags updated",
        { variant: "success" },
      );
      // Refreshes the trace-detail drawer. The LLM tracing grid is AG-Grid
      // server-side (not React Query), so it relies on onSuccess instead.
      queryClient.invalidateQueries({ queryKey: ["trace-detail"] });
      // A single save also hands back the acknowledged result and the saved
      // entity, so a grid can update that row without reloading the list.
      onSuccess?.(result, isBulk ? undefined : selectedEntity);
    },
    onError: (error) => {
      if (!isBulk) setTags(confirmedTags.current);
      queryClient.invalidateQueries({ queryKey: ["trace-detail"] });
      enqueueSnackbar(
        `${isBulk ? "Some items may have been updated. " : ""}${tagUpdateErrorMessage(error)}`,
        { variant: "error" },
      );
    },
    retry: false,
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
              readOnly={isPending || isSourceTag(selectedEntity, tag.name)}
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
  entity: PropTypes.object,
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
