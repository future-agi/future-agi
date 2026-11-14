import React, { useState, useCallback } from "react";
import PropTypes from "prop-types";
import { Popover, Stack, Typography } from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import useSaveEntityTags, {
  isSourceTag,
  latestTagState,
  tagTargetKey,
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
  onTagsSaved,
}) => {
  const items = React.useMemo(
    () => (Array.isArray(bulkItems) ? bulkItems : EMPTY_TAGS),
    [bulkItems],
  );
  const isBulk = items.length > 1;
  const selectedEntity = items.length === 1 ? items[0].entity : entity;
  const selectedType = items[0]?.type || (spanId ? "span" : "trace");
  const saveEntityTags = useSaveEntityTags();
  // Keep the visible count, but write each shared assignment only once.
  const uniqueItems = React.useMemo(() => {
    const seen = new Set();
    return items.filter((item) => {
      const key = tagTargetKey(item.entity, item.type);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }, [items]);
  // Mutations finish after a popover closes or changes selection. The session
  // belongs to the request, never to the latest render's selection.
  const session = React.useRef({ open, items, id: 0 });
  if (session.current.open !== open || session.current.items !== items) {
    session.current = { open, items, id: session.current.id + 1 };
  }

  // The last acknowledged save of each entity (tagTargetKey -> result).
  // The owner's entity and tags can be older than it (a drawer
  // refetch or a grid row update lands later), and the popover can be
  // reopened, or moved to another entity and back, before they catch up.
  const acknowledged = React.useRef(new Map());
  // The tag state the shown list was built from, sent as the save's base so
  // the list and the expected revision always match.
  const baseState = React.useRef({ key: null, state: null });
  const selectedEntityRef = React.useRef(selectedEntity);
  selectedEntityRef.current = selectedEntity;
  const onTagsSavedRef = React.useRef(onTagsSaved);
  onTagsSavedRef.current = onTagsSaved;

  const seed = () => {
    if (isBulk) return [];
    const current = selectedEntityRef.current;
    const state = latestTagState(current, acknowledged.current, selectedType);
    const visible = normalizeTags(state?.tags || currentTags);
    baseState.current = {
      key: tagTargetKey(current, selectedType),
      state: state && { ...state, tags: visible },
    };
    return visible;
  };
  const [tags, setTags] = useState(seed);
  const confirmedTags = React.useRef(tags);
  const queryClient = useQueryClient();

  const selectedKey = tagTargetKey(selectedEntity, selectedType);
  React.useEffect(() => {
    if (open) {
      confirmedTags.current = seed();
      setTags(confirmedTags.current);
    }
    // Reseed on open, when the owner's tags change and when it selects
    // another entity; the entity is read through a ref so a new object for
    // the same entity does not reseed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, currentTags, selectedKey, selectedEntity?.tag_revision, isBulk]);

  const { mutate: saveTags, isPending } = useMutation({
    // The saved entity travels with the request: the owner can select another
    // entity before the save is acknowledged.
    mutationFn: ({
      tags: newTags,
      entity: savedEntity,
      type,
      base,
      bulk,
      savedItems,
    }) => {
      if (bulk) {
        // The backend replaces tags[]. Read both the list and revision from
        // the same state, even if the selected grid row changed in place.
        return Promise.allSettled(
          savedItems.map((item) => {
            const state = latestTagState(
              item.entity,
              acknowledged.current,
              item.type,
            );
            const merged = normalizeTags(state?.tags || []);
            newTags.forEach((t) => {
              if (!merged.some((e) => e.name === t.name)) merged.push(t);
            });
            return saveEntityTags({
              entity: item.entity,
              type: item.type,
              tags: merged,
              base: state,
            });
          }),
        ).then((results) => {
          const saved = results.flatMap((outcome, index) => {
            if (outcome.status !== "fulfilled") return [];
            const { entity, type } = savedItems[index];
            const key = tagTargetKey(entity, type);
            const previous = acknowledged.current.get(key);
            if (
              !previous ||
              outcome.value.tag_revision >= previous.tag_revision
            ) {
              acknowledged.current.set(key, outcome.value);
            }
            return [{ result: outcome.value, entity, type }];
          });
          const failed = results.find(
            (outcome) => outcome.status === "rejected",
          );
          if (failed) throw { reason: failed.reason, saved };
          return saved;
        });
      }
      return saveEntityTags({
        entity: savedEntity,
        type,
        tags: newTags,
        base,
      });
    },
    onSuccess: (
      result,
      {
        entity: savedEntity,
        type,
        tags: savedTags,
        bulk,
        sessionId,
        itemCount,
      },
    ) => {
      if (bulk) {
        if (sessionId === session.current.id) confirmedTags.current = savedTags;
      } else {
        const key = tagTargetKey(savedEntity, type);
        acknowledged.current.set(key, result);
        // Show it only if the popover still edits the entity it was saved for.
        if (key === tagTargetKey(selectedEntityRef.current, selectedType)) {
          baseState.current = { key, state: result };
          confirmedTags.current = normalizeTags(result.tags);
          setTags(confirmedTags.current);
        }
      }
      enqueueSnackbar(
        bulk ? `Tags applied to ${itemCount} items` : "Tags updated",
        { variant: "success" },
      );
      // Detail drawers use React Query; server-side grid rows need the
      // acknowledged revision applied directly, including after close.
      queryClient.invalidateQueries({ queryKey: ["trace-detail"] });
      queryClient.invalidateQueries({ queryKey: ["voiceCallDetail"] });
      onTagsSavedRef.current?.(
        bulk ? result : [{ result, entity: savedEntity, type }],
      );
      onSuccess?.(result, bulk ? undefined : { entity: savedEntity, type });
    },
    onError: (error, { bulk, sessionId }) => {
      if (!bulk || sessionId === session.current.id) {
        setTags(confirmedTags.current);
      }
      queryClient.invalidateQueries({ queryKey: ["trace-detail"] });
      queryClient.invalidateQueries({ queryKey: ["voiceCallDetail"] });
      if (bulk && error.saved.length) onTagsSavedRef.current?.(error.saved);
      enqueueSnackbar(
        `${bulk ? "Some items may have been updated. " : ""}${tagUpdateErrorMessage(bulk ? error.reason : error)}`,
        { variant: "error" },
      );
    },
    retry: false,
  });

  const persist = useCallback(
    (nextTags) => {
      setTags(nextTags);
      saveTags({
        tags: nextTags,
        entity: selectedEntity,
        // Never another entity's state: then the entity's own revision.
        base:
          baseState.current.key === tagTargetKey(selectedEntity, selectedType)
            ? baseState.current.state
            : selectedEntity,
        type: selectedType,
        bulk: isBulk,
        savedItems: uniqueItems,
        itemCount: items.length,
        sessionId: session.current.id,
      });
    },
    [saveTags, selectedEntity, selectedType, items, isBulk, uniqueItems],
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
  // Called with acknowledged { result, entity, type } saves, even after close.
  onTagsSaved: PropTypes.func,
};

export default React.memo(AddTagsPopover);
