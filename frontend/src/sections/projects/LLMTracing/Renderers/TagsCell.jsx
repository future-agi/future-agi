import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Chip } from "@mui/material";
import { normalizeTag } from "src/components/traceDetail/tagUtils";
import TagChip from "src/components/traceDetail/TagChip";
import AddTagsPopover from "src/components/traceDetail/AddTagsPopover";

const MAX_VISIBLE = 2;

const TagsCell = ({
  value,
  traceId,
  spanId,
  entityType,
  canEditTags = true,
  onTagsUpdated,
}) => {
  const [anchorEl, setAnchorEl] = useState(null);
  const tags = Array.isArray(value) ? value : [];

  // Resolve which single entity this cell tags from the grid context (trace
  // grid vs span grid). A trace row can also carry its root span_id and the
  // popover tags spanId-first, so the explicit context prevents retagging the
  // root span; the spanId heuristic is only a fallback when no context is set.
  const isSpanRow = entityType ? entityType === "span" : Boolean(spanId);
  const targetTraceId = isSpanRow ? undefined : traceId;
  const targetSpanId = isSpanRow ? spanId : undefined;

  // Editable only when there is something to PATCH (a trace/span id) AND the
  // role may edit tags. Otherwise the cell stays a passive display.
  const editable = Boolean(targetTraceId || targetSpanId) && canEditTags;

  if (tags.length === 0 && !editable) return null;

  const visible = tags.slice(0, MAX_VISIBLE);
  const overflowCount = tags.length - MAX_VISIBLE;

  const handleClick = (event) => {
    event.stopPropagation();
    setAnchorEl(event.currentTarget);
  };

  const handleKeyDown = (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      event.stopPropagation();
      setAnchorEl(event.currentTarget);
    }
  };

  const handleClose = () => {
    setAnchorEl(null);
    // This grid is AG-Grid server-side: refreshing rebuilds the row and would
    // unmount this cell. The popover stays open across multiple adds, so we
    // refresh once on close (mirroring the bulk-tag flow) rather than per save,
    // which would snap the popover shut after the first tag.
    onTagsUpdated?.();
  };

  return (
    <>
      <Box
        onClick={editable ? handleClick : undefined}
        onKeyDown={editable ? handleKeyDown : undefined}
        onMouseDown={editable ? (e) => e.stopPropagation() : undefined}
        role={editable ? "button" : undefined}
        tabIndex={editable ? 0 : undefined}
        aria-label={
          editable ? (tags.length === 0 ? "Add tags" : "Edit tags") : undefined
        }
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.5,
          px: 1.5,
          overflow: "hidden",
          height: "100%",
          cursor: editable ? "pointer" : "default",
        }}
      >
        {visible.map((rawTag, idx) => {
          const tag = normalizeTag(rawTag);
          return (
            <TagChip
              key={`${tag.name}-${idx}`}
              name={tag.name}
              color={tag.color}
              size="small"
              readOnly
            />
          );
        })}
        {overflowCount > 0 && (
          <Chip
            label={`+${overflowCount}`}
            size="small"
            sx={{
              height: 20,
              fontSize: 11,
              "& .MuiChip-label": { px: 0.75 },
              bgcolor: "action.hover",
            }}
          />
        )}
        {tags.length === 0 && editable && (
          <Chip
            label="+ Tag"
            size="small"
            variant="outlined"
            sx={{
              height: 20,
              fontSize: 11,
              color: "text.secondary",
              borderStyle: "dashed",
              "& .MuiChip-label": { px: 0.75 },
            }}
          />
        )}
      </Box>
      {editable && (
        <AddTagsPopover
          open={Boolean(anchorEl)}
          anchorEl={anchorEl}
          onClose={handleClose}
          traceId={targetTraceId}
          spanId={targetSpanId}
          currentTags={value}
        />
      )}
    </>
  );
};

TagsCell.propTypes = {
  value: PropTypes.array,
  traceId: PropTypes.string,
  spanId: PropTypes.string,
  // "trace" | "span" — which entity this grid tags. Disambiguates rows that
  // carry both ids so the right endpoint is hit.
  entityType: PropTypes.oneOf(["trace", "span"]),
  // Whether the current role may edit tags; false renders the cell read-only.
  canEditTags: PropTypes.bool,
  // Called when the popover closes after editing, so the server-side grid can
  // refresh and show the saved tags.
  onTagsUpdated: PropTypes.func,
};

export default TagsCell;
