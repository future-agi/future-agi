import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Chip } from "@mui/material";
import { normalizeTag } from "src/components/traceDetail/tagUtils";
import TagChip from "src/components/traceDetail/TagChip";
import AddTagsPopover from "src/components/traceDetail/AddTagsPopover";

const MAX_VISIBLE = 2;

const TagsCell = ({ value, traceId, spanId, entityType, onTagsUpdated }) => {
  const [anchorEl, setAnchorEl] = useState(null);
  const tags = Array.isArray(value) ? value : [];

  // Resolve which single entity this cell tags from the grid context (trace
  // grid vs span grid), mirroring the bulk-tag action. A trace row can carry
  // its root span_id, and the popover's rule is "spanId wins, else traceId" —
  // so without explicit context we'd retag the root span instead of the trace.
  // Fall back to that heuristic only when no entityType is supplied.
  const isSpanRow = entityType ? entityType === "span" : Boolean(spanId);
  const targetTraceId = isSpanRow ? undefined : traceId;
  const targetSpanId = isSpanRow ? spanId : undefined;

  // Only rows that carry a trace/span id can mutate tags. Without one there is
  // nothing to PATCH, so the cell stays a passive (non-clickable) display.
  const editable = Boolean(targetTraceId || targetSpanId);

  if (tags.length === 0 && !editable) return null;

  const visible = tags.slice(0, MAX_VISIBLE);
  const overflowCount = tags.length - MAX_VISIBLE;

  const handleOpen = (event) => {
    event.stopPropagation();
    setAnchorEl(event.currentTarget);
  };

  return (
    <>
      <Box
        onClick={editable ? handleOpen : undefined}
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
          onClose={() => setAnchorEl(null)}
          traceId={targetTraceId}
          spanId={targetSpanId}
          currentTags={value}
          onSuccess={onTagsUpdated}
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
  // Called after a successful tag save so the server-side grid can refresh.
  onTagsUpdated: PropTypes.func,
};

export default React.memo(TagsCell);
